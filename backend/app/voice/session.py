"""One live conversation: audio in -> VAD -> ASR -> dialog -> TTS -> audio out.

Transport-agnostic: the browser demo (WebSocket) and the phone adapter (Asterisk AudioSocket)
both drive a CallSession through the same interface.
"""
from __future__ import annotations

import asyncio
import logging
import time
import wave
from datetime import timedelta
from typing import Protocol

import numpy as np

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import Call, CallEvent, Tenant
from ..security import normalize_phone
from ..services.notifications import notify
from . import runtime
from .dialog import Receptionist, Reply
from .engines import PIPELINE_SR, TurnDetector, resample, split_sentences
from .llm import LocalLLM
from .tools import ReceptionistTools, load_snapshot

log = logging.getLogger("voicerep.session")


class Transport(Protocol):
    async def send_event(self, event: dict) -> None: ...
    async def send_audio(self, audio: np.ndarray, sample_rate: int) -> None: ...
    async def stop_audio(self) -> None: ...


class CallSession:
    SILENCE_PROMPT_S = 9.0

    def __init__(self, tenant_id: str, transport: Transport, *, channel: str, caller_number: str | None = None,
                 external_id: str | None = None, input_sr: int = PIPELINE_SR, is_demo: bool = False):
        self.tenant_id = tenant_id
        self.transport = transport
        self.channel = channel
        self.caller_number = normalize_phone(caller_number) if caller_number else None
        self.external_id = external_id
        self.input_sr = input_sr
        self.is_demo = is_demo
        self.call_id: str | None = None
        self.dialog: Receptionist | None = None
        self.started = time.monotonic()
        self._speak_task: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()
        self._speaking_until = 0.0
        self._last_activity = time.monotonic()
        self._silence_prompts = 0
        self._caller_turns = 0
        self._closed = False
        self._failed: str | None = None
        self._watchdog: asyncio.Task | None = None
        self._recording: wave.Wave_write | None = None
        self._recording_name: str | None = None
        self.end_requested = asyncio.Event()
        self.next_action = "hangup"  # or "transfer"
        self.asr = None
        self.tts = None
        self.turns: TurnDetector | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        snap = await loop.run_in_executor(None, load_snapshot, self.tenant_id)
        self.call_id = await loop.run_in_executor(None, self._create_call)
        tools = ReceptionistTools(self.tenant_id, self.call_id)
        llm = await loop.run_in_executor(None, LocalLLM.maybe)
        self.dialog = Receptionist(snap, tools, caller_number=self.caller_number, channel=self.channel, llm=llm)
        self.asr = await loop.run_in_executor(None, runtime.get_asr)
        self.tts = await loop.run_in_executor(None, runtime.get_tts)
        vad, vad_kind = runtime.new_vad()
        self.turns = TurnDetector(vad)
        if snap.recording_enabled and self.channel == "phone":
            self._open_recording()
        await self.transport.send_event({
            "type": "ready", "call_id": self.call_id, "asr": self.asr is not None, "tts": self.tts is not None,
            "vad": vad_kind, "receptionist": snap.receptionist_name, "business": snap.name,
        })
        await self.say(self.dialog.greeting())
        self._watchdog = asyncio.create_task(self._silence_watchdog())

    def _create_call(self) -> str:
        with SessionLocal() as db:
            call = Call(tenant_id=self.tenant_id, channel=self.channel, caller_number=self.caller_number,
                        external_id=self.external_id, is_demo=self.is_demo, status="in_progress")
            db.add(call)
            db.commit()
            return call.id

    def _log(self, kind: str, text: str = "", data: dict | None = None) -> None:
        if not self.call_id:
            return
        try:
            with SessionLocal() as db:
                db.add(CallEvent(call_id=self.call_id, tenant_id=self.tenant_id, kind=kind, text=text[:2000], data=data))
                db.commit()
        except Exception:
            log.exception("failed to log call event")

    async def close(self, reason: str = "hangup") -> None:
        if self._closed:
            return
        self._closed = True
        if self._watchdog:
            self._watchdog.cancel()
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        if self._recording:
            self._recording.close()
        await asyncio.get_running_loop().run_in_executor(None, self._finalize, reason)

    def _finalize(self, reason: str) -> None:
        if not self.call_id:
            return
        d = self.dialog
        with SessionLocal() as db:
            call = db.get(Call, self.call_id)
            tenant = db.get(Tenant, self.tenant_id)
            if call is None or tenant is None:
                return
            call.ended_at = utcnow()
            call.duration_sec = int((call.ended_at - call.started_at).total_seconds())
            flags = d.outcome_flags if d else set()
            if self._failed:
                call.status, call.outcome = "failed", "failed"
                call.error_detail = self._failed[:500]
                call.follow_up_status = "needed"
            else:
                call.status = "completed"
                if "transferred" in flags:
                    call.outcome = "transferred"
                elif self._caller_turns == 0 and self.channel == "phone":
                    call.outcome = "missed"
                    call.follow_up_status = "needed"
                else:
                    call.outcome = "answered"
            if d:
                call.intent = d.detected_intent()
                call.summary = d.summary() if self._caller_turns else "Caller hung up before saying anything."
                if d.caller_name and not call.caller_name:
                    call.caller_name = d.caller_name
                if {"callback", "urgent"} & flags:
                    call.follow_up_status = "needed"
            if self._recording_name:
                call.recording_path = self._recording_name
            who = call.caller_name or (call.caller_number or "Unknown caller")
            if call.outcome == "missed" and not self.is_demo:
                notify(db, tenant, "call_missed", f"Missed call from {who}", "The caller hung up before speaking.",
                       link=f"/calls?id={call.id}", entity_type="call", entity_id=call.id)
            if call.status == "failed":
                notify(db, tenant, "system_failure", "A call failed",
                       f"The AI receptionist hit an error during a call from {who}. Please call them back.",
                       link=f"/calls?id={call.id}", entity_type="call", entity_id=call.id)
            db.commit()

    # ------------------------------------------------------------------ audio in
    @property
    def speaking(self) -> bool:
        return time.monotonic() < self._speaking_until or bool(self._speak_task and not self._speak_task.done())

    async def on_audio(self, audio: np.ndarray, sample_rate: int | None = None) -> None:
        if self._closed or self.turns is None:
            return
        a16 = resample(audio, sample_rate or self.input_sr, PIPELINE_SR)
        if self._recording:
            self._recording.writeframes((np.clip(a16, -1, 1) * 32767).astype("<i2").tobytes())
        # While we talk, require stronger evidence of speech (helps with echo).
        self.turns.start_t = 0.85 if self.speaking else 0.6
        for ev in self.turns.feed(a16):
            if ev.kind == "speech_start":
                self._last_activity = time.monotonic()
                if self.speaking:
                    await self.interrupt()
                await self.transport.send_event({"type": "listening", "speech": True})
            elif ev.kind == "turn_end" and ev.audio is not None:
                self._last_activity = time.monotonic()
                await self.transport.send_event({"type": "listening", "speech": False})
                asyncio.create_task(self._process_audio_turn(ev.audio))

    async def interrupt(self) -> None:
        """Caller barge-in: cancel synthesis and flush any queued audio immediately."""
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self._speaking_until = 0.0
        await self.transport.stop_audio()
        await self.transport.send_event({"type": "interrupted"})
        self._log("state", "caller interrupted")

    async def _process_audio_turn(self, audio16k: np.ndarray) -> None:
        if self.asr is None:
            await self.transport.send_event({"type": "error", "message": "Speech recognition model is not installed."})
            return
        if audio16k.size < PIPELINE_SR * 0.25:
            return
        loop = asyncio.get_running_loop()
        hints = ", ".join(s["name"] for s in self.dialog.snap.services[:8]) if self.dialog else ""
        try:
            tr = await loop.run_in_executor(None, self.asr.transcribe, audio16k, f"{self.dialog.snap.name}. {hints}.")
        except Exception as exc:
            log.exception("ASR failed")
            await self._fail(f"ASR error: {type(exc).__name__}")
            return
        if not tr.text or tr.confidence < 0.15:
            return  # noise; ignore rather than talk over the caller
        await self.on_text(tr.text, confidence=tr.confidence, asr_ms=tr.latency_ms)

    async def on_text(self, text: str, *, confidence: float = 1.0, asr_ms: int | None = None) -> None:
        if self._closed or self.dialog is None:
            return
        text = text.strip()[:500]
        if not text:
            return
        async with self._turn_lock:
            self._caller_turns += 1
            self._silence_prompts = 0
            self._last_activity = time.monotonic()
            if self.speaking:
                await self.interrupt()
            await self.transport.send_event({"type": "transcript", "role": "caller", "text": text,
                                             "confidence": round(confidence, 2), "asr_ms": asr_ms})
            self._log("caller", text, {"confidence": round(confidence, 2), "asr_ms": asr_ms})
            t0 = time.perf_counter()
            try:
                reply = await asyncio.get_running_loop().run_in_executor(None, self.dialog.handle, text, confidence)
            except Exception as exc:
                log.exception("dialog failed")
                await self._fail(f"Dialog error: {type(exc).__name__}")
                return
            dialog_ms = int((time.perf_counter() - t0) * 1000)
            self._log("latency", "", {"asr_ms": asr_ms, "dialog_ms": dialog_ms})
            await self.say(reply, dialog_ms=dialog_ms)

    # ------------------------------------------------------------------ audio out
    async def say(self, reply: Reply, dialog_ms: int | None = None) -> None:
        if not reply.text:
            return
        await self.transport.send_event({"type": "transcript", "role": "agent", "text": reply.text,
                                         "state": reply.state, "dialog_ms": dialog_ms})
        self._log("agent", reply.text, {"state": reply.state})
        self._speak_task = asyncio.create_task(self._speak(reply.text))
        try:
            await self._speak_task
        except asyncio.CancelledError:
            if self._closed:
                raise
            return
        if reply.end_call:
            if reply.transfer:
                self.next_action = "transfer"
            # Let the last words play out before hanging up.
            await asyncio.sleep(max(0.0, self._speaking_until - time.monotonic()) + 0.3)
            await self.transport.send_event({"type": "end", "transfer": reply.transfer})
            self.end_requested.set()

    async def _speak(self, text: str) -> None:
        if self.tts is None:
            self._speaking_until = time.monotonic() + min(6.0, len(text) / 15)
            return
        loop = asyncio.get_running_loop()
        voice = self.dialog.snap.voice_id if self.dialog else "af_heart"
        speed = self.dialog.snap.speed if self.dialog else 1.0
        first = True
        t0 = time.perf_counter()
        for sentence in split_sentences(text):
            audio = await loop.run_in_executor(None, self.tts.synthesize, sentence, voice, speed)
            if first:
                ms = int((time.perf_counter() - t0) * 1000)
                await self.transport.send_event({"type": "latency", "tts_first_ms": ms})
                first = False
            dur = audio.size / self.tts.sample_rate
            start = max(time.monotonic(), self._speaking_until)
            self._speaking_until = start + dur
            await self.transport.send_audio(audio, self.tts.sample_rate)

    async def _silence_watchdog(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(1.0)
                if self.speaking or self._turn_lock.locked() or (self.turns and self.turns.in_speech):
                    self._last_activity = time.monotonic()
                    continue
                if time.monotonic() - self._last_activity < self.SILENCE_PROMPT_S:
                    continue
                self._last_activity = time.monotonic()
                self._silence_prompts += 1
                if self._silence_prompts == 1:
                    await self.say(Reply("Are you still there?"))
                elif self._silence_prompts == 2:
                    await self.say(Reply("I'm sorry, I can't hear you. Please call back any time. Goodbye!", end_call=True))
                    if self.dialog:
                        self.dialog.ended = True
                    return
        except asyncio.CancelledError:
            pass

    async def _fail(self, detail: str) -> None:
        self._failed = detail
        self._log("error", detail)
        try:
            await self.say(Reply("I'm sorry, I'm having technical difficulties. A member of our team will call you back.",
                                 end_call=True))
        except Exception:
            self.end_requested.set()

    def _open_recording(self) -> None:
        folder = get_settings().data_dir / "recordings" / self.tenant_id
        folder.mkdir(parents=True, exist_ok=True)
        self._recording_name = f"{self.call_id}.wav"
        w = wave.open(str(folder / self._recording_name), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(PIPELINE_SR)
        self._recording = w


def purge_expired(now=None) -> int:
    """Data retention: delete transcripts and recordings older than each tenant's settings."""
    from sqlalchemy import delete, select

    now = now or utcnow()
    removed = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant)):
            cutoff = now - timedelta(days=t.transcript_retention_days)
            old_calls = db.scalars(select(Call.id).where(Call.tenant_id == t.id, Call.started_at < cutoff)).all()
            if old_calls:
                db.execute(delete(CallEvent).where(CallEvent.tenant_id == t.id, CallEvent.call_id.in_(old_calls)))
                removed += len(old_calls)
            rcut = now - timedelta(days=t.recording_retention_days)
            for c in db.scalars(select(Call).where(Call.tenant_id == t.id, Call.recording_path.is_not(None), Call.started_at < rcut)):
                (get_settings().data_dir / "recordings" / t.id / c.recording_path).unlink(missing_ok=True)
                c.recording_path = None
        db.commit()
    return removed

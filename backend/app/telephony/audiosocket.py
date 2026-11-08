"""Asterisk AudioSocket adapter (smallest useful live-phone integration).

Asterisk answers the SIP call, registers the call with our API (caller ID, dialled number and
the tenant's inbound token) and then streams 8 kHz signed-linear audio to this TCP server using
the AudioSocket() dialplan application. Frame format: 1 byte type, 2 bytes big-endian length, payload.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuidlib
from dataclasses import dataclass

import numpy as np

from ..db import SessionLocal, utcnow
from ..models import TelephonyIntegration, Tenant
from ..services.notifications import notify
from ..voice.engines import float_to_pcm16, pcm16_to_float, resample
from ..voice.session import CallSession

log = logging.getLogger("voicerep.audiosocket")

KIND_HANGUP, KIND_UUID, KIND_DTMF, KIND_AUDIO, KIND_ERROR = 0x00, 0x01, 0x03, 0x10, 0xFF
PHONE_SR = 8000
FRAME_BYTES = 320  # 20 ms of 8 kHz 16-bit mono
FRAME_SECONDS = 0.02
SILENT_FRAME = b"\0" * FRAME_BYTES
MAX_CATCH_UP_S = 0.1  # after a stall, resume normal pacing rather than bursting the backlog at the caller
DRAIN_TIMEOUT_S = 5.0  # the most we wait for the last words to play before hanging up
PRECALL_TTL = 60.0


@dataclass
class PendingCall:
    tenant_id: str
    integration_id: str
    caller: str | None
    did: str | None
    created: float


class CallRegistry:
    """Calls announced by the dialplan (precall) and their post-call action (hangup/transfer)."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingCall] = {}
        self.actions: dict[str, tuple[str, float]] = {}

    def announce(self, call_uuid: str, p: PendingCall) -> None:
        self._gc()
        self.pending[call_uuid.lower()] = p

    def claim(self, call_uuid: str) -> PendingCall | None:
        self._gc()
        return self.pending.pop(call_uuid.lower(), None)

    def set_action(self, call_uuid: str, action: str) -> None:
        self.actions[call_uuid.lower()] = (action, time.monotonic())

    def action(self, call_uuid: str) -> str:
        self._gc()
        return self.actions.pop(call_uuid.lower(), ("hangup", 0))[0]

    def _gc(self) -> None:
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if now - v.created < PRECALL_TTL}
        self.actions = {k: v for k, v in self.actions.items() if now - v[1] < 600}


registry = CallRegistry()


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(3)
    kind, length = header[0], int.from_bytes(header[1:3], "big")
    payload = await reader.readexactly(length) if length else b""
    return kind, payload


def frame(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


class AudioSocketTransport:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self._out = bytearray()
        self._closed = False
        self._pacer = asyncio.create_task(self._pace())

    async def send_event(self, event: dict) -> None:
        return None  # no side channel on a phone line

    async def send_audio(self, audio: np.ndarray, sample_rate: int) -> None:
        pcm = float_to_pcm16(resample(audio, sample_rate, PHONE_SR))
        # Only whole frames go out, so every clip is padded to end on a frame boundary.
        self._out.extend(pcm + b"\0" * (-len(pcm) % FRAME_BYTES))

    async def stop_audio(self) -> None:
        self._out.clear()

    async def _pace(self) -> None:
        """Send one frame every 20 ms: queued speech, otherwise silence.

        The constant stream matters: Asterisk ends the session after 2 seconds with no audio in either
        direction, which can happen on a quiet line when the carrier suppresses silence."""
        next_t = time.monotonic()
        try:
            while not self._closed:
                if self._out:
                    chunk = bytes(self._out[:FRAME_BYTES])
                    del self._out[:FRAME_BYTES]
                else:
                    chunk = SILENT_FRAME
                self.writer.write(frame(KIND_AUDIO, chunk))
                await self.writer.drain()
                now = time.monotonic()
                next_t = max(next_t + FRAME_SECONDS, now - MAX_CATCH_UP_S)
                await asyncio.sleep(max(0.0, next_t - now))
        except (ConnectionError, asyncio.CancelledError):
            pass

    @property
    def pending_audio(self) -> bool:
        """Speech still to be sent. False once the line is gone, because nothing will ever send it."""
        return bool(self._out) and not self._pacer.done()

    async def close(self) -> None:
        self._closed = True
        self._pacer.cancel()


class AudioSocketAdapter:
    provider = "asterisk_audiosocket"

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self._server: asyncio.base_events.Server | None = None
        self.error = ""
        self.active_calls = 0
        self.total_calls = 0

    async def start(self) -> None:
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
            self.error = ""
            log.info("AudioSocket listening on %s:%s", self.host, self.port)
        except OSError as exc:
            self.error = f"Could not listen on port {self.port}: {exc.strerror or exc}"
            log.error(self.error)
            _mark_offline(self.error)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def status(self) -> dict:
        return {"listening": self._server is not None, "host": self.host, "port": self.port, "error": self.error,
                "active_calls": self.active_calls, "total_calls": self.total_calls}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        call: CallSession | None = None
        transport: AudioSocketTransport | None = None
        call_uuid = ""
        rd: asyncio.Task | None = None
        end_wait: asyncio.Task | None = None
        try:
            kind, payload = await asyncio.wait_for(read_frame(reader), timeout=5)
            if kind != KIND_UUID or len(payload) != 16:
                writer.write(frame(KIND_HANGUP))
                return
            call_uuid = str(uuidlib.UUID(bytes=payload))
            pending = registry.claim(call_uuid)
            if pending is None:
                log.warning("AudioSocket call %s was not announced; rejecting", call_uuid[:8])
                writer.write(frame(KIND_HANGUP))
                return
            self.active_calls += 1
            self.total_calls += 1
            _mark_call(pending.integration_id)
            transport = AudioSocketTransport(writer)
            call = CallSession(pending.tenant_id, transport, channel="phone", caller_number=pending.caller,
                               external_id=call_uuid, input_sr=PHONE_SR)
            await call.start()
            end_wait = asyncio.create_task(call.end_requested.wait())
            while True:
                rd = asyncio.create_task(read_frame(reader))
                done, _ = await asyncio.wait({rd, end_wait}, return_when=asyncio.FIRST_COMPLETED)
                if end_wait in done:
                    rd.cancel()
                    deadline = time.monotonic() + DRAIN_TIMEOUT_S
                    while transport.pending_audio and time.monotonic() < deadline:
                        await asyncio.sleep(0.05)
                    break
                kind, payload = rd.result()
                if kind == KIND_AUDIO:
                    await call.on_audio(pcm16_to_float(payload), PHONE_SR)
                elif kind in (KIND_HANGUP, KIND_ERROR):
                    break
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            log.exception("AudioSocket call error")
            if call is not None:
                call._failed = f"Telephony error: {type(exc).__name__}"
        finally:
            # Left running, these get garbage-collected and asyncio logs "Task was destroyed but it is pending!".
            for task in (rd, end_wait):
                if task is not None:
                    task.cancel()
            if call is not None:
                if call_uuid and call.next_action == "transfer" and call.dialog and call.dialog.snap.transfer_number:
                    registry.set_action(call_uuid, f"transfer:{call.dialog.snap.transfer_number}")
                await call.close()
                self.active_calls = max(0, self.active_calls - 1)
            if transport is not None:
                await transport.close()
            try:
                writer.write(frame(KIND_HANGUP))
                await writer.drain()
                writer.close()
            except Exception:
                pass


def _mark_call(integration_id: str) -> None:
    with SessionLocal() as db:
        integ = db.get(TelephonyIntegration, integration_id)
        if integ is None:
            return
        integ.last_call_at = utcnow()
        if integ.status != "connected":
            integ.status = "connected"  # validated by a real inbound call
            integ.last_validated_at = utcnow()
            integ.last_error = ""
        integ.checklist = {**(integ.checklist or {}), "test_call_received": True}
        db.commit()


def _mark_offline(error: str) -> None:
    try:
        with SessionLocal() as db:
            for integ in db.query(TelephonyIntegration).filter(TelephonyIntegration.status == "connected"):
                integ.status = "error"
                integ.last_error = error[:300]
                tenant = db.get(Tenant, integ.tenant_id)
                if tenant:
                    notify(db, tenant, "phone_offline", "Phone line offline",
                           "The AI receptionist cannot receive phone calls right now. " + error[:200], link="/integrations")
            db.commit()
    except Exception:
        log.exception("could not mark telephony offline")

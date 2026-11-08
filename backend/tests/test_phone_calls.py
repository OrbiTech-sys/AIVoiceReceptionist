"""How a real phone call starts, sounds and ends over AudioSocket, without needing the speech models."""
from __future__ import annotations

import asyncio
import threading
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_voice import _free_port, _tenant_with_integration

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import Call, CallEvent, Notification, ReceptionistSettings, TelephonyIntegration
from app.telephony.audiosocket import (
    FRAME_BYTES,
    KIND_AUDIO,
    KIND_HANGUP,
    KIND_UUID,
    PHONE_SR,
    AudioSocketAdapter,
    AudioSocketTransport,
    PendingCall,
    frame,
    read_frame,
    registry,
)
from app.voice import engines, runtime
from app.voice import session as voice_session
from app.voice.dialog import Receptionist
from app.voice.session import CallSession

SILENT_FRAME = b"\0" * FRAME_BYTES
CALLER = "+14155550999"
DID = "+14155550100"
CALL_UUID = "12345678-1234-1234-1234-123456789abc"


class FakeTTS:
    """Speech in clips that are deliberately not a whole number of phone frames."""

    sample_rate = 24000

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    def synthesize(self, text, voice, speed=1.0):
        time.sleep(self.delay)
        return np.full(2411, 0.1, dtype=np.float32)


class FakeASR:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, audio16k, hints=""):
        return engines.Transcript(self.text, 0.9, 5)


class QuietTransport:
    async def send_event(self, event):
        pass

    async def send_audio(self, audio, sample_rate):
        pass

    async def stop_audio(self):
        pass


async def _connect(adapter_port: int, tenant_id: str, integ_id: str):
    cid = uuidlib.uuid4()
    registry.announce(str(cid), PendingCall(tenant_id, integ_id, CALLER, DID, time.monotonic()))
    rd, wr = await asyncio.open_connection("127.0.0.1", adapter_port)
    wr.write(frame(KIND_UUID, cid.bytes))
    await wr.drain()
    return cid, rd, wr


async def _audio_until_hangup(rd, timeout: float = 10.0) -> list[bytes]:
    """Everything Asterisk would receive up to the receptionist's hang-up frame."""
    payloads: list[bytes] = []

    async def loop():
        while True:
            kind, payload = await read_frame(rd)
            if kind == KIND_HANGUP:
                return
            assert kind == KIND_AUDIO
            payloads.append(payload)

    await asyncio.wait_for(loop(), timeout)
    return payloads


# ------------------------------------------------------------------ how a call ends
def test_call_ended_by_the_receptionist_hangs_up_and_is_recorded(owner, monkeypatch):
    tenant_id, integ_id = _tenant_with_integration(owner)
    monkeypatch.setattr(runtime, "get_tts", lambda: FakeTTS())
    monkeypatch.setattr(CallSession, "SILENCE_PROMPT_S", 0.1)  # nobody speaks, so it says goodbye quickly

    async def scenario():
        port = _free_port()
        adapter = AudioSocketAdapter("127.0.0.1", port)
        await adapter.start()
        _, rd, wr = await _connect(port, tenant_id, integ_id)
        payloads = await _audio_until_hangup(rd)
        wr.close()
        active = adapter.status()["active_calls"]
        await adapter.stop()
        return payloads, active

    payloads, active = asyncio.run(scenario())
    assert all(len(p) == FRAME_BYTES for p in payloads)
    assert any(p != SILENT_FRAME for p in payloads)  # the caller heard speech
    assert active == 0
    with SessionLocal() as db:
        call = db.query(Call).one()
        assert call.status == "completed" and call.outcome == "missed" and call.ended_at is not None


def test_caller_hanging_up_leaves_nothing_waiting(owner, monkeypatch):
    tenant_id, integ_id = _tenant_with_integration(owner)
    monkeypatch.setattr(runtime, "get_tts", lambda: FakeTTS())

    async def scenario():
        port = _free_port()
        adapter = AudioSocketAdapter("127.0.0.1", port)
        await adapter.start()
        _, rd, wr = await _connect(port, tenant_id, integ_id)
        wr.write(frame(KIND_HANGUP))
        await wr.drain()
        await _audio_until_hangup(rd)  # the adapter sends its own hang-up frame after cleaning up
        left_waiting = [t for t in asyncio.all_tasks() if t.get_coro().__qualname__ == "Event.wait"]
        wr.close()
        await adapter.stop()
        return left_waiting

    assert asyncio.run(scenario()) == []


def test_transfer_request_reaches_the_dialplan(owner, monkeypatch):
    tenant_id, integ_id = _tenant_with_integration(owner)
    with SessionLocal() as db:
        settings = db.get(ReceptionistSettings, tenant_id) or ReceptionistSettings(tenant_id=tenant_id)
        settings.transfer_number = "+14155550111"
        db.add(settings)
        db.commit()
    monkeypatch.setattr(runtime, "get_tts", lambda: FakeTTS())
    monkeypatch.setattr(runtime, "get_asr", lambda: FakeASR("I'd like to talk to a real person"))
    monkeypatch.setattr(runtime, "new_vad", lambda: (engines.EnergyVAD(), "energy"))
    monkeypatch.setattr(Receptionist, "is_open_now", lambda self: True)
    t = np.arange(PHONE_SR // 50) / PHONE_SR
    speech = engines.float_to_pcm16(0.3 * np.sin(2 * np.pi * 300 * t))

    async def scenario():
        port = _free_port()
        adapter = AudioSocketAdapter("127.0.0.1", port)
        await adapter.start()
        cid, rd, wr = await _connect(port, tenant_id, integ_id)
        for payload in [speech] * 30 + [SILENT_FRAME] * 60:  # a spoken turn, then silence ends the turn
            wr.write(frame(KIND_AUDIO, payload))
        await wr.drain()
        await _audio_until_hangup(rd)
        wr.close()
        await adapter.stop()
        return cid

    cid = asyncio.run(scenario())
    assert registry.action(str(cid)) == "transfer:+14155550111"
    with SessionLocal() as db:
        assert db.query(Call).one().outcome == "transferred"


def test_line_gets_whole_frames_and_silence_when_idle():
    class Writer:
        def __init__(self):
            self.frames: list[bytes] = []

        def write(self, data):
            self.frames.append(data)

        async def drain(self):
            pass

    async def scenario():
        writer = Writer()
        transport = AudioSocketTransport(writer)
        await transport.send_audio(np.full(2411, 0.1, dtype=np.float32), 24000)
        assert transport.pending_audio
        await asyncio.sleep(0.4)
        assert not transport.pending_audio
        sent = len(writer.frames)
        await asyncio.sleep(0.2)
        assert len(writer.frames) > sent  # keeps the line active between sentences
        await transport.close()
        return writer.frames

    frames = asyncio.run(scenario())
    assert all(f[0] == KIND_AUDIO and int.from_bytes(f[1:3], "big") == FRAME_BYTES == len(f) - 3 for f in frames)
    assert frames[-1][3:] == SILENT_FRAME


def test_transport_stops_waiting_once_the_line_is_gone():
    class BrokenWriter:
        def write(self, data):
            pass

        async def drain(self):
            raise ConnectionResetError

    async def scenario():
        transport = AudioSocketTransport(BrokenWriter())
        await transport.send_audio(np.full(24000, 0.1, dtype=np.float32), 24000)
        await asyncio.sleep(0.1)
        pending = transport.pending_audio
        await transport.close()
        return pending

    assert asyncio.run(scenario()) is False


def test_final_save_survives_being_cancelled(owner):
    tenant_id, _ = _tenant_with_integration(owner)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        call = CallSession(tenant_id, QuietTransport(), channel="phone", input_sr=PHONE_SR)
        await call.start()
        # Keep the only worker busy, so the save is still queued when the cancel arrives.
        worker_free = threading.Event()
        loop.run_in_executor(None, worker_free.wait)
        closing = asyncio.create_task(call.close())
        await asyncio.sleep(0.01)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        worker_free.set()

    asyncio.run(scenario())  # shutting down the event loop waits for the worker to finish
    with SessionLocal() as db:
        call = db.query(Call).one()
        assert call.status == "completed" and call.ended_at is not None


# ------------------------------------------------------------------ how a call starts
def test_start_does_not_wait_for_the_greeting(owner, monkeypatch):
    tenant_id, _ = _tenant_with_integration(owner)
    monkeypatch.setattr(runtime, "get_tts", lambda: FakeTTS(delay=1.0))

    async def scenario():
        call = CallSession(tenant_id, QuietTransport(), channel="phone", input_sr=PHONE_SR)
        started = time.monotonic()
        await call.start()
        elapsed = time.monotonic() - started
        await call.close()
        return elapsed

    assert asyncio.run(scenario()) < 1.0  # returned before even the first clip could have been synthesized


def test_greeting_failure_ends_the_call_instead_of_leaving_silence(owner, monkeypatch):
    tenant_id, _ = _tenant_with_integration(owner)

    class BrokenTTS(FakeTTS):
        def synthesize(self, text, voice, speed=1.0):
            raise RuntimeError("voice engine crashed")

    monkeypatch.setattr(runtime, "get_tts", lambda: BrokenTTS())

    async def scenario():
        call = CallSession(tenant_id, QuietTransport(), channel="phone", input_sr=PHONE_SR)
        await call.start()
        await asyncio.wait_for(call.end_requested.wait(), 5)
        failed = call._failed
        await call.close()
        return failed

    assert asyncio.run(scenario()) == "TTS error: RuntimeError"
    with SessionLocal() as db:
        assert db.query(Call).one().status == "failed"


# ------------------------------------------------------------------ how a call sounds
def _tone(hz: float, sample_rate: int, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return np.sin(2 * np.pi * hz * t).astype(np.float32)


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a**2)))


def _snr_db(audio: np.ndarray, hz: float, sample_rate: int) -> float:
    """How cleanly the audio matches a pure tone, whatever its delay or level."""
    t = np.arange(audio.size) / sample_rate
    basis = np.column_stack([np.sin(2 * np.pi * hz * t), np.cos(2 * np.pi * hz * t)])
    coef, *_ = np.linalg.lstsq(basis, audio, rcond=None)
    residual = audio - basis @ coef
    return float(10 * np.log10(np.mean(audio**2) / np.mean(residual**2)))


def test_resample_removes_what_a_phone_line_cannot_carry():
    assert _rms(engines.resample(_tone(6000, 24000), 24000, PHONE_SR)) < 0.01  # above 4 kHz: must disappear
    assert _rms(engines.resample(_tone(1000, 24000), 24000, PHONE_SR)) == pytest.approx(0.707, abs=0.02)


@pytest.mark.parametrize("hz", [300, 1000, 3000])
def test_stream_resampler_stays_clean_across_small_frames(hz):
    x = _tone(hz, PHONE_SR, seconds=2.0)
    resampler = engines.StreamResampler(PHONE_SR, engines.PIPELINE_SR)
    frames = [resampler.process(x[i:i + PHONE_SR // 50]) for i in range(0, x.size, PHONE_SR // 50)]
    out = np.concatenate(frames)
    assert abs(out.size - x.size * 2) < engines.PIPELINE_SR * 0.05  # only the filter's short delay is missing
    assert _snr_db(out[2000:-2000], hz, engines.PIPELINE_SR) > 40


def test_stream_resampler_passes_matching_rate_through():
    x = _tone(1000, engines.PIPELINE_SR, seconds=0.1)
    out = engines.StreamResampler(engines.PIPELINE_SR, engines.PIPELINE_SR).process(x)
    assert np.array_equal(out, x)


# ------------------------------------------------------------------ restarts
def test_restart_closes_calls_left_in_progress(owner):
    tenant_id, _ = _tenant_with_integration(owner)
    started = utcnow() - timedelta(minutes=5)
    with SessionLocal() as db:
        phone = Call(tenant_id=tenant_id, channel="phone", caller_number=CALLER, status="in_progress", started_at=started)
        demo = Call(tenant_id=tenant_id, channel="browser_demo", is_demo=True, status="in_progress", started_at=started)
        done = Call(tenant_id=tenant_id, channel="phone", status="completed", outcome="answered", started_at=started)
        db.add_all([phone, demo, done])
        db.flush()
        db.add(CallEvent(call_id=phone.id, tenant_id=tenant_id, kind="caller", text="hello", ts=started + timedelta(seconds=40)))
        db.commit()
        phone_id, demo_id, done_id = phone.id, demo.id, done.id

    assert voice_session.recover_interrupted_calls() == 2
    assert voice_session.recover_interrupted_calls() == 0  # nothing left to close

    with SessionLocal() as db:
        phone, demo, done = db.get(Call, phone_id), db.get(Call, demo_id), db.get(Call, done_id)
        assert phone.status == "failed" and phone.outcome == "failed" and phone.follow_up_status == "needed"
        assert phone.duration_sec == 40  # ends at its last known activity, not at the restart
        assert demo.status == "failed" and demo.follow_up_status == "none" and demo.ended_at == demo.started_at
        assert done.status == "completed" and done.outcome == "answered"
        alerts = db.query(Notification).filter(Notification.kind == "system_failure").all()
        assert alerts and {n.entity_id for n in alerts} == {phone_id}  # no alert for a browser test call


def test_server_start_closes_calls_left_in_progress(owner, monkeypatch):
    tenant_id, _ = _tenant_with_integration(owner)
    with SessionLocal() as db:
        call = Call(tenant_id=tenant_id, channel="phone", status="in_progress")
        db.add(call)
        db.commit()
        call_id = call.id
    monkeypatch.setattr(get_settings(), "run_workers", True)
    with TestClient(app):  # entering runs the app's start-up
        pass
    with SessionLocal() as db:
        assert db.get(Call, call_id).status == "failed"


# ------------------------------------------------------------------ deciding whether to take a call
def _precall(owner, token: str, did: str = "%2B14155550100"):
    return owner.c.get(f"/api/telephony/asterisk/precall?uuid={CALL_UUID}&did={did}&caller=%2B14155550999&token={token}")


@pytest.fixture
def inbound_token(owner, monkeypatch) -> str:
    monkeypatch.setattr(runtime, "voice_ready", lambda: True)
    registry.pending.clear()
    r = owner.put("/api/telephony", json={"did_number": DID, "sip_host": "sip.example.net", "sip_username": "acct", "sip_password": "pw"})
    assert r.status_code == 200, r.text
    return r.json()["inbound_token"]


def test_precall_accepts_a_call_when_ready(owner, inbound_token):
    r = _precall(owner, inbound_token)
    assert r.status_code == 200 and r.text == "ok"
    assert registry.claim(CALL_UUID) is not None


def test_precall_refuses_when_the_receptionist_is_turned_off(owner, inbound_token):
    with SessionLocal() as db:
        tenant_id = db.query(TelephonyIntegration).one().tenant_id
        settings = db.get(ReceptionistSettings, tenant_id) or ReceptionistSettings(tenant_id=tenant_id)
        settings.enabled = False
        db.add(settings)
        db.commit()
    r = _precall(owner, inbound_token)
    assert r.status_code == 200 and r.text == "off"
    assert registry.claim(CALL_UUID) is None


def test_precall_refuses_when_speech_models_are_not_ready(owner, inbound_token, monkeypatch):
    monkeypatch.setattr(runtime, "voice_ready", lambda: False)
    r = _precall(owner, inbound_token)
    assert r.status_code == 200 and r.text == "unavailable"
    assert registry.claim(CALL_UUID) is None


def test_voice_ready_is_false_without_models():
    assert runtime.voice_ready() is False


@pytest.mark.parametrize("sent", ["%2B14155550100", "14155550100", "4155550100"])
def test_precall_understands_how_carriers_send_a_us_number(owner, inbound_token, sent):
    assert _precall(owner, inbound_token, did=sent).text == "ok"


@pytest.mark.parametrize("sent", ["%2B61291234567", "61291234567"])
def test_precall_understands_how_carriers_send_an_international_number(owner, monkeypatch, sent):
    monkeypatch.setattr(runtime, "voice_ready", lambda: True)
    r = owner.put("/api/telephony", json={"did_number": "+61 2 9123 4567", "sip_host": "sip.example.net",
                                          "sip_username": "acct", "sip_password": "pw"})
    assert r.status_code == 200, r.text
    assert _precall(owner, r.json()["inbound_token"], did=sent).text == "ok"


def test_precall_uses_the_token_when_a_number_could_mean_two_businesses(owner, other_owner, inbound_token):
    # "4155550100" is both +1 415 555 0100 and +41 55 550 01 00.
    r = other_owner.put("/api/telephony", json={"did_number": "+41 55 550 01 00", "sip_host": "sip.example.net",
                                                "sip_username": "acct", "sip_password": "pw"})
    assert r.status_code == 200, r.text
    assert _precall(owner, inbound_token, did="4155550100").text == "ok"
    with SessionLocal() as db:
        ours = db.query(TelephonyIntegration).filter(TelephonyIntegration.did_number == DID).one()
    assert registry.claim(CALL_UUID).tenant_id == ours.tenant_id


def test_precall_rejects_an_unknown_number_or_wrong_token(owner, inbound_token):
    assert _precall(owner, inbound_token, did="%2B14155550199").status_code == 401
    assert _precall(owner, "wrong-token").status_code == 401


def test_generated_config_points_at_this_server(owner, inbound_token):
    cfg = owner.get("/api/telephony/asterisk-config").text
    assert "voicerep-api" not in cfg
    assert "http://127.0.0.1:8000/api/telephony/asterisk/precall" in cfg
    assert "127.0.0.1:9092" in cfg

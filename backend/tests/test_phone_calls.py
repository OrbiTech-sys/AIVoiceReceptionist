"""How a real phone call starts, sounds and ends over AudioSocket, without needing the speech models."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import threading
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_voice import _free_port, _tenant_with_integration

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import Call, CallEvent, Notification, ReceptionistSettings, TelephonyIntegration, User
from app.telephony import health
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
TOKEN = "test-asterisk-token"


def _precall(owner, did: str = DID, token: str = TOKEN, caller: str = CALLER):
    return owner.c.post("/api/telephony/asterisk/precall", data={"uuid": CALL_UUID, "did": did, "caller": caller, "token": token})


def _set_receptionist(tenant_id: str, **values) -> None:
    with SessionLocal() as db:
        settings = db.get(ReceptionistSettings, tenant_id) or ReceptionistSettings(tenant_id=tenant_id)
        for key, value in values.items():
            setattr(settings, key, value)
        db.add(settings)
        db.commit()


@pytest.fixture
def phone_line(owner, monkeypatch) -> str:
    """A business with a number, and a receptionist that's ready. Returns the business's id."""
    monkeypatch.setattr(runtime, "voice_ready", lambda: True)
    registry.pending.clear()
    tenant_id, _ = _tenant_with_integration(owner)
    return tenant_id


def test_precall_accepts_a_call_when_ready(owner, phone_line):
    r = _precall(owner)
    assert r.status_code == 200 and r.text == "ok"
    pending = registry.claim(CALL_UUID)
    assert pending is not None and pending.tenant_id == phone_line


def test_dialplan_callbacks_need_the_token_and_post(owner, phone_line):
    assert _precall(owner, token="wrong-token").status_code == 401
    assert owner.c.post("/api/telephony/asterisk/next-action", data={"uuid": CALL_UUID, "token": "wrong"}).status_code == 401
    assert owner.c.post("/api/telephony/asterisk/heartbeat", data={"token": "wrong"}).status_code == 401
    assert owner.c.get(f"/api/telephony/asterisk/precall?uuid={CALL_UUID}&did={DID}&token={TOKEN}").status_code == 405


def test_dialplan_callbacks_are_closed_until_a_token_is_configured(owner, phone_line, monkeypatch):
    monkeypatch.setattr(get_settings(), "asterisk_token", "")
    assert _precall(owner, token="").status_code in (401, 422)
    assert _precall(owner, token="anything").status_code == 401


def test_next_action_defaults_to_hanging_up(owner, phone_line):
    assert owner.c.post("/api/telephony/asterisk/next-action", data={"uuid": CALL_UUID, "token": TOKEN}).text == "hangup"


def test_precall_refuses_when_the_receptionist_is_turned_off(owner, phone_line):
    _set_receptionist(phone_line, enabled=False)
    r = _precall(owner)
    assert r.status_code == 200 and r.text == "off"
    assert registry.claim(CALL_UUID) is None


def test_precall_refuses_when_speech_models_are_not_ready(owner, phone_line, monkeypatch):
    monkeypatch.setattr(runtime, "voice_ready", lambda: False)
    assert _precall(owner).text == "unavailable"
    assert registry.claim(CALL_UUID) is None


def test_precall_refuses_above_the_call_limit(owner, phone_line, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 1)
    assert _precall(owner).text == "ok"  # now one call is on its way
    assert _precall(owner, caller="+14155550998").text == "busy"
    registry.pending.clear()
    assert _precall(owner).text == "ok"


def test_refused_call_is_recorded_and_the_owner_is_told(owner, phone_line, monkeypatch):
    monkeypatch.setattr(runtime, "voice_ready", lambda: False)
    _precall(owner)
    with SessionLocal() as db:
        call = db.query(Call).one()
        assert call.status == "completed" and call.outcome == "missed" and call.follow_up_status == "needed"
        assert call.caller_number == CALLER and "speech models" in call.summary
        alert = db.query(Notification).filter(Notification.kind == "call_missed").one()
        assert alert.entity_id == call.id


def test_refused_call_goes_to_the_transfer_number_when_there_is_one(owner, phone_line):
    _set_receptionist(phone_line, enabled=False, transfer_number="+14155550111")
    assert _precall(owner).text == "forward:+14155550111"
    with SessionLocal() as db:
        assert db.query(Call).one().outcome == "transferred"
        assert db.query(Notification).filter(Notification.kind == "call_missed").count() == 0  # someone will answer


def test_voice_ready_is_false_without_models():
    assert runtime.voice_ready() is False


@pytest.mark.parametrize("sent", ["+14155550100", "14155550100", "4155550100"])
def test_precall_understands_how_carriers_send_a_us_number(owner, phone_line, sent):
    assert _precall(owner, did=sent).text == "ok"


def test_precall_understands_a_number_without_its_plus(owner, phone_line):
    with SessionLocal() as db:
        db.query(TelephonyIntegration).one().did_number = "+61291234567"
        db.commit()
    assert _precall(owner, did="61291234567").text == "ok"


def test_call_to_a_number_no_business_has_is_refused(owner, phone_line):
    assert _precall(owner, did="+14155550199").text == "unknown"
    with SessionLocal() as db:
        assert db.query(Call).count() == 0


def test_unanswered_transfer_asks_the_owner_to_call_back(owner, phone_line):
    with SessionLocal() as db:
        db.add(Call(tenant_id=phone_line, channel="phone", external_id=CALL_UUID, status="completed", outcome="transferred", caller_number=CALLER))
        db.commit()
    r = owner.c.post("/api/telephony/asterisk/transfer-failed", data={"uuid": CALL_UUID, "token": TOKEN})
    assert r.text == "ok"
    with SessionLocal() as db:
        call = db.query(Call).one()
        assert call.outcome == "answered" and call.follow_up_status == "needed"
        assert db.query(Notification).filter(Notification.kind == "callback_requested", Notification.entity_id == call.id).count() >= 1


# ------------------------------------------------------------------ assigning numbers
def _make_platform_admin(email: str) -> None:
    with SessionLocal() as db:
        db.query(User).filter(User.email == email).one().is_platform_admin = True
        db.commit()


def test_platform_admin_assigns_a_number_and_the_owner_sees_it(owner, other_owner):
    _make_platform_admin("owner@example.com")
    assert other_owner.get("/api/telephony").json() == {"configured": False, "status": "not_configured"}
    tenants = owner.get("/api/admin/tenants").json()
    salon = next(t for t in tenants if t["name"] == "Other Salon")
    assert salon["phone_number"] is None and salon["phone_status"] == "not_configured"

    r = owner.put(f"/api/telephony/admin/tenants/{salon['id']}", json={"did_number": "+1 415 555 0100"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_validation"  # never "connected" before a real call

    seen = other_owner.get("/api/telephony").json()
    assert seen["did_number"] == DID and seen["configured"] is True
    salon = next(t for t in owner.get("/api/admin/tenants").json() if t["name"] == "Other Salon")
    assert salon["phone_number"] == DID

    assert owner.delete(f"/api/telephony/admin/tenants/{salon['id']}").status_code == 200
    assert other_owner.get("/api/telephony").json()["configured"] is False


def test_changing_a_number_needs_a_new_test_call(owner):
    _make_platform_admin("owner@example.com")
    tenant_id, integ_id = _tenant_with_integration(owner)
    with SessionLocal() as db:
        db.get(TelephonyIntegration, integ_id).status = "connected"
        db.commit()
    url = f"/api/telephony/admin/tenants/{tenant_id}"
    assert owner.put(url, json={"did_number": DID}).json()["status"] == "connected"  # same number: nothing changes
    assert owner.put(url, json={"did_number": "+14155550101"}).json()["status"] == "pending_validation"


def test_one_number_cannot_belong_to_two_businesses(owner, other_owner):
    _make_platform_admin("owner@example.com")
    _tenant_with_integration(owner)
    salon = next(t for t in owner.get("/api/admin/tenants").json() if t["name"] == "Other Salon")
    r = owner.put(f"/api/telephony/admin/tenants/{salon['id']}", json={"did_number": DID})
    assert r.status_code == 409


def test_invalid_number_is_rejected(owner):
    _make_platform_admin("owner@example.com")
    tenant_id = owner.get("/api/admin/tenants").json()[0]["id"]
    assert owner.put(f"/api/telephony/admin/tenants/{tenant_id}", json={"did_number": "12345"}).status_code == 400


def test_only_a_platform_admin_can_assign_numbers(owner):
    tenant_id, _ = _tenant_with_integration(owner)
    assert owner.put(f"/api/telephony/admin/tenants/{tenant_id}", json={"did_number": "+14155550101"}).status_code == 403
    assert owner.delete(f"/api/telephony/admin/tenants/{tenant_id}").status_code == 403


# ------------------------------------------------------------------ noticing when Asterisk stops
def test_owner_is_alerted_once_when_asterisk_goes_quiet_and_line_recovers(owner):
    tenant_id, integ_id = _tenant_with_integration(owner)
    now = utcnow()
    assert health.check_heartbeat(now) is False  # no heartbeat yet: Asterisk may simply not be set up
    with SessionLocal() as db:
        db.get(TelephonyIntegration, integ_id).status = "connected"
        db.commit()
    assert owner.c.post("/api/telephony/asterisk/heartbeat", data={"token": TOKEN}).text == "ok"
    assert health.check_heartbeat(utcnow() + timedelta(minutes=1)) is False

    later = utcnow() + timedelta(minutes=4)
    assert health.check_heartbeat(later) is True
    assert health.check_heartbeat(later) is True
    with SessionLocal() as db:
        assert db.get(TelephonyIntegration, integ_id).status == "error"
        assert db.query(Notification).filter(Notification.kind == "phone_offline").count() == 1

    owner.c.post("/api/telephony/asterisk/heartbeat", data={"token": TOKEN})
    with SessionLocal() as db:
        line = db.get(TelephonyIntegration, integ_id)
        assert line.status == "connected" and line.last_error == ""


# ------------------------------------------------------------------ restarts during a call
def test_restart_waits_for_a_call_in_progress_to_finish():
    async def scenario():
        port = _free_port()
        adapter = AudioSocketAdapter("127.0.0.1", port)
        await adapter.start()
        adapter.active_calls = 1

        async def call_ends():
            await asyncio.sleep(0.5)
            adapter.active_calls = 0

        ending = asyncio.create_task(call_ends())
        started = time.monotonic()
        await adapter.stop(drain_seconds=5)
        await ending
        return time.monotonic() - started

    assert 0.4 < asyncio.run(scenario()) < 3


def test_restart_gives_up_waiting_after_the_limit():
    async def scenario():
        adapter = AudioSocketAdapter("127.0.0.1", _free_port())
        await adapter.start()
        adapter.active_calls = 1
        started = time.monotonic()
        await adapter.stop(drain_seconds=0.3)
        return time.monotonic() - started, adapter.status()["listening"]

    elapsed, listening = asyncio.run(scenario())
    assert elapsed < 2 and listening is False


# ------------------------------------------------------------------ voice failures during a call
def test_voice_crash_mid_call_ends_the_call_instead_of_leaving_silence(owner, monkeypatch):
    tenant_id, _ = _tenant_with_integration(owner)

    class CrashesAfterGreeting(FakeTTS):
        def synthesize(self, text, voice, speed=1.0):
            if "hours" in text.lower() or "open" in text.lower():  # the answer to the caller's question
                raise RuntimeError("voice engine crashed")
            return super().synthesize(text, voice, speed)

    monkeypatch.setattr(runtime, "get_tts", lambda: CrashesAfterGreeting())

    async def scenario():
        call = CallSession(tenant_id, QuietTransport(), channel="phone", input_sr=PHONE_SR)
        await call.start()
        await call.on_text("What are your hours?")
        await asyncio.wait_for(call.end_requested.wait(), 5)
        failed = call._failed
        await call.close()
        return failed

    assert asyncio.run(scenario()) == "TTS error: RuntimeError"
    with SessionLocal() as db:
        call = db.query(Call).one()
        assert call.status == "failed" and call.follow_up_status == "needed"


# ------------------------------------------------------------------ Asterisk's config files
ASTERISK_DIR = Path(__file__).resolve().parents[2] / "deploy" / "asterisk"
RENDER_ENV = {"VR_ASTERISK_TOKEN": "secret-token", "ASTERISK_PUBLIC_IP": "203.0.113.7", "ASTERISK_LOCAL_NET": "172.31.0.0/16",
              "SIP_PROVIDER_HOST": "sip.provider.example", "SIP_PROVIDER_IPS": "192.0.2.1, 192.0.2.2,198.51.100.0/24"}


def _render(tmp_path, env: dict) -> subprocess.CompletedProcess:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("needs a POSIX shell")
    script = (ASTERISK_DIR / "render-config.sh").as_posix()
    return subprocess.run([sh, script, (ASTERISK_DIR / "config").as_posix(), tmp_path.as_posix()],
                          env={"PATH": os.environ["PATH"], **env}, capture_output=True, text=True)


def test_asterisk_config_is_rendered_from_settings(tmp_path):
    result = _render(tmp_path, RENDER_ENV)
    assert result.returncode == 0, result.stderr
    pjsip = (tmp_path / "pjsip.conf").read_text()
    assert "@" not in pjsip  # every placeholder was filled in
    assert "external_media_address=203.0.113.7" in pjsip and "external_signaling_address=203.0.113.7" in pjsip
    assert "local_net=172.31.0.0/16" in pjsip and "contact=sip:sip.provider.example" in pjsip
    assert pjsip.rstrip().endswith("match=192.0.2.1\nmatch=192.0.2.2\nmatch=198.51.100.0/24")
    assert {"extensions.conf", "rtp.conf", "modules.conf", "logger.conf"} <= {p.name for p in tmp_path.iterdir()}
    assert not any("secret-token" in p.read_text() for p in tmp_path.iterdir())  # the token stays in the environment


@pytest.mark.parametrize("missing", list(RENDER_ENV))
def test_asterisk_refuses_to_start_with_a_setting_missing(tmp_path, missing):
    result = _render(tmp_path, {k: v for k, v in RENDER_ENV.items() if k != missing})
    assert result.returncode != 0 and missing in result.stderr


def _dialplan() -> str:
    return (ASTERISK_DIR / "config" / "extensions.conf").read_text()


def test_dialplan_jumps_only_to_labels_that_exist():
    text = _dialplan()
    labels = set(re.findall(r"n\((\w+)\),", text))
    targets = set(re.findall(r"\?(\w+)\)", text)) | set(re.findall(r"Goto\((\w+)\)", text))
    assert targets and targets <= labels


def test_dialplan_uses_routes_and_fields_the_api_has():
    dialplan = _dialplan()
    assert "VR_API=http://127.0.0.1:8000/api/telephony/asterisk)" in dialplan
    paths = {f"/api/telephony/asterisk/{name}" for name in re.findall(r"\$\{VR_API\}/([\w-]+)", dialplan)}
    paths |= set(re.findall(r"http://127\.0\.0\.1:8000(/api/[\w/-]+)", (ASTERISK_DIR / "heartbeat.sh").read_text()))
    api = app.openapi()["paths"]
    assert len(paths) == 4 and all("post" in api[p] for p in paths)
    assert "uuid=${VR_UUID}&did=" in dialplan and "&caller=" in dialplan


def test_dialplan_reaches_the_ports_the_app_uses():
    port = get_settings().audiosocket_port
    assert f"127.0.0.1:{port}" in _dialplan()
    assert f'"127.0.0.1:{port}:{port}"' in (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()

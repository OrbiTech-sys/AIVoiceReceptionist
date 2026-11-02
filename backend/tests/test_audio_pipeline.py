"""Real audio through the full pipeline, using the locally installed models.

Skipped automatically when the models have not been downloaded (e.g. in CI without model cache).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.config import get_settings
from app.voice import models_manager as mm
from app.voice import runtime
from app.voice.engines import float_to_pcm16, resample

REAL_MODELS = Path(__file__).resolve().parent.parent / "data" / "models"


@pytest.fixture
def real_models(monkeypatch):
    monkeypatch.setattr(get_settings(), "models_dir", REAL_MODELS)
    mm._verified_cache.clear()
    if not (mm.installed_asr() and mm.is_installed("tts-kokoro")):
        pytest.skip("speech models not installed (run the model installer first)")
    runtime.reset_component("asr")
    runtime.reset_component("tts")
    yield
    runtime.reset_component("asr")
    runtime.reset_component("tts")
    mm._verified_cache.clear()


def test_spoken_question_is_heard_answered_and_spoken_back(owner, real_models):
    tts = runtime.get_tts()
    speech = resample(tts.synthesize("What are your opening hours?", "am_michael"), tts.sample_rate, 16000)
    audio = np.concatenate([np.zeros(8000, np.float32), speech, np.zeros(16000, np.float32)])
    pcm = float_to_pcm16(audio)

    cookie = f"vr_session={owner.c.cookies.get('vr_session')}"
    with owner.c.websocket_connect("/api/receptionist/ws", headers={"origin": "http://localhost:3000", "cookie": cookie}) as ws:
        events = []

        def pump(until, limit=400):
            for _ in range(limit):
                m = ws.receive()
                if m.get("text"):
                    ev = json.loads(m["text"])
                    events.append(ev)
                    if until(ev):
                        return ev
                elif m.get("bytes") is not None:
                    events.append({"type": "_pcm", "n": len(m["bytes"])})
            raise AssertionError(f"not received; got {[e['type'] for e in events][-20:]}")

        ready = pump(lambda e: e["type"] == "ready")
        assert ready["asr"] and ready["tts"] and ready["vad"] == "silero"
        pump(lambda e: e["type"] == "transcript" and e["role"] == "agent")  # greeting text
        # Stream the caller's audio in 32 ms frames, like the browser worklet does.
        for i in range(0, len(pcm), 1024):
            ws.send_bytes(pcm[i:i + 1024])
        heard = pump(lambda e: e["type"] == "transcript" and e["role"] == "caller")
        assert "hours" in heard["text"].lower()
        assert heard["asr_ms"] is not None
        answer = pump(lambda e: e["type"] == "transcript" and e["role"] == "agent")
        assert "Monday through Friday" in answer["text"]
        pump(lambda e: e["type"] == "latency")
        audio_header = pump(lambda e: e["type"] == "audio")
        assert audio_header["sample_rate"] == 24000 and audio_header["samples"] > 2400
        ws.send_text(json.dumps({"type": "hangup"}))

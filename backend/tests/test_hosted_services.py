"""Deepgram (hearing), Fish Audio (voice) and Groq (understanding): requests, costs, and falling back to local models.
Nothing here touches the network: the services are replaced by fake HTTP handlers."""
from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
from test_conversation import _second_call

from app.config import get_settings
from app.voice import engines, llm, runtime


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class LocalASR:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, hints=""):
        self.calls += 1
        return engines.Transcript("from the local model", 0.8, 1)


def _deepgram_reply(text="my name is Abdul Basit", conf=0.99):
    return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": text, "confidence": conf}]}]}})


AUDIO = np.zeros(16000, dtype=np.float32)


def test_deepgram_sends_audio_with_the_business_terms_and_returns_its_confidence():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["bytes"] = request.url, request.headers["authorization"], len(request.content)
        return _deepgram_reply()

    asr = engines.DeepgramASR("k")
    asr._http = _client(handler)
    tr = asr.transcribe(AUDIO, "Bright Smile Dental. Consultation, Dental cleaning.")
    assert (tr.text, tr.confidence) == ("my name is Abdul Basit", 0.99)
    assert seen["auth"] == "Token k" and seen["bytes"] == 32000  # 1 s of 16-bit audio at 16 kHz
    q = seen["url"].params.multi_items()
    assert ("encoding", "linear16") in q and ("sample_rate", "16000") in q
    assert [v for k, v in q if k == "keyterm"] == ["Bright Smile Dental", "Consultation", "Dental cleaning"]


@pytest.mark.parametrize("hint, terms", [
    ("Bright Smile. Consultation, Whitening.", ["Bright Smile", "Consultation", "Whitening"]),
    ("Phone number, spoken digit by digit.", []),
    ("The caller's full name.", []),
    ("", []),
])
def test_only_business_words_are_sent_as_key_terms(hint, terms):
    assert engines._terms_from_hint(hint) == terms


def test_deepgram_failure_uses_the_local_model_and_a_failing_service_is_left_alone_for_a_while():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    local = LocalASR()
    asr = engines.DeepgramASR("k", fallback=lambda: local)
    asr._http = _client(handler)
    for _ in range(4):
        assert asr.transcribe(AUDIO).text == "from the local model"
    assert calls["n"] == 4 and local.calls == 4  # two failed turns (each tried twice), then no more waiting on the broken service


def test_deepgram_without_a_local_model_reports_the_problem_instead_of_guessing():
    asr = engines.DeepgramASR("k", fallback=None)
    asr._http = _client(lambda r: httpx.Response(500))
    with pytest.raises(RuntimeError):
        asr.transcribe(AUDIO)


# ------------------------------------------------------------------ Fish Audio
PCM = (np.sin(np.linspace(0, 400, 24000)) * 12000).astype("<i2").tobytes()  # one second at 24 kHz


class LocalVoice:
    sample_rate = 22050

    def __init__(self):
        self.said = []

    def voices(self):
        return list(engines.PIPER_VOICES)

    def synthesize(self, text, voice, speed=1.0):
        self.said.append((text, voice))
        return np.full(22050, 0.1, dtype=np.float32)


def _fish(tmp_path, handler, cap=100, fallback=None):
    tts = engines.FishTTS("k", tmp_path, cap, fallback=fallback, voice_id="voice123")
    tts._http = _client(handler)
    return tts


def test_fish_speaks_once_pays_once_and_remembers_the_phrase_on_disk(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        body = json.loads(request.content)
        assert body["format"] == "pcm" and body["sample_rate"] == 24000 and "prosody" not in body
        assert body["reference_id"] == "voice123"  # one fixed voice, not whatever Fish picks
        assert request.headers["authorization"] == "Bearer k" and request.headers["model"] == "s1"
        return httpx.Response(200, content=PCM)

    tts = _fish(tmp_path, handler)
    audio = tts.synthesize("Is that correct?", "fish_default")
    assert audio.size == 24000 and audio.dtype == np.float32 and abs(audio).max() > 0.3
    tts.synthesize("Is that correct?", "fish_default")
    assert n["calls"] == 1 and tts.chars_left() == 100 - len("Is that correct?")
    again = _fish(tmp_path, handler)  # a new start-up still has the phrase
    again.synthesize("Is that correct?", "fish_default")
    assert n["calls"] == 1 and again.chars_left() == tts.chars_left()


def test_fish_speed_is_passed_on(tmp_path):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=PCM)

    _fish(tmp_path, handler).synthesize("Hello there.", "fish_default", 1.2)
    assert seen["prosody"] == {"speed": 1.2}


def test_fish_stops_spending_at_the_monthly_cap_and_the_local_voice_speaks(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        return httpx.Response(200, content=PCM)

    local = LocalVoice()
    tts = _fish(tmp_path, handler, cap=20, fallback=lambda: local)
    tts.synthesize("Is that correct?", "fish_default")  # 16 characters
    audio = tts.synthesize("Which works best for you?", "fish_default")  # would pass 20
    assert n["calls"] == 1 and local.said == [("Which works best for you?", "piper_kristin")]
    assert audio.size == 24000  # the local clip was converted to Fish's sample rate: 1 s


def test_fish_failure_falls_back_to_the_local_voice(tmp_path):
    local = LocalVoice()
    tts = _fish(tmp_path, lambda r: httpx.Response(503), fallback=lambda: local)
    assert tts.synthesize("Sure.", "fish_default").size == 24000 and local.said
    with pytest.raises(RuntimeError):
        _fish(tmp_path / "other", lambda r: httpx.Response(503)).synthesize("Sure.", "fish_default")


# ------------------------------------------------------------------ choosing engines
def test_hosted_services_are_used_only_when_keyed_and_not_switched_off(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "deepgram_api_key", "")
    assert not runtime.use_deepgram()
    monkeypatch.setattr(s, "deepgram_api_key", "k")
    assert runtime.use_deepgram()
    monkeypatch.setattr(s, "asr_provider", "local")
    assert not runtime.use_deepgram()
    monkeypatch.setattr(s, "tts_engine", "fish")
    assert not runtime.use_fish()  # no key yet
    monkeypatch.setattr(s, "fish_audio_api_key", "k")
    assert runtime.use_fish() and runtime.active_tts_id() == "tts-fish"
    assert runtime.effective_voice("af_heart") == "fish_default"
    assert [v["id"] for v in runtime.available_voices()] == ["fish_default"]


def test_deepgram_counts_as_ready_speech_recognition_without_a_local_model(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "deepgram_api_key", "k")
    assert runtime.readiness()["asr"] == {"ready": True, "model": "deepgram-nova-3", "error": "", "latency_ms": None}


# ------------------------------------------------------------------ Groq
def _groq(monkeypatch, handler):
    s = get_settings()
    monkeypatch.setattr(s, "groq_api_key", "gk")
    monkeypatch.setattr(s, "llm_url", "")
    monkeypatch.setattr(llm, "_http", _client(handler))


def test_groq_gets_one_short_request_and_its_answer_is_validated(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content), auth=request.headers["authorization"], url=str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "status", "kb_id": null}'}}]})

    _groq(monkeypatch, handler)
    assistant = llm.LocalLLM.maybe()
    assert assistant is not None and llm.llm_status()["provider"] == "groq"
    r, _ = _second_call()
    assert assistant.classify("what apartment did you book", r.snap, "Is there anything else?") == ("status", None)
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions" and seen["auth"] == "Bearer gk"
    assert seen["model"] == get_settings().groq_model and seen["max_tokens"] == 40 and seen["temperature"] == 0
    user = json.loads(seen["messages"][1]["content"])
    assert user["caller_said"] == "what apartment did you book" and "faq" in user
    assert len(seen["messages"][0]["content"]) < 700  # a short, fixed prompt


def test_groq_errors_and_nonsense_are_ignored(monkeypatch):
    _groq(monkeypatch, lambda r: httpx.Response(429))
    r, _ = _second_call()
    assert llm.LocalLLM.maybe().classify("hello there friend", r.snap) is None
    _groq(monkeypatch, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "delete_everything"}'}}]}))
    assert llm.LocalLLM.maybe().classify("hello there friend", r.snap) is None


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def classify(self, text, snap, said_before=""):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else None


def test_the_llm_answers_a_question_the_rules_missed_and_the_booking_goes_on():
    r, tools = _second_call()
    r.llm = FakeLLM(("hours", None))
    r.handle("I want to book a consultation.")
    reply = r.handle("Remind me when the doors are unlocked until?", 0.9).text
    assert reply.startswith("We're open Monday through Friday") and reply.endswith("Now, what day or time works best for you?")
    assert r.state == "book_when"


def test_the_llm_is_not_asked_when_the_rules_understood_and_is_capped_per_call():
    r, _ = _second_call()
    r.llm = FakeLLM()
    r.llm_max_calls = 2
    r.handle("I want to book a consultation.")
    r.handle("Any time.")
    assert r.llm.asked == []  # understood without it: no cost
    r2, _ = _second_call()
    r2.llm, r2.llm_max_calls = FakeLLM(), 2
    for garbled in ("purple monkey dishwasher", "yellow banana telephone", "green chair lamp"):
        r2.handle(garbled, 0.3)
    assert len(r2.llm.asked) == 2
    r2.handle("one", 0.3)
    assert len(r2.llm.asked) == 2  # one-word sentences are never sent

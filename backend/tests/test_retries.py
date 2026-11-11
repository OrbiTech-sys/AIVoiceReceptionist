"""A brief hiccup in a hosted service must not end a call; a refused key must not be retried."""
from __future__ import annotations

import httpx
import numpy as np
import pytest

from app.voice import engines

AUDIO = np.zeros(16000, dtype=np.float32)
OK = {"results": {"channels": [{"alternatives": [{"transcript": "hello there", "confidence": 0.98}]}]}}


def _deepgram(handler) -> engines.DeepgramASR:
    asr = engines.DeepgramASR("k")
    asr._http = httpx.Client(transport=httpx.MockTransport(handler))
    return asr


@pytest.mark.parametrize("first", [httpx.Response(503), httpx.Response(429), "timeout"])
def test_deepgram_is_tried_again_after_a_temporary_failure(first):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            if first == "timeout":
                raise httpx.ConnectTimeout("slow", request=request)
            return first
        return httpx.Response(200, json=OK)

    assert _deepgram(handler).transcribe(AUDIO).text == "hello there"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_a_refused_key_or_empty_account_is_not_retried(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(RuntimeError):
        _deepgram(handler).transcribe(AUDIO)
    assert len(calls) == 1


def test_fish_is_tried_again_after_a_timeout_and_the_phrase_is_paid_for_once(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow", request=request)
        return httpx.Response(200, content=b"\x01\x02" * 4000)

    tts = engines.FishTTS("k", tmp_path, 100)
    tts._http = httpx.Client(transport=httpx.MockTransport(handler))
    assert tts.synthesize("Is that correct?", "fish_default").size == 4000
    assert len(calls) == 2 and tts.chars_left() == 100 - len("Is that correct?")


def test_fish_does_not_retry_when_the_account_is_out_of_credit(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(402)

    tts = engines.FishTTS("k", tmp_path, 100)
    tts._http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        tts.synthesize("Hello.", "fish_default")
    assert len(calls) == 1 and tts.chars_left() == 100  # nothing was charged for a failed request

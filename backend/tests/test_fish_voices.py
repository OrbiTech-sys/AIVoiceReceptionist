"""Choosing a Fish Audio voice: browsing the library, saving the choice, and speaking with it."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from app.errors import AppError
from app.services import fish_voices
from app.voice import engines, runtime

SARAH = "933563129e564b19a115bedd57b7406a"
RAW = {"_id": SARAH, "title": "Sarah", "description": "An engaged speaker. ", "tags": ["female", "young", "conversational"],
       "like_count": 9234, "visibility": "public", "state": "trained",
       "samples": [{"audio": "https://platform.r2.fish.audio/task/x.mp3", "text": "Hello there."}]}


@pytest.fixture
def fish(monkeypatch):
    """A fake Fish Audio library; records the requests the server makes."""
    s = get_settings()
    monkeypatch.setattr(s, "fish_audio_api_key", "sk-fish-test-key-123")
    monkeypatch.setattr(s, "tts_engine", "fish")
    fish_voices._cache.clear()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(SARAH):
            return httpx.Response(200, json=RAW)
        hidden = {**RAW, "_id": "a" * 32, "title": "Private", "visibility": "private"}
        return httpx.Response(200, json={"total": 30, "items": [RAW, hidden]})

    monkeypatch.setattr(fish_voices, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def test_browsing_lists_public_voices_with_free_samples_and_never_exposes_the_key(owner, fish):
    r = owner.get("/api/receptionist/fish-voices?q=sar&gender=female&page=2")
    assert r.status_code == 200
    body = r.json()
    assert [v["title"] for v in body["items"]] == ["Sarah"]  # the private one is left out
    assert body["items"][0] == {"id": f"fish:{SARAH}", "title": "Sarah", "description": "An engaged speaker.",
                                "tags": ["female", "young", "conversational"], "likes": 9234,
                                "sample_url": "https://platform.r2.fish.audio/task/x.mp3", "sample_text": "Hello there."}
    assert body["has_more"] is True and body["page"] == 2
    q = fish[0].url.params
    assert q["title"] == "sar" and q["tag"] == "female" and q["page_number"] == "2" and q["language"] == "en"
    assert "sk-fish-test-key-123" not in json.dumps(body) and fish[0].headers["authorization"] == "Bearer sk-fish-test-key-123"
    assert owner.get("/api/receptionist/fish-voices?gender=robot").status_code == 422


def test_without_a_fish_key_browsing_says_so(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "fish_audio_api_key", "")
    fish_voices._cache.clear()
    r = owner.get("/api/receptionist/fish-voices")
    assert r.status_code == 503 and "FISH_AUDIO_API_KEY" in r.json()["error"]["message"]


def test_choosing_a_voice_saves_only_the_voice_and_shows_it_as_current(owner, fish):
    assert owner.get("/api/receptionist/fish-voices/current").json()["voice"] is None
    before = owner.get("/api/receptionist").json()
    assert owner.put("/api/receptionist/voice", json={"voice_id": f"fish:{SARAH}"}).status_code == 200
    after = owner.get("/api/receptionist").json()
    assert after["voice_id"] == f"fish:{SARAH}"
    assert {k: v for k, v in after.items() if k not in ("voice_id", "readiness")} == {k: v for k, v in before.items() if k not in ("voice_id", "readiness")}
    assert owner.get("/api/receptionist/fish-voices/current").json()["voice"]["title"] == "Sarah"


@pytest.mark.parametrize("bad", ["af_heart", "fish:short", "fish:" + "z" * 32, "fish:" + SARAH + "/../x", ""])
def test_only_real_fish_voice_ids_can_be_chosen(owner, fish, bad):
    assert owner.put("/api/receptionist/voice", json={"voice_id": bad}).status_code in (400, 422)


def test_a_business_voice_is_used_on_calls_and_other_voices_still_map_by_gender(fish):
    assert runtime.effective_voice(f"fish:{SARAH}") == f"fish:{SARAH}"
    assert runtime.effective_voice("af_heart") == "fish_default"
    assert runtime.effective_voice("fish:nonsense") == "fish_default"


def test_fish_speaks_with_the_chosen_voice_and_keeps_each_voices_audio_apart(tmp_path):
    asked = []

    def handler(request):
        asked.append(json.loads(request.content).get("reference_id"))
        return httpx.Response(200, content=b"\x01\x02" * 4000)

    tts = engines.FishTTS("k", tmp_path, 100, voice_id="default-voice")
    tts._http = httpx.Client(transport=httpx.MockTransport(handler))
    tts.synthesize("Hello.", "fish_default")
    tts.synthesize("Hello.", f"fish:{SARAH}")
    tts.synthesize("Hello.", f"fish:{SARAH}")  # remembered
    assert asked == ["default-voice", SARAH]


def test_a_voice_that_does_not_exist_is_reported(monkeypatch):
    monkeypatch.setattr(get_settings(), "fish_audio_api_key", "k")
    with pytest.raises(AppError):
        fish_voices.one("not-an-id")

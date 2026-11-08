"""Choosing between the Kokoro and Piper voices."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.config import get_settings
from app.voice import models_manager as mm
from app.voice import runtime

REAL_MODELS = Path(__file__).resolve().parent.parent / "data" / "models"


def test_a_voice_from_the_other_engine_is_swapped_for_one_of_the_same_gender(monkeypatch):
    monkeypatch.setattr(mm, "installed_tts", lambda: "tts-piper")
    assert runtime.effective_voice("af_heart") == "piper_kristin"  # female -> female
    assert runtime.effective_voice("am_michael") == "piper_joe"  # male -> male
    assert runtime.effective_voice("piper_joe") == "piper_joe"
    monkeypatch.setattr(mm, "installed_tts", lambda: "tts-kokoro")
    assert runtime.effective_voice("piper_joe") == "am_michael"
    assert runtime.effective_voice("af_heart") == "af_heart"
    assert runtime.effective_voice("no_such_voice") == "af_heart"


def test_the_chosen_voice_engine_is_used_when_installed_and_the_other_when_not(monkeypatch):
    installed = {"tts-kokoro", "tts-piper"}
    monkeypatch.setattr(mm, "is_installed", lambda mid: mid in installed)
    monkeypatch.setattr(get_settings(), "tts_engine", "piper")
    assert mm.installed_tts() == "tts-piper"
    installed.discard("tts-piper")
    assert mm.installed_tts() == "tts-kokoro"
    installed.clear()
    assert mm.installed_tts() is None


def test_every_voice_file_is_pinned_to_a_checksum():
    for model_id in ("tts-piper", "asr-parakeet-tdt-0.6b-v2"):
        for f in mm.CATALOG[model_id].files:
            assert f.sha256 or f.git_sha1, f.rel_path


def test_piper_voices_speak(monkeypatch):
    monkeypatch.setattr(get_settings(), "models_dir", REAL_MODELS)
    mm._verified_cache.clear()
    if not mm.is_installed("tts-piper"):
        pytest.skip("Piper voices not installed")
    from app.voice.engines import PiperTTS

    tts = PiperTTS(REAL_MODELS / "tts" / "piper")
    slow, normal = tts.synthesize("Is that correct?", "piper_joe", 0.8), tts.synthesize("Is that correct?", "piper_joe", 1.0)
    assert normal.dtype == np.float32 and 0.5 < normal.size / tts.sample_rate < 3
    assert slow.size > normal.size  # a lower speed gives longer speech
    assert tts.synthesize("Hello.", "unknown_voice").size > 0  # an unknown voice falls back instead of failing
    mm._verified_cache.clear()

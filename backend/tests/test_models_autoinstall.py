"""Start-up auto-install of the default speech models (no network: install/is_installed are stubbed)."""
from __future__ import annotations

from app.config import get_settings
from app.voice import models_manager as mm


def _hw(ram_gb):
    return lambda: {"cpu_count": 4, "ram_gb": ram_gb, "disk_free_gb": 50.0, "gpu": None}


def test_downloads_only_the_missing_default_models(monkeypatch):
    started: list[str] = []
    asr_id = f"asr-{get_settings().asr_model}"
    monkeypatch.setattr(mm, "hardware", _hw(16.0))
    monkeypatch.setattr(mm, "is_installed", lambda mid: mid == "tts-kokoro")  # Kokoro already present
    monkeypatch.setattr(mm, "install", lambda mid, blocking=False: started.append(mid))

    assert mm.auto_install() == [asr_id]
    assert started == [asr_id]


def test_nothing_to_do_when_models_are_installed(monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(mm, "hardware", _hw(16.0))
    monkeypatch.setattr(mm, "is_installed", lambda mid: True)
    monkeypatch.setattr(mm, "install", lambda mid, blocking=False: started.append(mid))

    assert mm.auto_install() == []
    assert started == []


def test_skips_models_the_server_has_too_little_ram_for(monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(mm, "hardware", _hw(1.0))  # below both models' minimum
    monkeypatch.setattr(mm, "is_installed", lambda mid: False)
    monkeypatch.setattr(mm, "install", lambda mid, blocking=False: started.append(mid))

    assert mm.auto_install() == []
    assert started == []


def test_unknown_asr_model_name_does_not_crash(monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(get_settings(), "asr_model", "does-not-exist")
    monkeypatch.setattr(mm, "hardware", _hw(16.0))
    monkeypatch.setattr(mm, "is_installed", lambda mid: False)
    monkeypatch.setattr(mm, "install", lambda mid, blocking=False: started.append(mid))

    assert mm.auto_install() == ["tts-kokoro"]  # the valid one still goes ahead
    assert started == ["tts-kokoro"]

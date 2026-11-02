"""Lazy, process-wide engine instances with readiness reporting."""
from __future__ import annotations

import logging
import os
import threading
import time

from ..config import get_settings
from . import models_manager as mm
from .engines import ASREngine, EnergyVAD, FasterWhisperASR, KokoroTTS, SileroVAD, TTSEngine

log = logging.getLogger("voicerep.voice")
_lock = threading.Lock()
_asr: ASREngine | None = None
_asr_id: str | None = None
_tts: TTSEngine | None = None
_errors: dict[str, str] = {}
_latency: dict[str, int] = {}


def reset_component(component: str) -> None:
    global _asr, _asr_id, _tts
    with _lock:
        if component == "asr":
            _asr, _asr_id = None, None
        elif component == "tts":
            _tts = None
        _errors.pop(component, None)


def get_asr() -> ASREngine | None:
    global _asr, _asr_id
    with _lock:
        if _asr is not None:
            return _asr
        mid = mm.installed_asr()
        if mid is None:
            return None
        try:
            spec = mm.CATALOG[mid]
            _asr = FasterWhisperASR(mm.models_dir() / spec.files[0].rel_path.rsplit("/", 1)[0], get_settings().asr_threads or (os.cpu_count() or 4))
            _asr_id = mid
        except Exception as exc:
            _errors["asr"] = f"{type(exc).__name__}: {exc}"[:300]
            log.exception("ASR load failed")
            return None
        return _asr


def get_tts() -> TTSEngine | None:
    global _tts
    with _lock:
        if _tts is not None:
            return _tts
        if not mm.is_installed("tts-kokoro"):
            return None
        try:
            spec = mm.CATALOG["tts-kokoro"]
            _tts = KokoroTTS(mm.models_dir() / spec.files[0].rel_path, mm.models_dir() / spec.files[1].rel_path,
                             get_settings().tts_threads)
        except Exception as exc:
            _errors["tts"] = f"{type(exc).__name__}: {exc}"[:300]
            log.exception("TTS load failed")
            return None
        return _tts


def new_vad():
    try:
        return SileroVAD.load(), "silero"
    except Exception as exc:  # pragma: no cover - depends on installation
        _errors["vad"] = str(exc)[:300]
        return EnergyVAD(), "energy"


def warmup() -> dict:
    """Load engines and run a tiny inference to measure latency (used by the admin screen)."""
    import numpy as np

    res = {}
    asr = get_asr()
    if asr:
        t = time.perf_counter()
        asr.transcribe(np.zeros(16000, dtype=np.float32))
        _latency["asr"] = res["asr_ms"] = int((time.perf_counter() - t) * 1000)
    tts = get_tts()
    if tts:
        t = time.perf_counter()
        for v in tts.voices():
            tts.synthesize("Hello.", v.id)
            getattr(tts, "verified", set()).add(v.id)
        _latency["tts"] = res["tts_ms"] = int((time.perf_counter() - t) * 1000 / max(1, len(tts.voices())))
    return res


def readiness() -> dict:
    from .llm import llm_status

    asr_mid = mm.installed_asr()
    tts_ok = mm.is_installed("tts-kokoro")
    vad_kind = "silero"
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        vad_kind = "energy"
    asr_ready = asr_mid is not None and "asr" not in _errors
    tts_ready = tts_ok and "tts" not in _errors
    return {
        "asr": {"ready": asr_ready, "model": asr_mid, "error": _errors.get("asr", ""), "latency_ms": _latency.get("asr")},
        "tts": {"ready": tts_ready, "model": "tts-kokoro" if tts_ok else None, "error": _errors.get("tts", ""),
                "latency_ms": _latency.get("tts")},
        "vad": {"ready": True, "model": vad_kind},
        "llm": llm_status(),
        "voice_ready": asr_ready and tts_ready,
    }


def available_voices() -> list[dict]:
    """Only voices whose model files are installed are offered."""
    if not mm.is_installed("tts-kokoro"):
        return []
    tts = _tts
    verified = getattr(tts, "verified", set()) if tts else set()
    from .engines import KOKORO_VOICES

    return [{"id": v.id, "label": v.label, "gender": v.gender, "language": v.language, "verified": v.id in verified}
            for v in KOKORO_VOICES]

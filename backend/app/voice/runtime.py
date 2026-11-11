"""Lazy, process-wide engine instances with readiness reporting."""
from __future__ import annotations

import logging
import os
import threading
import time

from ..config import get_settings
from ..services.fish_voices import tenant_voice_id
from . import models_manager as mm
from .engines import (
    KOKORO_VOICES,
    PIPER_VOICES,
    ASREngine,
    EnergyVAD,
    FISH_VOICES,
    DeepgramASR,
    FasterWhisperASR,
    FishTTS,
    KokoroTTS,
    ParakeetASR,
    PiperTTS,
    SileroVAD,
    TTSEngine,
    VoiceInfo,
)

log = logging.getLogger("voicerep.voice")
_lock = threading.Lock()
_asr: ASREngine | None = None
_asr_id: str | None = None
_tts: TTSEngine | None = None
_errors: dict[str, str] = {}
_latency: dict[str, int] = {}


def _default_threads() -> int:
    """Speech recognition threads. On CPUs with two threads per core, using every thread was measured slower than
    one per core (i5-8350U: 2.0 s with 8 threads, 1.75 s with 4)."""
    n = os.cpu_count() or 4
    return max(2, n // 2) if n >= 6 else n


def reset_component(component: str) -> None:
    global _asr, _asr_id, _tts, _local_asr, _local_tts
    with _lock:
        if component == "asr":
            _asr, _asr_id, _local_asr = None, None, None
        elif component == "tts":
            _tts, _local_tts = None, None
        _errors.pop(component, None)


def use_deepgram() -> bool:
    s = get_settings()
    return s.asr_provider != "local" and bool(s.deepgram_api_key)


def use_fish() -> bool:
    s = get_settings()
    return s.tts_engine == "fish" and bool(s.fish_audio_api_key)


_local_asr: ASREngine | None = None
_local_tts: TTSEngine | None = None
_local_lock = threading.Lock()


def _load_local_asr() -> ASREngine | None:
    """The speech model that runs on this machine. With Deepgram on, it is loaded only if Deepgram fails."""
    global _local_asr
    with _local_lock:
        if _local_asr is None:
            mid = mm.installed_asr()
            if mid is None:
                return None
            spec = mm.CATALOG[mid]
            engine = ParakeetASR if mid.startswith("asr-parakeet") else FasterWhisperASR
            _local_asr = engine(mm.models_dir() / spec.files[0].rel_path.rsplit("/", 1)[0],
                                get_settings().asr_threads or _default_threads())
        return _local_asr


def _load_local_tts() -> TTSEngine | None:
    global _local_tts
    with _local_lock:
        if _local_tts is None:
            mid = mm.installed_tts()
            if mid is None:
                return None
            spec = mm.CATALOG[mid]
            if mid == "tts-piper":
                _local_tts = PiperTTS(mm.models_dir() / spec.files[0].rel_path.rsplit("/", 1)[0])
            else:
                _local_tts = KokoroTTS(mm.models_dir() / spec.files[0].rel_path, mm.models_dir() / spec.files[1].rel_path,
                                       get_settings().tts_threads)
        return _local_tts


def _quiet(loader):
    def load():
        try:
            return loader()
        except Exception:
            log.exception("local speech model could not be loaded")
            return None

    return load


def get_asr() -> ASREngine | None:
    global _asr, _asr_id
    with _lock:
        if _asr is not None:
            return _asr
        try:
            if use_deepgram():
                _asr = DeepgramASR(get_settings().deepgram_api_key, fallback=_quiet(_load_local_asr) if get_settings().local_fallback else None)
                _asr_id = "deepgram-nova-3"
            else:
                _asr = _load_local_asr()
                _asr_id = mm.installed_asr()
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
        try:
            if use_fish():
                s = get_settings()
                _tts = FishTTS(s.fish_audio_api_key, s.data_dir, s.fish_monthly_chars,
                              fallback=_quiet(_load_local_tts) if s.local_fallback else None,
                              voice_id=s.fish_voice_id)
            else:
                _tts = _load_local_tts()
        except Exception as exc:
            _errors["tts"] = f"{type(exc).__name__}: {exc}"[:300]
            log.exception("TTS load failed")
            return None
        return _tts


def active_tts_id() -> str | None:
    """Which voice model speaks: Fish Audio if chosen and keyed, else the installed local one."""
    return "tts-fish" if use_fish() else mm.installed_tts()


_ENGINE_VOICES: dict[str, tuple[VoiceInfo, ...]] = {"tts-kokoro": KOKORO_VOICES, "tts-piper": PIPER_VOICES, "tts-fish": FISH_VOICES}


def effective_voice(voice_id: str, model_id: str | None = None) -> str:
    """The voice to actually use. A business may have picked a voice from the other engine (or before the engine was
    changed): take the first voice of the same gender here, so calls never fail on an unknown voice."""
    model_id = model_id or active_tts_id() or "tts-kokoro"
    if model_id == "tts-fish" and tenant_voice_id(voice_id):
        return voice_id
    voices = _ENGINE_VOICES.get(model_id, KOKORO_VOICES)
    if any(v.id == voice_id for v in voices):
        return voice_id
    gender = next((v.gender for v in (*KOKORO_VOICES, *PIPER_VOICES, *FISH_VOICES) if v.id == voice_id), None)
    return next((v.id for v in voices if v.gender == gender), voices[0].id)


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


# Phrases the receptionist says on most calls, usually as the first chunk of a reply. Synthesized once at start-up
# so the caller hears them at once (the clip cache keeps them).
STOCK_PHRASES = (
    "Of course.", "Of course!", "Sure.", "Sure, go ahead.", "Okay, go on.", "Got it.", "Thank you.", "Great!",
    "Is that correct?", "Is that right?", "Is there anything else I can help with?", "Which works best for you?",
    "Which one would you like?", "Sorry, I didn't quite catch that.", "Sorry, the line wasn't clear.",
    "Do you have a preferred day or time?", "Let me confirm:", "Shall I book that for you?", "You're all set!",
    "What's the best number to reach you?", "May I have your full name, please?", "I can help with that.",
    "Are you still there?",
)


def prewarm_phrases() -> int:
    """Synthesize STOCK_PHRASES for the voices businesses have chosen. Runs in a background thread at start-up."""
    tts = get_tts()
    if tts is None or isinstance(tts, FishTTS):
        return 0  # a hosted voice is paid by the character: phrases are bought when first used, then kept on disk
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import ReceptionistSettings

    with SessionLocal() as db:
        voices = list(dict.fromkeys((r.voice_id, round(r.speed, 2)) for r in db.scalars(select(ReceptionistSettings))))
    done = 0
    for voice, speed in list(dict.fromkeys((effective_voice(v), sp) for v, sp in (voices or [("af_heart", 1.0)])))[:3]:
        for phrase in STOCK_PHRASES:
            try:
                tts.synthesize(phrase, voice, speed)
                done += 1
            except Exception:
                log.exception("could not prepare phrase for voice %s", voice)
                break
    return done


def startup_warmup() -> None:
    try:
        from .agent import get_agent

        get_agent()  # imports the LangChain packages now, not during the first call
    except Exception:
        log.exception("agent warm-up failed")
    warmup()
    try:
        n = prewarm_phrases()
        log.info("prepared %d common phrases", n)
    except Exception:
        log.exception("phrase prewarm failed")


def voice_ready() -> bool:
    """Could a caller be heard and answered right now? Unlike readiness(), this never probes the optional LLM
    over the network, so it is cheap enough to check on every incoming call."""
    return ((use_deepgram() or mm.installed_asr() is not None) and "asr" not in _errors
            and active_tts_id() is not None and "tts" not in _errors)


def fish_chars_left() -> int | None:
    """Fish Audio characters left this month under the cap, or None when Fish isn't the voice."""
    if not use_fish():
        return None
    import json

    s = get_settings()
    try:
        data = json.loads((s.data_dir / "fish_usage.json").read_text())
        used = int(data["chars"]) if data.get("month") == time.strftime("%Y-%m") else 0
    except Exception:
        used = 0
    return max(0, s.fish_monthly_chars - used)


def readiness() -> dict:
    from .llm import llm_status

    asr_mid = "deepgram-nova-3" if use_deepgram() else mm.installed_asr()
    tts_mid = active_tts_id()
    tts_ok = tts_mid is not None
    vad_kind = "silero"
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        vad_kind = "energy"
    asr_ready = asr_mid is not None and "asr" not in _errors
    tts_ready = tts_ok and "tts" not in _errors
    return {
        "asr": {"ready": asr_ready, "model": asr_mid, "error": _errors.get("asr", ""), "latency_ms": _latency.get("asr")},
        "tts": {"ready": tts_ready, "model": tts_mid, "error": _errors.get("tts", ""),
                "latency_ms": _latency.get("tts"), "chars_left": fish_chars_left(), "chars_cap": get_settings().fish_monthly_chars if use_fish() else None},
        "vad": {"ready": True, "model": vad_kind},
        "llm": llm_status(),
        "voice_ready": asr_ready and tts_ready,
    }


def available_voices() -> list[dict]:
    """Only voices of the installed engine are offered."""
    mid = active_tts_id()
    if mid is None:
        return []
    verified = getattr(_tts, "verified", set()) if _tts else set()
    return [{"id": v.id, "label": v.label, "gender": v.gender, "language": v.language, "verified": v.id in verified}
            for v in _ENGINE_VOICES[mid]]

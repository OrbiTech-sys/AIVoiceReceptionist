"""Speech engines behind small interfaces: VAD, ASR (speech-to-text) and TTS (text-to-speech).

Each engine is independent and replaceable. All audio inside the pipeline is mono float32.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import soxr

log = logging.getLogger("voicerep.voice")

PIPELINE_SR = 16000  # VAD + ASR sample rate


def resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample one complete clip. Frequencies the new rate can't carry are filtered out; without that they
    would fold back into the audible range as distortion."""
    audio = audio.astype(np.float32, copy=False)
    if src_sr == dst_sr or audio.size == 0:
        return audio
    return soxr.resample(audio, src_sr, dst_sr, quality="HQ")


class StreamResampler:
    """Resample live audio that arrives in small frames. The filter's state carries over between frames, so
    frame boundaries stay inaudible. Output runs about 30 ms behind the input, so early frames may come back empty."""

    def __init__(self, src_sr: int, dst_sr: int):
        self.src_sr = src_sr
        self._stream = None if src_sr == dst_sr else soxr.ResampleStream(src_sr, dst_sr, 1, dtype="float32", quality="HQ")

    def process(self, audio: np.ndarray) -> np.ndarray:
        audio = audio.astype(np.float32, copy=False)
        if self._stream is None or audio.size == 0:
            return audio
        return self._stream.resample_chunk(audio)


def pcm16_to_float(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(a: np.ndarray) -> bytes:
    return (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


# ------------------------------------------------------------------ VAD
class VAD(Protocol):
    frame_samples: int

    def prob(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class SileroVAD:
    """Streaming Silero VAD (v6 ONNX model bundled with faster-whisper, MIT). 512-sample frames @16 kHz."""

    frame_samples = 512
    context_samples = 64

    def __init__(self, session):
        self._session = session
        self.reset()

    @classmethod
    def load(cls) -> SileroVAD:
        import faster_whisper
        import onnxruntime

        path = Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        return cls(onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts))

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._ctx = np.zeros((1, self.context_samples), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = frame.astype(np.float32).reshape(1, -1)
        inp = np.concatenate([self._ctx, x], axis=1)
        out, self._h, self._c = self._session.run(None, {"input": inp, "h": self._h, "c": self._c})
        self._ctx = x[:, -self.context_samples:]
        return float(np.asarray(out).reshape(-1)[0])


class EnergyVAD:
    """Fallback when the Silero model is unavailable: adaptive RMS threshold."""

    frame_samples = 512

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._noise = 0.01

    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame**2) + 1e-12))
        if rms < self._noise * 2:
            self._noise = 0.95 * self._noise + 0.05 * rms
        return float(min(1.0, max(0.0, (rms - self._noise * 2.5) / (self._noise * 4 + 1e-4))))


@dataclass
class TurnEvent:
    kind: str  # speech_start | turn_end
    audio: np.ndarray | None = None


class TurnDetector:
    """Turns a stream of frames into speech-start and end-of-turn events."""

    def __init__(self, vad: VAD, *, start_threshold: float = 0.6, end_threshold: float = 0.35,
                 min_speech_ms: int = 220, end_silence_ms: int = 650, max_turn_s: float = 20.0, preroll_ms: int = 300):
        self.vad = vad
        self.start_t, self.end_t = start_threshold, end_threshold
        fm = vad.frame_samples * 1000 / PIPELINE_SR
        self.min_speech_frames = max(1, int(min_speech_ms / fm))
        self.set_end_silence_ms(end_silence_ms)
        self.max_frames = int(max_turn_s * 1000 / fm)
        self.preroll_frames = max(1, int(preroll_ms / fm))
        self._buf = np.zeros(0, dtype=np.float32)
        self._reset_turn()

    def set_end_silence_ms(self, ms: int) -> None:
        """How long a pause ends the caller's turn."""
        self.end_silence_frames = max(1, int(ms * PIPELINE_SR / (self.vad.frame_samples * 1000)))

    def _reset_turn(self) -> None:
        self.in_speech = False
        self._frames: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_run = 0
        self._announced = False

    def feed(self, audio16k: np.ndarray) -> list[TurnEvent]:
        events: list[TurnEvent] = []
        self._buf = np.concatenate([self._buf, audio16k])
        n = self.vad.frame_samples
        while self._buf.size >= n:
            frame, self._buf = self._buf[:n], self._buf[n:]
            p = self.vad.prob(frame)
            if not self.in_speech:
                self._pre.append(frame)
                if len(self._pre) > self.preroll_frames + self.min_speech_frames:
                    self._pre.pop(0)
                self._speech_run = self._speech_run + 1 if p >= self.start_t else 0
                if self._speech_run >= self.min_speech_frames:
                    self.in_speech = True
                    self._frames = list(self._pre)
                    self._silence_run = 0
                    events.append(TurnEvent("speech_start"))
            else:
                self._frames.append(frame)
                self._silence_run = self._silence_run + 1 if p < self.end_t else 0
                if self._silence_run >= self.end_silence_frames or len(self._frames) >= self.max_frames:
                    audio = np.concatenate(self._frames)
                    self._reset_turn()
                    self.vad.reset()
                    events.append(TurnEvent("turn_end", audio))
        return events


# ------------------------------------------------------------------ ASR
@dataclass
class Transcript:
    text: str
    confidence: float  # 0..1, derived from average log-probability
    latency_ms: int


class ASREngine(Protocol):
    def transcribe(self, audio16k: np.ndarray, hints: str = "") -> Transcript: ...


class FasterWhisperASR:
    def __init__(self, model_dir: Path, threads: int = 4):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=threads)
        self._lock = threading.Lock()

    def transcribe(self, audio16k: np.ndarray, hints: str = "") -> Transcript:
        t0 = time.perf_counter()
        with self._lock:
            segments, _info = self.model.transcribe(
                audio16k.astype(np.float32), language="en", beam_size=1, best_of=1, temperature=0.0,
                condition_on_previous_text=False, vad_filter=False, without_timestamps=True,
                initial_prompt=hints[:200] or None,
            )
            segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        if segs:
            avg = float(np.mean([s.avg_logprob for s in segs]))
            no_speech = float(np.mean([s.no_speech_prob for s in segs]))
            conf = float(np.clip(np.exp(avg), 0, 1)) * (1 - no_speech)
        else:
            conf = 0.0
        text = _clean_hallucinations(text)
        return Transcript(text, conf, int((time.perf_counter() - t0) * 1000))


_HALLUCINATIONS = re.compile(r"^(thanks? (you )?for watching|subtitles by|\[.*\]|\(.*\)|you\.?)$", re.I)


def _clean_hallucinations(text: str) -> str:
    return "" if _HALLUCINATIONS.match(text.strip()) else text


# ------------------------------------------------------------------ TTS
@dataclass(frozen=True)
class VoiceInfo:
    id: str
    label: str
    gender: str
    language: str


KOKORO_VOICES: tuple[VoiceInfo, ...] = (
    VoiceInfo("af_heart", "Heart — warm (US)", "female", "en-US"),
    VoiceInfo("af_bella", "Bella — bright (US)", "female", "en-US"),
    VoiceInfo("am_michael", "Michael — calm (US)", "male", "en-US"),
    VoiceInfo("am_fenrir", "Fenrir — deep (US)", "male", "en-US"),
    VoiceInfo("bf_emma", "Emma — clear (UK)", "female", "en-GB"),
    VoiceInfo("bm_george", "George — formal (UK)", "male", "en-GB"),
)


class TTSEngine(Protocol):
    sample_rate: int

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray: ...
    def voices(self) -> list[VoiceInfo]: ...


class KokoroTTS:
    sample_rate = 24000

    def __init__(self, model_path: Path, voices_path: Path, threads: int = 4):
        import onnxruntime
        from kokoro_onnx import Kokoro

        opts = onnxruntime.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.log_severity_level = 3
        sess = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"], sess_options=opts)
        self.k = Kokoro.from_session(sess, str(voices_path))
        self._lock = threading.Lock()
        self.verified: set[str] = set()
        self._cache: dict[tuple[str, str, float], np.ndarray] = {}

    def voices(self) -> list[VoiceInfo]:
        available = set(getattr(self.k, "voices", {}).keys()) if hasattr(self.k, "voices") else set()
        return [v for v in KOKORO_VOICES if not available or v.id in available]

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        key = (text, voice, round(float(speed), 2))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        lang = "en-gb" if voice.startswith("b") else "en-us"
        with self._lock:
            audio, _sr = self.k.create(text, voice=voice, speed=float(np.clip(speed, 0.7, 1.4)), lang=lang)
        out = np.asarray(audio, dtype=np.float32)
        # Greetings and stock phrases repeat across calls; keep a small cache of short clips.
        if len(text) <= 160:
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = out
        return out


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


_CLAUSE = re.compile(r"(?<=[;,:])\s+")


def split_sentences(text: str, max_len: int = 70) -> list[str]:
    """Short chunks let us start speaking after the first one is synthesised.

    Sentences are split first; long sentences are further split at clause punctuation."""
    chunks: list[str] = []
    for sent in (p.strip() for p in _SENTENCE.split(text.strip()) if p.strip()):
        if len(sent) <= max_len:
            chunks.append(sent)
            continue
        cur = ""
        for clause in _CLAUSE.split(sent):
            if cur and len(cur) + len(clause) + 1 > max_len:
                chunks.append(cur)
                cur = clause
            else:
                cur = f"{cur} {clause}".strip()
        if cur:
            chunks.append(cur)
    out: list[str] = []
    for c in chunks:
        # Merge a very short chunk forward only when the result stays short, keeping first audio fast.
        if out and len(out[-1]) < 18 and len(out[-1]) + len(c) < 45:
            out[-1] = f"{out[-1]} {c}"
        else:
            out.append(c)
    return out

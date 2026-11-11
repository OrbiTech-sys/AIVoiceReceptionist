"""Speech engines behind small interfaces: VAD, ASR (speech-to-text) and TTS (text-to-speech).

Each engine is independent and replaceable. All audio inside the pipeline is mono float32.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
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


class ParakeetASR:
    """NVIDIA Parakeet TDT 0.6B v2 (ONNX, int8) through onnx-asr. Whisper always processes a 30-second window, so even
    "yes" costs the full amount; Parakeet's work grows with the length of the speech. It is also more accurate,
    especially on accented English. It takes no prompt, so hints are ignored."""

    MODEL = "nemo-parakeet-tdt-0.6b-v2"

    def __init__(self, model_dir: Path, threads: int = 4):
        import onnx_asr
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self.model = onnx_asr.load_model(self.MODEL, str(model_dir), quantization="int8", sess_options=opts,
                                         providers=["CPUExecutionProvider"]).with_timestamps()
        self._lock = threading.Lock()

    def transcribe(self, audio16k: np.ndarray, hints: str = "") -> Transcript:
        t0 = time.perf_counter()
        with self._lock:
            result = self.model.recognize(audio16k.astype(np.float32), sample_rate=PIPELINE_SR)
        text = (result.text or "").strip()
        logprobs = result.logprobs or []
        # How sure the model was of the words it picked: the geometric mean of their probabilities.
        conf = float(np.clip(np.exp(np.mean(logprobs)), 0, 1)) if text and logprobs else 0.0
        return Transcript(text, conf, int((time.perf_counter() - t0) * 1000))


class _Breaker:
    """Stops calling a hosted service that keeps failing, so callers aren't kept waiting on timeouts."""

    def __init__(self, threshold: int = 2, cool_s: float = 30.0):
        self.threshold, self.cool_s = threshold, cool_s
        self._fails = 0
        self._until = 0.0

    @property
    def open(self) -> bool:
        return time.monotonic() < self._until

    def ok(self) -> None:
        self._fails = 0

    def fail(self) -> None:
        self._fails += 1
        if self._fails >= self.threshold:
            self._fails, self._until = 0, time.monotonic() + self.cool_s


def _temporary(exc: Exception) -> bool:
    """A failure worth one more try: the network hiccupped, the service had a problem, or it asked us to slow down. A
    refused key, an empty account or a bad request would fail again, so those aren't retried."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (408, 425, 429) or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _with_one_retry(call):
    try:
        return call()
    except Exception as exc:
        if not _temporary(exc):
            raise
        log.info("hosted service hiccup (%s); trying once more", type(exc).__name__)
        time.sleep(0.25)
        return call()


def _terms_from_hint(hint: str) -> list[str]:
    """Business and service names from the recognition hint, for Deepgram's keyterm prompting. The generic hints
    for names and phone numbers carry no terms."""
    if not hint or "digit" in hint or "full name" in hint:
        return []
    terms = [t.strip(" .") for t in re.split(r"[.,]\s*", hint)]
    return list(dict.fromkeys(t for t in terms if 3 <= len(t) <= 40))[:8]


def trim_trailing_silence(audio: np.ndarray, keep_s: float = 0.3, window: int = 160) -> np.ndarray:
    """Cut the quiet that follows the caller's last word (the turn detector waits 0.65-1.5 s before deciding they are
    done). Less audio to upload means a quicker answer, and the service bills for audio length."""
    if audio.size < window * 4:
        return audio
    n = audio.size // window
    rms = np.sqrt(np.mean(audio[: n * window].reshape(n, window) ** 2, axis=1))
    loud = np.flatnonzero(rms > max(0.006, float(rms.max()) * 0.08))
    if loud.size == 0:
        return audio
    end = min(audio.size, (int(loud[-1]) + 1) * window + int(keep_s * PIPELINE_SR))
    return audio[:end]


class DeepgramASR:
    """Hosted speech recognition (Deepgram Nova-3). Each turn is one request of the caller's audio, about 0.35-0.8 s,
    and its confidence is real: 1.00 on clear speech. Business and service names are sent as key terms, which is
    what gets names like "Basit" right. If the service fails or is slow, the local model takes over."""

    URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, fallback=None, timeout: float = 12.0):
        self._key = api_key
        self._fallback = fallback  # callable returning a local ASREngine, or None
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0))  # one connection is kept open between turns
        self._breaker = _Breaker()

    def _remote(self, audio16k: np.ndarray, hints: str) -> Transcript:
        t0 = time.perf_counter()
        params: list[tuple[str, str]] = [("model", "nova-3"), ("language", "en"), ("smart_format", "true"),
                                         ("encoding", "linear16"), ("sample_rate", str(PIPELINE_SR)), ("channels", "1")]
        params += [("keyterm", t) for t in _terms_from_hint(hints)]
        r = self._http.post(self.URL, params=params, content=float_to_pcm16(trim_trailing_silence(audio16k)),
                            headers={"Authorization": f"Token {self._key}", "Content-Type": "application/octet-stream"})
        r.raise_for_status()
        alts = r.json()["results"]["channels"][0]["alternatives"]
        text = (alts[0].get("transcript") or "").strip() if alts else ""
        conf = float(alts[0].get("confidence") or 0.0) if text else 0.0
        return Transcript(_clean_hallucinations(text), conf, int((time.perf_counter() - t0) * 1000))

    def transcribe(self, audio16k: np.ndarray, hints: str = "") -> Transcript:
        if not self._breaker.open:
            try:
                tr = _with_one_retry(lambda: self._remote(audio16k, hints))
                self._breaker.ok()
                return tr
            except Exception as exc:
                self._breaker.fail()
                log.warning("Deepgram failed (%s); using the local speech model", type(exc).__name__)
        local = self._fallback() if self._fallback else None
        if local is None:
            raise RuntimeError("speech recognition is unavailable")
        return local.transcribe(audio16k, hints)


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


PIPER_VOICES: tuple[VoiceInfo, ...] = (
    VoiceInfo("piper_kristin", "Kristin — clear (US)", "female", "en-US"),
    VoiceInfo("piper_joe", "Joe — steady (US)", "male", "en-US"),
)


class TTSEngine(Protocol):
    sample_rate: int

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray: ...
    def voices(self) -> list[VoiceInfo]: ...


FISH_VOICES: tuple[VoiceInfo, ...] = (VoiceInfo("fish_default", "Fish Audio — standard (US)", "female", "en-US"),)


class FishTTS:
    """Hosted voice (Fish Audio). It sounds natural but takes 0.8-1.4 s per sentence over the network, against 0.3 s
    for local Piper, and its free plan covers only 10,000 characters a month. So: every phrase is kept on disk and
    never paid for twice, spending stops at `monthly_chars` (then the local voice speaks), and any failure falls
    back to the local voice as well."""

    sample_rate = 24000
    URL = "https://api.fish.audio/v1/tts"

    def __init__(self, api_key: str, data_dir: Path, monthly_chars: int, fallback=None, model: str = "s1", timeout: float = 6.0,
                 voice_id: str = ""):
        self._key, self._model, self._cap, self._voice_id = api_key, model, monthly_chars, voice_id
        self._fallback = fallback  # callable returning a local TTSEngine, or None
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0))
        self._breaker = _Breaker()
        self._usage_file = data_dir / "fish_usage.json"
        self._cache_dir = data_dir / "tts_cache" / "fish"
        self._lock = threading.Lock()
        self._mem: dict[str, np.ndarray] = {}
        self.verified: set[str] = set()

    def voices(self) -> list[VoiceInfo]:
        return list(FISH_VOICES)

    # -- spending
    def _used(self) -> int:
        try:
            data = json.loads(self._usage_file.read_text())
            return int(data["chars"]) if data.get("month") == time.strftime("%Y-%m") else 0
        except Exception:
            return 0

    def _spend(self, chars: int) -> None:
        with self._lock:
            self._usage_file.parent.mkdir(parents=True, exist_ok=True)
            self._usage_file.write_text(json.dumps({"month": time.strftime("%Y-%m"), "chars": self._used() + chars}))

    def chars_left(self) -> int:
        return max(0, self._cap - self._used())

    # -- speech
    def _remote(self, text: str, speed: float, voice_ref: str) -> bytes:
        body: dict = {"text": text, "format": "pcm", "sample_rate": self.sample_rate, "latency": "normal"}
        if voice_ref:
            body["reference_id"] = voice_ref  # always the same voice
        if abs(speed - 1.0) > 0.01:
            body["prosody"] = {"speed": float(np.clip(speed, 0.5, 2.0))}
        r = self._http.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key}", "model": self._model})
        r.raise_for_status()
        if len(r.content) < 200:
            raise RuntimeError("empty audio")
        return r.content

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        # "fish:<id>" is the voice a business chose on the Voices page; anything else means the server's default voice
        ref = voice[5:] if voice.startswith("fish:") and len(voice) == 37 else self._voice_id
        key = hashlib.sha1(f"{ref}|{text}|{round(float(speed), 2)}".encode()).hexdigest()
        if key in self._mem:
            return self._mem[key]
        path = self._cache_dir / f"{key}.pcm"
        try:
            raw = path.read_bytes() if path.exists() else None
        except OSError:
            raw = None
        if raw is None and not self._breaker.open and len(text) <= self.chars_left():
            try:
                raw = _with_one_retry(lambda: self._remote(text, speed, ref))
                self._breaker.ok()
                self._spend(len(text))
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            except Exception as exc:
                self._breaker.fail()
                log.warning("Fish Audio failed (%s); using the local voice", type(exc).__name__)
        if raw is not None:
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if len(self._mem) >= 256:
                self._mem.pop(next(iter(self._mem)))
            self._mem[key] = audio
            return audio
        local = self._fallback() if self._fallback else None
        if local is None:
            raise RuntimeError("no voice is available")
        voices = local.voices()
        pick = next((v.id for v in voices if v.gender == "female"), voices[0].id)
        return resample(local.synthesize(text, pick, speed), local.sample_rate, self.sample_rate)


class PiperTTS:
    """Piper voices (ONNX). Far lighter than Kokoro: on a laptop CPU a sentence takes 0.25-0.7 s to synthesize
    instead of 2-6 s, so the caller hears the reply almost at once. The voice is plainer than Kokoro's."""

    sample_rate = 22050

    def __init__(self, voices_dir: Path):
        from piper import PiperVoice

        self._voices = {v.id: PiperVoice.load(voices_dir / f"en_US-{v.id.removeprefix('piper_')}-medium.onnx")
                        for v in PIPER_VOICES}
        rates = {pv.config.sample_rate for pv in self._voices.values()}
        if rates != {self.sample_rate}:
            raise RuntimeError(f"unexpected Piper sample rates {sorted(rates)}")
        self._lock = threading.Lock()
        self.verified: set[str] = set()
        self._cache: dict[tuple[str, str, float], np.ndarray] = {}

    def voices(self) -> list[VoiceInfo]:
        return list(PIPER_VOICES)

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        from piper import SynthesisConfig

        voice = voice if voice in self._voices else PIPER_VOICES[0].id
        key = (text, voice, round(float(speed), 2))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        cfg = SynthesisConfig(length_scale=1.0 / float(np.clip(speed, 0.7, 1.4)))
        with self._lock:
            chunks = [np.asarray(c.audio_float_array, dtype=np.float32) for c in self._voices[voice].synthesize(text, cfg)]
        out = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if len(text) <= 160:
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = out
        return out


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


def split_sentences(text: str, max_len: int = 70, first_max: int = 24) -> list[str]:
    """Short chunks let us start speaking after the first one is synthesised.

    Sentences are split first; long sentences are further split at clause punctuation. The first chunk is kept
    especially short ("Let me confirm:"), because the caller hears nothing until it is ready; the rest is
    synthesised while it plays."""
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
    if chunks and len(chunks[0]) > first_max:
        # Split the opening at its first clause break, if that leaves a piece worth saying on its own.
        m = next((m for m in re.finditer(r"[,;:]\s+", chunks[0]) if m.start() >= 7), None)
        if m:
            chunks[:1] = [chunks[0][:m.start() + 1], chunks[0][m.end():]]
    out: list[str] = []
    for c in chunks:
        # Merge a very short chunk forward only when the result stays short. Never into the first chunk: the
        # caller is waiting for it.
        if len(out) > 1 and len(out[-1]) < 18 and len(out[-1]) + len(c) < 45:
            out[-1] = f"{out[-1]} {c}"
        else:
            out.append(c)
    return out

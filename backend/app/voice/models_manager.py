"""Administrator-managed download, verification and readiness of local AI models.

Every file is pinned to a SHA-256 (Git LFS / release asset) or a Git blob SHA-1 and verified
after download. Nothing is executed from downloaded files: they are model weights only.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("voicerep.models")

HF ="https://huggingface.co"
KOKORO_REL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


@dataclass(frozen=True)
class FileSpec:
    url: str
    rel_path: str
    size: int
    sha256: str | None = None
    git_sha1: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    component: str  # asr | tts | llm
    name: str
    description: str
    license: str
    files: tuple[FileSpec, ...]
    min_ram_gb: float
    optional: bool = False
    notes: str = ""

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)


def _whisper(variant: str, model_sha: str, model_size: int, cfg_sha1: str, cfg_size: int) -> tuple[FileSpec, ...]:
    base = f"{HF}/Systran/faster-whisper-{variant}/resolve/main"
    d = f"asr/faster-whisper-{variant}"
    return (
        FileSpec(f"{base}/model.bin", f"{d}/model.bin", model_size, sha256=model_sha),
        FileSpec(f"{base}/config.json", f"{d}/config.json", cfg_size, git_sha1=cfg_sha1),
        FileSpec(f"{base}/tokenizer.json", f"{d}/tokenizer.json", 2128466, git_sha1="15d7bdf9ba25718ca2504eec6a8f02bc55af0a6a"),
        FileSpec(f"{base}/vocabulary.txt", f"{d}/vocabulary.txt", 422309, git_sha1="ee695b8d3e3c10d488304e04468efec4ca27554a"),
    )


PARAKEET_REV = f"{HF}/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85"
PARAKEET_DIR = "asr/parakeet-tdt-0.6b-v2"
PIPER_REV = f"{HF}/rhasspy/piper-voices/resolve/e7229f7d33a10e145e109b886d661984201ac3c4/en/en_US"

CATALOG: dict[str, ModelSpec] = {
    "asr-parakeet-tdt-0.6b-v2": ModelSpec(
        "asr-parakeet-tdt-0.6b-v2", "asr", "NVIDIA Parakeet TDT 0.6B v2 (ONNX, int8)",
        "Most accurate English recognition here, including accented speech. Its work grows with the length of what the "
        "caller says (Whisper always processes 30 seconds), so short replies are recognised 2–4× faster than Whisper base.en.",
        "CC-BY-4.0 (weights: NVIDIA) · onnx-asr MIT",
        (
            # The encoder is listed first: the runtime loads the model from that file's folder.
            FileSpec(f"{PARAKEET_REV}/encoder-model.int8.onnx", f"{PARAKEET_DIR}/encoder-model.int8.onnx", 652184014,
                     sha256="3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155"),
            FileSpec(f"{PARAKEET_REV}/decoder_joint-model.int8.onnx", f"{PARAKEET_DIR}/decoder_joint-model.int8.onnx", 8998286,
                     sha256="a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3"),
            FileSpec(f"{PARAKEET_REV}/nemo128.onnx", f"{PARAKEET_DIR}/nemo128.onnx", 139764,
                     sha256="a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
            FileSpec(f"{PARAKEET_REV}/vocab.txt", f"{PARAKEET_DIR}/vocab.txt", 9384,
                     git_sha1="f0742785f6073e80b911964c455b05f3609bf23b"),
            FileSpec(f"{PARAKEET_REV}/config.json", f"{PARAKEET_DIR}/config.json", 97,
                     git_sha1="02a773005666393de591dfd55230b778da1653d8"),
        ), 4.0,
        notes="Select it with VR_ASR_MODEL=parakeet-tdt-0.6b-v2. Uses about 1.5 GB more RAM than Whisper base.en."),

    "asr-tiny.en": ModelSpec(
        "asr-tiny.en", "asr", "Whisper tiny.en (faster-whisper, int8)", "Fastest English speech recognition; lower accuracy.",
        "MIT (weights: OpenAI Whisper, MIT)",
        _whisper("tiny.en", "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a", 75537502,
                 "4065bb3bed375b176d5465be117d2d202e210434", 2317), 1.0),
    "asr-base.en": ModelSpec(
        "asr-base.en", "asr", "Whisper base.en (faster-whisper, int8)", "Recommended: good accuracy with low CPU latency.",
        "MIT (weights: OpenAI Whisper, MIT)",
        _whisper("base.en", "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef", 145216508,
                 "594369787efe617005d199b03739ee0ead7e3ab7", 2227), 1.5),
    "asr-small.en": ModelSpec(
        "asr-small.en", "asr", "Whisper small.en (faster-whisper, int8)", "Higher accuracy; needs a fast CPU or GPU for real-time use.",
        "MIT (weights: OpenAI Whisper, MIT)",
        _whisper("small.en", "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a", 483545366,
                 "ced3fd5bbb3e9f7664658065d2d36164712cf1d5", 2657), 3.0),
    "tts-piper": ModelSpec(
        "tts-piper", "tts", "Piper voices: Kristin and Joe (ONNX)",
        "Fast voices that start speaking within a fraction of a second, even on a laptop. Plainer than Kokoro.",
        "Voices: Kristin public domain (LibriVox recordings), Joe CC0 · Piper engine GPL-3.0-or-later (see README)",
        (
            FileSpec(f"{PIPER_REV}/kristin/medium/en_US-kristin-medium.onnx", "tts/piper/en_US-kristin-medium.onnx",
                     63531379, sha256="5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4"),
            FileSpec(f"{PIPER_REV}/kristin/medium/en_US-kristin-medium.onnx.json", "tts/piper/en_US-kristin-medium.onnx.json",
                     4968, git_sha1="e94be84ab0dc3532b4b2b48cbb1f5d4a8746bedd"),
            FileSpec(f"{PIPER_REV}/joe/medium/en_US-joe-medium.onnx", "tts/piper/en_US-joe-medium.onnx",
                     63201294, sha256="58afce0321b8d9c46d7cdf9c16500cc55a793b4220212dba6b70fb788b3baf06"),
            FileSpec(f"{PIPER_REV}/joe/medium/en_US-joe-medium.onnx.json", "tts/piper/en_US-joe-medium.onnx.json",
                     4794, git_sha1="b4ab600fbd71cee986a8e7714bd88996f33be945"),
        ), 1.0,
        notes="Select it with VR_TTS_ENGINE=piper."),
    "tts-kokoro": ModelSpec(
        "tts-kokoro", "tts", "Kokoro-82M v1.0 (ONNX)", "Natural neural voices, faster than real time on a modern CPU. English (US/UK).",
        "Apache-2.0 (weights) · kokoro-onnx MIT · espeak-ng GPL-3.0 (phonemizer library, see README)",
        (
            # fp32 weights: on x86 CPUs this runs ~2x faster than the int8 variant.
            FileSpec(f"{KOKORO_REL}/kokoro-v1.0.onnx", "tts/kokoro-v1.0.onnx", 325532387,
                     sha256="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
            FileSpec(f"{KOKORO_REL}/voices-v1.0.bin", "tts/voices-v1.0.bin", 28214398,
                     sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
        ), 2.0),
    "llm-qwen2.5-1.5b": ModelSpec(
        "llm-qwen2.5-1.5b", "llm", "Qwen2.5-1.5B-Instruct (GGUF Q4_K_M)",
        "Optional. Improves understanding of unusual phrasing. Served by llama.cpp's llama-server.",
        "Apache-2.0",
        (FileSpec(f"{HF}/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
                  "llm/qwen2.5-1.5b-instruct-q4_k_m.gguf", 1117320736,
                  sha256="6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"),), 4.0, optional=True,
        notes="Off by default and not needed for calls. Needs llama-server (MIT) running with this file and VR_LLM_URL set, and about 2 GB more RAM. "
              "docker-compose.yml has an opt-in 'llm' profile for it (steps in the README)."),
}


@dataclass
class Progress:
    state: str = "idle"  # idle | downloading | verifying | done | error
    downloaded: int = 0
    total: int = 0
    error: str = ""
    started_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_progress: dict[str, Progress] = {mid: Progress() for mid in CATALOG}


def models_dir() -> Path:
    return get_settings().models_dir


def _verify(path: Path, spec: FileSpec) -> bool:
    if not path.exists() or path.stat().st_size != spec.size:
        return False
    if spec.sha256:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == spec.sha256
    if spec.git_sha1:
        h = hashlib.sha1(f"blob {spec.size}\0".encode())
        h.update(path.read_bytes())
        return h.hexdigest() == spec.git_sha1
    return True


_verified_cache: dict[str, tuple[float, bool]] = {}


def is_installed(model_id: str) -> bool:
    spec = CATALOG[model_id]
    root = models_dir()
    for f in spec.files:
        p = root / f.rel_path
        if not p.exists():
            return False
        key = str(p)
        mtime = p.stat().st_mtime
        cached = _verified_cache.get(key)
        if cached and cached[0] == mtime:
            if not cached[1]:
                return False
            continue
        ok = _verify(p, f)
        _verified_cache[key] = (mtime, ok)
        if not ok:
            return False
    return True


def hardware() -> dict:
    ram_gb = None
    try:
        if os.name == "nt":
            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            ram_gb = round(m.ullTotalPhys / 2**30, 1)
        else:
            ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:
        pass
    disk = shutil.disk_usage(models_dir())
    return {"cpu_count": os.cpu_count(), "ram_gb": ram_gb, "disk_free_gb": round(disk.free / 2**30, 1), "gpu": None}


def status() -> list[dict]:
    hw = hardware()
    out = []
    for mid, spec in CATALOG.items():
        pr = _progress[mid]
        installed = pr.state != "downloading" and is_installed(mid)
        compatible = hw["ram_gb"] is None or hw["ram_gb"] >= spec.min_ram_gb
        out.append({
            "id": mid, "component": spec.component, "name": spec.name, "description": spec.description,
            "license": spec.license, "size_mb": round(spec.size_bytes / 2**20), "min_ram_gb": spec.min_ram_gb,
            "optional": spec.optional, "notes": spec.notes, "installed": installed, "compatible": compatible,
            "progress": {"state": pr.state, "downloaded": pr.downloaded, "total": pr.total or spec.size_bytes, "error": pr.error},
        })
    return out


def _download_file(spec: FileSpec, dest: Path, pr: Progress) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(spec.url, headers={"User-Agent": "voicerep-model-manager/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            with pr.lock:
                pr.downloaded += len(chunk)
    os.replace(tmp, dest)


def install(model_id: str, blocking: bool = False) -> None:
    if model_id not in CATALOG:
        raise KeyError(model_id)
    pr = _progress[model_id]
    with pr.lock:
        if pr.state in ("downloading", "verifying"):
            return
        pr.state, pr.downloaded, pr.error, pr.started_at = "downloading", 0, "", time.time()
        pr.total = CATALOG[model_id].size_bytes

    def run():
        spec = CATALOG[model_id]
        try:
            free = shutil.disk_usage(models_dir()).free
            if free < spec.size_bytes * 1.2:
                raise RuntimeError(f"Not enough disk space: need {spec.size_bytes // 2**20} MB")
            for f in spec.files:
                dest = models_dir() / f.rel_path
                if _verify(dest, f):
                    with pr.lock:
                        pr.downloaded += f.size
                    continue
                _download_file(f, dest, pr)
                pr.state = "verifying"
                if not _verify(dest, f):
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(f"Checksum mismatch for {f.rel_path}; file removed")
                pr.state = "downloading"
            _verified_cache.clear()
            pr.state = "done"
            from . import runtime

            runtime.reset_component(spec.component)
        except Exception as exc:
            pr.state, pr.error = "error", str(exc)[:300]

    if blocking:
        run()
    else:
        threading.Thread(target=run, name=f"install-{model_id}", daemon=True).start()


def auto_install() -> list[str]:
    """Start background downloads for the default speech models that are missing.

    Called at API start-up when VR_AUTO_INSTALL_MODELS is on, so a fresh deployment needs no admin
    clicks. Idempotent: models that are already installed and verified are left alone, and a download
    that is already running is not started twice. Models the server has too little RAM for are skipped
    (the admin screen still explains why). Returns the ids whose download was started."""
    wanted = [f"asr-{get_settings().asr_model}", f"tts-{get_settings().tts_engine}"]
    ram_gb = hardware()["ram_gb"]
    started: list[str] = []
    for mid in wanted:
        spec = CATALOG.get(mid)
        if spec is None:
            log.warning("auto-install: unknown model %s (check VR_ASR_MODEL)", mid)
            continue
        if is_installed(mid):
            continue
        if ram_gb is not None and ram_gb < spec.min_ram_gb:
            log.warning("auto-install: skipping %s, it needs %.1f GB RAM and this server has %.1f GB",
                        mid, spec.min_ram_gb, ram_gb)
            continue
        install(mid)
        started.append(mid)
    return started


def remove(model_id: str) -> None:
    spec = CATALOG[model_id]
    for f in spec.files:
        (models_dir() / f.rel_path).unlink(missing_ok=True)
    _verified_cache.clear()
    _progress[model_id] = Progress()
    from . import runtime

    runtime.reset_component(spec.component)


def installed_tts() -> str | None:
    """The voice model to use: the chosen engine if it is installed, else the other one."""
    preferred = f"tts-{get_settings().tts_engine}"
    for mid in (preferred, "tts-kokoro", "tts-piper"):
        if mid in CATALOG and is_installed(mid):
            return mid
    return None


def installed_asr() -> str | None:
    preferred = f"asr-{get_settings().asr_model}"
    fallback = ("asr-base.en", "asr-tiny.en", "asr-small.en", "asr-parakeet-tdt-0.6b-v2")
    order = [preferred] + [m for m in fallback if m != preferred]
    for mid in order:
        if mid in CATALOG and is_installed(mid):
            return mid
    return None

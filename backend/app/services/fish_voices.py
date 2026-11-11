"""Browse Fish Audio's public voice library. The API key stays on the server; the browser only sees the voices
and their free sample clips (listening costs no characters)."""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..errors import AppError

API = "https://api.fish.audio/model"
VOICE_ID = re.compile(r"^[0-9a-f]{32}$")
PAGE_SIZE = 12
_cache: dict[tuple, tuple[float, object]] = {}
_http = httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0))


def tenant_voice_id(voice_id: str) -> str | None:
    """"fish:<id>" as stored for a business -> the Fish voice id, or None for anything else."""
    if voice_id.startswith("fish:") and VOICE_ID.match(voice_id[5:]):
        return voice_id[5:]
    return None


def _get(params: dict | None, path: str = "") -> dict:
    key = get_settings().fish_audio_api_key
    if not key:
        raise AppError("Fish Audio isn't set up on this server (no FISH_AUDIO_API_KEY).", code="fish_missing", status_code=503)
    ck = (path, tuple(sorted((params or {}).items())))
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 600:
        return hit[1]  # type: ignore[return-value]
    try:
        r = _http.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError:
        raise AppError("Couldn't reach Fish Audio. Try again in a moment.", code="fish_unavailable", status_code=502)
    if len(_cache) > 200:
        _cache.clear()
    _cache[ck] = (time.time(), data)
    return data


def _safe_sample_url(url: object) -> str:
    """The sample clip is played by the browser, so it must be an https address on Fish Audio's own domain."""
    if not isinstance(url, str):
        return ""
    p = urlparse(url)
    return url if p.scheme == "https" and p.hostname and p.hostname.endswith(".fish.audio") and not p.username else ""


def _shape(m: dict) -> dict:
    sample = (m.get("samples") or [{}])[0]
    return {
        "id": f"fish:{m['_id']}", "title": (m.get("title") or "")[:60], "description": (m.get("description") or "").strip()[:140],
        "tags": [t for t in (m.get("tags") or []) if isinstance(t, str)][:6], "likes": int(m.get("like_count") or 0),
        "sample_url": _safe_sample_url(sample.get("audio")), "sample_text": (sample.get("text") or "")[:200],
    }


def search(q: str = "", gender: str = "", page: int = 1) -> dict:
    params: dict = {"page_size": PAGE_SIZE, "page_number": max(1, page), "language": "en", "sort_by": "score"}
    if q.strip():
        params["title"] = q.strip()[:60]
    if gender in ("female", "male"):
        params["tag"] = gender
    data = _get(params)
    items = [_shape(m) for m in data.get("items", []) if m.get("visibility") == "public" and m.get("state", "trained") == "trained"]
    total = int(data.get("total") or 0)
    return {"items": items, "page": page, "has_more": page * PAGE_SIZE < total, "total": total}


def one(fish_id: str) -> dict:
    if not VOICE_ID.match(fish_id):
        raise AppError("Unknown voice.", code="invalid_voice")
    return _shape(_get(None, f"/{fish_id}"))

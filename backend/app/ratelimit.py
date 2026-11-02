"""Small in-process sliding-window rate limiter.

Sufficient for a single API instance. For multiple instances put a shared limiter
(e.g. a reverse proxy or Redis-based limiter) in front; the interface stays the same.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .errors import RateLimited


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_seconds - (now - q[0])) + 1
                raise RateLimited(f"Too many requests. Try again in {retry} seconds.", details={"retry_after": retry})
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < window_seconds}

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()

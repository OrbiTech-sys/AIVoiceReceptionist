"""Telephony adapter interface. Conversation and booking logic never depend on a specific provider:
an adapter only has to turn calls into CallSession instances with a Transport."""
from __future__ import annotations

from typing import Protocol


class TelephonyAdapter(Protocol):
    provider: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> dict: ...


_adapters: dict[str, TelephonyAdapter] = {}


def register(adapter: TelephonyAdapter) -> None:
    _adapters[adapter.provider] = adapter


def get(provider: str) -> TelephonyAdapter | None:
    return _adapters.get(provider)


def all_status() -> dict[str, dict]:
    return {k: a.status() for k, a in _adapters.items()}

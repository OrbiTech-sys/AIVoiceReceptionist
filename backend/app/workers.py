"""Background loop: reminders, notification outbox, data retention.

All job state lives in the database, so restarting the API never loses a reminder.
Running several API instances is safe: reminder jobs are claimed with a conditional UPDATE.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .config import get_settings
from .db import SessionLocal

log = logging.getLogger("voicerep.workers")


def tick() -> dict:
    from .services import delivery, reminders
    from .telephony import health

    out = {}
    with SessionLocal() as db:
        out["reminders"] = reminders.run_due(db)
    out["asterisk_down"] = health.check_heartbeat()  # before the outbox, so an alert goes out in this same pass
    with SessionLocal() as db:
        out["outbox"] = delivery.process_outbox(db)
    return out


async def run_forever(stop: asyncio.Event) -> None:
    interval = get_settings().worker_interval_seconds
    loop = asyncio.get_running_loop()
    last_retention = 0.0
    while not stop.is_set():
        try:
            await loop.run_in_executor(None, tick)
            if time.time() - last_retention > 3600:
                from .voice.session import purge_expired

                await loop.run_in_executor(None, purge_expired)
                last_retention = time.time()
        except Exception:
            log.exception("worker tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

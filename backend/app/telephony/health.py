"""Noticing when Asterisk stops working. It sends a heartbeat every minute, and the background worker alerts
businesses with a working phone line when those stop."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, utcnow
from ..models import ServiceHealth, TelephonyIntegration
from .audiosocket import _mark_offline

COMPONENT = "asterisk"
HEARTBEAT_TIMEOUT = timedelta(minutes=3)
DOWN_MESSAGE = "The phone system stopped responding."


def record_heartbeat(db: Session, now: datetime | None = None) -> None:
    row = db.get(ServiceHealth, COMPONENT) or ServiceHealth(component=COMPONENT)
    row.status = "ready"
    row.updated_at = now or utcnow()
    db.add(row)
    # Lines marked offline because of a missed heartbeat are working again.
    for integ in db.scalars(select(TelephonyIntegration).where(TelephonyIntegration.status == "error",
                                                               TelephonyIntegration.last_error == DOWN_MESSAGE)):
        integ.status = "connected"
        integ.last_error = ""
    db.commit()


def check_heartbeat(now: datetime | None = None) -> bool:
    """Returns True if Asterisk has gone quiet. Businesses are alerted once, because this only changes lines still
    marked connected. Does nothing until the first heartbeat, so servers without Asterisk never raise an alert."""
    with SessionLocal() as db:
        row = db.get(ServiceHealth, COMPONENT)
        if row is None or (now or utcnow()) - row.updated_at < HEARTBEAT_TIMEOUT:
            return False
    _mark_offline(DOWN_MESSAGE)
    return True

"""Push (Web Push / VAPID) and optional SMTP delivery for the notification outbox."""
from __future__ import annotations

import base64
import json
import logging
import smtplib
from email.message import EmailMessage

from cryptography.hazmat.primitives import serialization
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import AppSecret, DeliveryAttempt, Notification, PushSubscription, User
from ..security import decrypt_secret, encrypt_secret

log = logging.getLogger("voicerep.delivery")


def _vapid(db: Session):
    from py_vapid import Vapid

    row = db.get(AppSecret, "vapid")
    if row is None:
        v = Vapid()
        v.generate_keys()
        pem = v.private_pem().decode()
        pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        row = AppSecret(name="vapid", value_enc=encrypt_secret(pem),
                        public_value=base64.urlsafe_b64encode(pub).decode().rstrip("="))
        db.add(row)
        db.commit()
    return Vapid.from_pem(decrypt_secret(row.value_enc).encode()), row.public_value


def vapid_public_key(db: Session) -> str:
    return _vapid(db)[1]


def send_push_for(db: Session, n: Notification) -> str:
    from pywebpush import WebPushException, webpush

    subs = db.scalars(
        select(PushSubscription).where(PushSubscription.user_id == n.user_id, PushSubscription.tenant_id == n.tenant_id)
    ).all()
    if not subs:
        return "skipped"
    vapid, _ = _vapid(db)
    payload = json.dumps({
        "title": n.title, "body": n.body, "url": n.link or "/notifications",
        "tag": f"{n.kind}:{n.entity_id}", "urgent": n.severity == "urgent", "id": n.id,
    })
    ok = False
    for s in subs:
        try:
            webpush(
                subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": get_settings().vapid_contact},
                ttl=3600,
                timeout=10,
            )
            s.last_success_at = utcnow()
            s.failure_count = 0
            ok = True
            db.add(DeliveryAttempt(tenant_id=n.tenant_id, notification_id=n.id, channel="push", status="sent"))
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            s.failure_count += 1
            db.add(DeliveryAttempt(tenant_id=n.tenant_id, notification_id=n.id, channel="push", status="failed",
                                   detail=f"push service responded {code}"))
            if code in (404, 410):  # subscription expired
                db.delete(s)
        except Exception as exc:  # network errors etc.
            s.failure_count += 1
            db.add(DeliveryAttempt(tenant_id=n.tenant_id, notification_id=n.id, channel="push", status="failed",
                                   detail=type(exc).__name__))
    return "sent" if ok else "failed"


def email_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send_email_for(db: Session, n: Notification) -> str:
    if not email_configured():
        return "skipped"
    s = get_settings()
    user = db.get(User, n.user_id)
    if user is None:
        return "skipped"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, user.email, n.title
    msg.set_content(f"{n.body}\n\nOpen: {s.public_app_url}{n.link or '/notifications'}")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)
        db.add(DeliveryAttempt(tenant_id=n.tenant_id, notification_id=n.id, channel="email", status="sent"))
        return "sent"
    except Exception as exc:
        db.add(DeliveryAttempt(tenant_id=n.tenant_id, notification_id=n.id, channel="email", status="failed",
                               detail=type(exc).__name__))
        return "failed"


def process_outbox(db: Session, batch: int = 50) -> int:
    done = 0
    for n in db.scalars(select(Notification).where(Notification.push_status == "pending").limit(batch)).all():
        n.push_status = send_push_for(db, n)
        db.commit()
        done += 1
    for n in db.scalars(select(Notification).where(Notification.email_status == "pending").limit(batch)).all():
        n.email_status = send_email_for(db, n)
        db.commit()
        done += 1
    return done

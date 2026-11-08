"""The public website's contact form, and the platform admin inbox that reads it."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import AuthContext, client_ip, require_platform_admin
from ..errors import NotFound
from ..models import ContactMessage, User
from ..ratelimit import limiter
from ..services.audit import audit
from ..services.delivery import email_configured

router = APIRouter(tags=["contact"])  # included by the system router, which adds the /api prefix
log = logging.getLogger("voicerep.contact")

TOPICS = {"pricing": "Pricing", "setup": "Connecting a phone line", "support": "Account help", "other": "Something else"}


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    business: str = Field(default="", max_length=160)
    topic: str = "other"
    message: str = Field(min_length=10, max_length=4000)
    website: str = Field(default="", max_length=200)  # hidden from people; only bots fill it in

    @field_validator("name", "message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required.")
        return v

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        return v if v in TOPICS else "other"


class HandledIn(BaseModel):
    handled: bool


def _email_admins(recipients: list[str], body: ContactIn) -> None:
    """Runs after the response is sent, so a slow mail server never holds up the visitor."""
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(recipients)
    msg["Reply-To"] = body.email
    msg["Subject"] = f"Website message: {TOPICS[body.topic]}"
    business = f" ({body.business.strip()})" if body.business.strip() else ""
    msg.set_content(f"{body.name}{business} <{body.email}> wrote:\n\n{body.message}\n\nOpen: {s.public_app_url}/admin")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)
    except Exception as exc:  # the message is already saved and visible in Platform admin
        log.warning("could not e-mail a website message: %s", type(exc).__name__)


@router.post("/contact", status_code=201)
def submit_contact(body: ContactIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    limiter.check(f"contact:{client_ip(request)}", 5, 3600)
    if body.website.strip():
        return {"ok": True}  # a bot filled in the hidden field: answer as usual and store nothing
    db.add(ContactMessage(name=body.name, email=body.email.lower(), business=body.business.strip(), topic=body.topic,
                          message=body.message))
    db.commit()
    if email_configured():
        recipients = list(db.scalars(select(User.email).where(User.is_platform_admin.is_(True), User.is_active.is_(True))).all())
        if recipients:
            background.add_task(_email_admins, recipients, body)
    return {"ok": True}


def _row(m: ContactMessage) -> dict:
    return {"id": m.id, "name": m.name, "email": m.email, "business": m.business, "topic": TOPICS.get(m.topic, m.topic),
            "message": m.message, "created_at": m.created_at.isoformat(), "handled": m.handled_at is not None}


@router.get("/admin/contact-messages")
def list_contact_messages(_ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(ContactMessage).order_by(ContactMessage.created_at.desc()).limit(200)).all()
    return [_row(m) for m in rows]


def _get(db: Session, message_id: str) -> ContactMessage:
    m = db.get(ContactMessage, message_id)
    if m is None:
        raise NotFound("Message not found.")
    return m


@router.patch("/admin/contact-messages/{message_id}")
def mark_contact_message(message_id: str, body: HandledIn, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    m = _get(db, message_id)
    m.handled_at = utcnow() if body.handled else None
    audit(db, "admin.contact_handled", user_id=ctx.user.id, entity_type="contact_message", entity_id=m.id)
    db.commit()
    return _row(m)


@router.delete("/admin/contact-messages/{message_id}", status_code=204)
def delete_contact_message(message_id: str, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    m = _get(db, message_id)
    audit(db, "admin.contact_deleted", user_id=ctx.user.id, entity_type="contact_message", entity_id=m.id)
    db.delete(m)
    db.commit()

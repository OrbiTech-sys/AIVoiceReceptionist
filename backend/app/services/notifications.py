"""Owner notifications: in-app feed rows (one per member) plus a durable push/e-mail outbox."""
from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    NOTIFICATION_KINDS,
    URGENT_KINDS,
    Appointment,
    Membership,
    Notification,
    NotificationPreference,
    PushSubscription,
    Tenant,
)
from .timeutil import spoken_time, zone

DEFAULT_EVENTS = {k: True for k in NOTIFICATION_KINDS}


def get_prefs(db: Session, tenant_id: str, user_id: str) -> NotificationPreference:
    p = db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.tenant_id == tenant_id, NotificationPreference.user_id == user_id
        )
    )
    if p is None:
        p = NotificationPreference(tenant_id=tenant_id, user_id=user_id, events=dict(DEFAULT_EVENTS), reminder_offsets=[60])
        db.add(p)
        db.flush()
    return p


def in_quiet_hours(prefs: NotificationPreference, tenant: Tenant, now: datetime | None = None) -> bool:
    if not prefs.quiet_hours_enabled or prefs.quiet_start is None or prefs.quiet_end is None:
        return False
    local: time = (now or utcnow()).astimezone(zone(tenant.timezone)).time()
    s, e = prefs.quiet_start, prefs.quiet_end
    return (s <= local < e) if s < e else (local >= s or local < e)


def notify(
    db: Session,
    tenant: Tenant,
    kind: str,
    title: str,
    body: str = "",
    *,
    link: str = "",
    entity_type: str = "",
    entity_id: str = "",
    event_time: datetime | None = None,
    user_ids: list[str] | None = None,
    now: datetime | None = None,
) -> list[Notification]:
    """Create notifications for every member who wants this event. Caller commits."""
    assert kind in NOTIFICATION_KINDS, kind
    severity = "urgent" if kind in URGENT_KINDS else "info"
    members = db.scalars(select(Membership).where(Membership.tenant_id == tenant.id)).all()
    created: list[Notification] = []
    for m in members:
        if user_ids is not None and m.user_id not in user_ids:
            continue
        prefs = get_prefs(db, tenant.id, m.user_id)
        if not prefs.events.get(kind, True):
            continue
        if not prefs.in_app_enabled and not prefs.push_enabled:
            continue
        quiet = in_quiet_hours(prefs, tenant, now) and severity != "urgent"
        has_push = prefs.push_enabled and db.scalar(
            select(PushSubscription.id).where(PushSubscription.user_id == m.user_id, PushSubscription.tenant_id == tenant.id).limit(1)
        )
        n = Notification(
            tenant_id=tenant.id,
            user_id=m.user_id,
            kind=kind,
            severity=severity,
            title=title[:200],
            body=body[:1000],
            link=link,
            entity_type=entity_type,
            entity_id=entity_id,
            event_time=event_time,
            push_status="pending" if has_push and not quiet else "skipped",
            email_status="pending" if prefs.email_enabled and not quiet else "skipped",
        )
        db.add(n)
        created.append(n)
    db.flush()
    return created


def _appt_when(tenant: Tenant, start: datetime) -> str:
    local = start.astimezone(zone(tenant.timezone))
    return f"{local.strftime('%a %b')} {local.day} at {spoken_time(local)}"


def notify_appointment(db: Session, tenant: Tenant, appt: Appointment, kind: str, previous_start: datetime | None = None) -> None:
    name = appt.contact.name if appt.contact else "Customer"
    service = appt.service.name if appt.service else "Appointment"
    when = _appt_when(tenant, appt.start_at)
    via = " via AI receptionist" if appt.source == "ai_call" else ""
    if kind == "appointment_booked":
        title, body = f"New booking: {name}", f"{service} on {when}{via}."
    elif kind == "appointment_rescheduled":
        prev = f" (was {_appt_when(tenant, previous_start)})" if previous_start else ""
        title, body = f"Rescheduled: {name}", f"{service} moved to {when}{prev}."
    elif kind == "appointment_cancelled":
        title, body = f"Cancelled: {name}", f"{service} on {when} was cancelled."
    else:
        title, body = f"Reminder: {name}", f"{service} on {when}."
    notify(db, tenant, kind, title, body, link=f"/appointments?id={appt.id}", entity_type="appointment",
           entity_id=appt.id, event_time=appt.start_at)

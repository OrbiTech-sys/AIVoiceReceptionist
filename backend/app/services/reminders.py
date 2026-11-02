"""Durable appointment reminders stored in the database.

Jobs are unique per (appointment, recipient, offset). Workers claim a job with a
conditional UPDATE (status scheduled -> processing) so two workers can never send
the same reminder twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import ACTIVE_APPOINTMENT_STATUSES, Appointment, Membership, ReminderJob, Tenant
from .notifications import get_prefs, notify_appointment

MAX_REMINDER_OFFSET = 7 * 24 * 60


def schedule_for_appointment(db: Session, appt: Appointment, now: datetime | None = None) -> list[ReminderJob]:
    """Create or re-time reminder jobs for every member's configured offsets. Caller commits."""
    now = now or utcnow()
    members = db.scalars(select(Membership).where(Membership.tenant_id == appt.tenant_id)).all()
    existing = {
        (j.user_id, j.offset_minutes): j
        for j in db.scalars(select(ReminderJob).where(ReminderJob.appointment_id == appt.id)).all()
    }
    wanted: set[tuple[str, int]] = set()
    jobs: list[ReminderJob] = []
    for m in members:
        prefs = get_prefs(db, appt.tenant_id, m.user_id)
        if not prefs.events.get("appointment_reminder", True):
            continue
        for off in sorted({int(o) for o in prefs.reminder_offsets if 0 < int(o) <= MAX_REMINDER_OFFSET}):
            key = (m.user_id, off)
            wanted.add(key)
            due = appt.start_at - timedelta(minutes=off)
            job = existing.get(key)
            if job is None:
                job = ReminderJob(tenant_id=appt.tenant_id, appointment_id=appt.id, user_id=m.user_id,
                                  offset_minutes=off, due_at=due)
                db.add(job)
            elif job.due_at != due or job.status in ("cancelled", "skipped"):
                job.due_at = due
                job.status = "scheduled"
                job.attempts = 0
                job.sent_at = None
            # A reminder whose time has already passed is never sent late.
            if due <= now and job.status == "scheduled":
                job.status = "skipped"
                job.last_error = "reminder time already passed when scheduled"
            jobs.append(job)
    for key, job in existing.items():
        if key not in wanted and job.status == "scheduled":
            job.status = "cancelled"
    db.flush()
    return jobs


def cancel_for_appointment(db: Session, appt: Appointment) -> None:
    db.execute(
        update(ReminderJob)
        .where(ReminderJob.appointment_id == appt.id, ReminderJob.status == "scheduled")
        .values(status="cancelled")
    )


def resync_user(db: Session, tenant_id: str, user_id: str) -> None:
    """Re-plan future reminders after a user changes reminder offsets."""
    appts = db.scalars(
        select(Appointment).where(
            Appointment.tenant_id == tenant_id,
            Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
            Appointment.start_at > utcnow(),
        )
    ).all()
    for a in appts:
        schedule_for_appointment(db, a)


def run_due(db: Session, now: datetime | None = None, batch: int = 100) -> int:
    """Deliver due reminders. Returns the number sent."""
    now = now or utcnow()
    due_ids = db.scalars(
        select(ReminderJob.id)
        .where(ReminderJob.status == "scheduled", ReminderJob.due_at <= now)
        .order_by(ReminderJob.due_at)
        .limit(batch)
    ).all()
    db.commit()
    sent = 0
    for job_id in due_ids:
        claimed = db.execute(
            update(ReminderJob)
            .where(ReminderJob.id == job_id, ReminderJob.status == "scheduled")
            .values(status="processing", attempts=ReminderJob.attempts + 1)
        ).rowcount
        db.commit()
        if claimed != 1:
            continue  # another worker took it
        job = db.get(ReminderJob, job_id)
        assert job is not None
        try:
            appt = db.get(Appointment, job.appointment_id)
            tenant = db.get(Tenant, job.tenant_id)
            if appt is None or tenant is None or appt.status not in ACTIVE_APPOINTMENT_STATUSES:
                job.status = "cancelled"
            elif appt.start_at <= now:
                job.status = "skipped"
                job.last_error = "appointment already started"
            else:
                from .notifications import notify

                notify(
                    db, tenant, "appointment_reminder",
                    f"In {_fmt_offset(job.offset_minutes)}: {appt.contact.name}",
                    f"{appt.service.name} starts soon.",
                    link=f"/appointments?id={appt.id}", entity_type="appointment", entity_id=appt.id,
                    event_time=appt.start_at, user_ids=[job.user_id], now=now,
                )
                job.status = "sent"
                job.sent_at = now
                sent += 1
            db.commit()
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            job = db.get(ReminderJob, job_id)
            if job:
                job.status = "scheduled" if job.attempts < 3 else "failed"
                job.last_error = type(exc).__name__
                db.commit()
    return sent


def _fmt_offset(minutes: int) -> str:
    if minutes % 1440 == 0:
        d = minutes // 1440
        return f"{d} day" + ("s" if d > 1 else "")
    if minutes % 60 == 0:
        h = minutes // 60
        return f"{h} hour" + ("s" if h > 1 else "")
    return f"{minutes} min"


__all__ = ["schedule_for_appointment", "cancel_for_appointment", "run_due", "resync_user", "notify_appointment"]

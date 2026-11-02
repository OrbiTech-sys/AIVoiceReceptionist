"""Deterministic availability and booking engine.

All booking writes go through `book_appointment` / `reschedule_appointment`, which
1. take a per-resource lock (pg advisory lock on PostgreSQL; BEGIN IMMEDIATE on SQLite),
2. re-validate the slot against business hours, staff availability, notice rules and buffers,
3. check overlaps against active appointments, and
4. commit. PostgreSQL additionally enforces an exclusion constraint as a last line of defence.
A caller-facing confirmation is only produced after the commit returns.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import AppError, Conflict, NotFound
from ..models import (
    ACTIVE_APPOINTMENT_STATUSES,
    Appointment,
    BookingPolicy,
    BusinessHours,
    Contact,
    HolidayException,
    Service,
    StaffAvailability,
    StaffMember,
    StaffService,
    Tenant,
)
from ..security import normalize_phone
from .timeutil import local_to_utc, zone

TENANT_RESOURCE = "tenant"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled", "no_show"},
    "confirmed": {"completed", "cancelled", "no_show", "pending"},
    "completed": set(),
    "cancelled": set(),
    "no_show": {"completed"},
}


class SlotUnavailable(Conflict):
    code = "slot_unavailable"


@dataclass(frozen=True)
class Slot:
    start: datetime  # UTC
    end: datetime
    staff_id: str | None
    staff_name: str | None

    @property
    def resource_key(self) -> str:
        return self.staff_id or TENANT_RESOURCE


def get_policy(db: Session, tenant_id: str) -> BookingPolicy:
    p = db.get(BookingPolicy, tenant_id)
    if p is None:
        p = BookingPolicy(tenant_id=tenant_id)
        db.add(p)
        db.flush()
    return p


def _intervals_for_day(db: Session, tenant_id: str, d: date) -> list[tuple[time, time]]:
    """Opening intervals for a local date after applying holidays and breaks."""
    hol = db.scalar(select(HolidayException).where(HolidayException.tenant_id == tenant_id, HolidayException.date == d))
    if hol is not None:
        if hol.is_closed or not hol.open_time or not hol.close_time:
            return []
        return [(hol.open_time, hol.close_time)]
    bh = db.scalar(select(BusinessHours).where(BusinessHours.tenant_id == tenant_id, BusinessHours.weekday == d.weekday()))
    if bh is None or bh.is_closed or not bh.open_time or not bh.close_time:
        return []
    if bh.break_start and bh.break_end and bh.open_time < bh.break_start < bh.break_end < bh.close_time:
        return [(bh.open_time, bh.break_start), (bh.break_end, bh.close_time)]
    return [(bh.open_time, bh.close_time)]


def is_open_at(db: Session, tenant: Tenant, when: datetime) -> bool:
    tz = zone(tenant.timezone)
    local = when.astimezone(tz)
    return any(a <= local.time() < b for a, b in _intervals_for_day(db, tenant.id, local.date()))


def _intersect(a: list[tuple[time, time]], b: list[tuple[time, time]]) -> list[tuple[time, time]]:
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            s, e = max(s1, s2), min(e1, e2)
            if s < e:
                out.append((s, e))
    return out


def eligible_staff(db: Session, tenant_id: str, service_id: str) -> list[StaffMember]:
    """Staff linked to the service. If no staff are linked, the business books without staff calendars."""
    rows = db.scalars(
        select(StaffMember)
        .join(StaffService, StaffService.staff_id == StaffMember.id)
        .where(
            StaffService.service_id == service_id,
            StaffService.tenant_id == tenant_id,
            StaffMember.tenant_id == tenant_id,
            StaffMember.is_active.is_(True),
        )
        .order_by(StaffMember.name)
    ).all()
    return list(rows)


def _staff_intervals(db: Session, staff: StaffMember, weekday: int) -> list[tuple[time, time]] | None:
    rows = db.scalars(
        select(StaffAvailability).where(StaffAvailability.staff_id == staff.id, StaffAvailability.tenant_id == staff.tenant_id)
    ).all()
    if not rows:
        return None  # no custom schedule: follows business hours
    return [(r.start_time, r.end_time) for r in rows if r.weekday == weekday and r.start_time < r.end_time]


def _busy(db: Session, tenant_id: str, resource_key: str, start: datetime, end: datetime, exclude_id: str | None):
    q = select(Appointment.start_at, Appointment.blocked_until).where(
        Appointment.tenant_id == tenant_id,
        Appointment.resource_key == resource_key,
        Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
        Appointment.start_at < end,
        Appointment.blocked_until > start,
    )
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    return [(s, e) for s, e in db.execute(q).all()]


def find_slots(
    db: Session,
    tenant: Tenant,
    service: Service,
    start_date: date,
    end_date: date,
    *,
    staff_id: str | None = None,
    now: datetime | None = None,
    exclude_appointment_id: str | None = None,
    limit: int = 200,
) -> list[Slot]:
    """Return bookable slots between two local dates (inclusive), in chronological order."""
    if service.tenant_id != tenant.id:
        raise NotFound("Service not found.")
    tz: ZoneInfo = zone(tenant.timezone)
    policy = get_policy(db, tenant.id)
    now = now or utcnow()
    earliest = now + timedelta(minutes=policy.min_notice_min)
    latest = now + timedelta(days=policy.max_advance_days)
    duration = timedelta(minutes=service.duration_min)
    buffer = timedelta(minutes=service.buffer_min if service.buffer_min is not None else policy.default_buffer_min)
    step = timedelta(minutes=max(5, policy.slot_interval_min))

    staff_list: list[StaffMember | None]
    linked = eligible_staff(db, tenant.id, service.id)
    if staff_id:
        linked = [s for s in linked if s.id == staff_id]
        if not linked:
            return []
    staff_list = list(linked) if linked else [None]

    out: list[Slot] = []
    d = start_date
    while d <= end_date and len(out) < limit:
        day_intervals = _intervals_for_day(db, tenant.id, d)
        if day_intervals:
            day_start_utc = local_to_utc(d, time(0, 0), tz) or datetime.combine(d, time(0, 0), tzinfo=timezone.utc)
            day_end_utc = day_start_utc + timedelta(days=1, hours=2)
            for staff in staff_list:
                intervals = day_intervals
                if staff is not None:
                    custom = _staff_intervals(db, staff, d.weekday())
                    if custom is not None:
                        intervals = _intersect(day_intervals, custom)
                key = staff.id if staff else TENANT_RESOURCE
                busy = _busy(db, tenant.id, key, day_start_utc - timedelta(hours=12), day_end_utc, exclude_appointment_id)
                for open_t, close_t in intervals:
                    cursor = datetime.combine(d, open_t)
                    close_dt = datetime.combine(d, close_t)
                    while cursor + duration <= close_dt:
                        s_utc = local_to_utc(d, cursor.time(), tz)
                        cursor += step
                        if s_utc is None:
                            continue
                        e_utc = s_utc + duration
                        if s_utc < earliest or s_utc > latest:
                            continue
                        blocked = e_utc + buffer
                        if any(bs < blocked and be > s_utc for bs, be in busy):
                            continue
                        out.append(Slot(s_utc, e_utc, staff.id if staff else None, staff.name if staff else None))
        d += timedelta(days=1)
    out.sort(key=lambda s: (s.start, s.staff_name or ""))
    return out[:limit]


def slot_is_available(db: Session, tenant: Tenant, service: Service, start: datetime, staff_id: str | None,
                      exclude_appointment_id: str | None = None, now: datetime | None = None) -> Slot | None:
    tz = zone(tenant.timezone)
    local_date = start.astimezone(tz).date()
    for slot in find_slots(db, tenant, service, local_date, local_date, staff_id=staff_id, now=now,
                           exclude_appointment_id=exclude_appointment_id, limit=1000):
        if slot.start == start and (staff_id is None or slot.staff_id == staff_id):
            return slot
    return None


def _lock_resource(db: Session, tenant_id: str, resource_key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(f"{tenant_id}:{resource_key}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
    # SQLite: the session already holds a RESERVED lock via BEGIN IMMEDIATE (see db.py).


def upsert_contact(db: Session, tenant_id: str, name: str, phone: str | None, email: str = "") -> Contact:
    e164 = normalize_phone(phone) if phone else None
    contact = None
    if e164:
        contact = db.scalar(select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone_e164 == e164))
    if contact is None:
        contact = Contact(tenant_id=tenant_id, name=name.strip(), phone_e164=e164, email=email.strip())
        db.add(contact)
        db.flush()
    else:
        if name.strip() and not contact.name:
            contact.name = name.strip()
        if email and not contact.email:
            contact.email = email.strip()
    contact.last_interaction_at = utcnow()
    return contact


def book_appointment(
    db: Session,
    tenant: Tenant,
    *,
    service_id: str,
    start: datetime,
    customer_name: str,
    customer_phone: str | None,
    customer_email: str = "",
    staff_id: str | None = None,
    source: str = "dashboard",
    notes: str = "",
    call_id: str | None = None,
    user_id: str | None = None,
    enforce_rules: bool = True,
    now: datetime | None = None,
) -> Appointment:
    """Create an appointment atomically. Raises SlotUnavailable if the slot is taken or invalid."""
    from . import notifications, reminders  # local import to avoid cycles

    if start.tzinfo is None:
        raise AppError("Start time must include a time zone.", code="naive_datetime")
    start = start.astimezone(timezone.utc)
    if not customer_name.strip():
        raise AppError("Customer name is required.", code="name_required")
    service = db.get(Service, service_id)
    if service is None or service.tenant_id != tenant.id or not service.is_active:
        raise NotFound("Service not found.")
    if staff_id:
        staff = db.get(StaffMember, staff_id)
        if staff is None or staff.tenant_id != tenant.id:
            raise NotFound("Staff member not found.")

    policy = get_policy(db, tenant.id)
    try:
        if enforce_rules:
            # Choose resource first so we can lock it, then re-validate under the lock.
            probe = slot_is_available(db, tenant, service, start, staff_id, now=now)
            if probe is None:
                raise SlotUnavailable("That time is no longer available.")
            resource_key = probe.resource_key
            _lock_resource(db, tenant.id, resource_key)
            slot = slot_is_available(db, tenant, service, start, probe.staff_id, now=now)
            if slot is None:
                raise SlotUnavailable("That time is no longer available.")
            chosen_staff = slot.staff_id
            end = slot.end
        else:
            # Manual entry by staff may override hours, but never overlap.
            chosen_staff = staff_id
            resource_key = staff_id or TENANT_RESOURCE
            _lock_resource(db, tenant.id, resource_key)
            end = start + timedelta(minutes=service.duration_min)
        buffer = timedelta(minutes=service.buffer_min if service.buffer_min is not None else policy.default_buffer_min)
        blocked_until = end + buffer
        if _busy(db, tenant.id, resource_key, start, blocked_until, None):
            raise SlotUnavailable("That time overlaps another appointment.")

        contact = upsert_contact(db, tenant.id, customer_name, customer_phone, customer_email)
        appt = Appointment(
            tenant_id=tenant.id,
            contact_id=contact.id,
            service_id=service.id,
            staff_id=chosen_staff,
            resource_key=resource_key,
            start_at=start,
            end_at=end,
            blocked_until=blocked_until,
            status="confirmed" if policy.auto_confirm else "pending",
            source=source,
            notes=notes.strip()[:2000],
            call_id=call_id,
            created_by_user_id=user_id,
        )
        db.add(appt)
        db.flush()
        reminders.schedule_for_appointment(db, appt, now=now)
        notifications.notify_appointment(db, tenant, appt, "appointment_booked")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise SlotUnavailable("That time was just booked by someone else.") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(appt)
    return appt


def _get_appt(db: Session, tenant_id: str, appointment_id: str) -> Appointment:
    appt = db.get(Appointment, appointment_id)
    if appt is None or appt.tenant_id != tenant_id:
        raise NotFound("Appointment not found.")
    return appt


def reschedule_appointment(db: Session, tenant: Tenant, appointment_id: str, new_start: datetime, *,
                           staff_id: str | None = None, enforce_rules: bool = True, by_caller: bool = False,
                           now: datetime | None = None) -> Appointment:
    from . import notifications, reminders

    now = now or utcnow()
    appt = _get_appt(db, tenant.id, appointment_id)
    if appt.status not in ACTIVE_APPOINTMENT_STATUSES:
        raise AppError("Only pending or confirmed appointments can be rescheduled.", code="invalid_status")
    policy = get_policy(db, tenant.id)
    if by_caller and appt.start_at - now < timedelta(hours=policy.reschedule_notice_hours):
        raise AppError(
            f"Appointments can only be rescheduled at least {policy.reschedule_notice_hours} hours in advance.",
            code="policy_notice",
        )
    new_start = new_start.astimezone(timezone.utc)
    service = appt.service
    try:
        target_staff = staff_id if staff_id is not None else appt.staff_id
        if enforce_rules:
            slot = slot_is_available(db, tenant, service, new_start, target_staff, exclude_appointment_id=appt.id, now=now)
            if slot is None and staff_id is None:
                slot = slot_is_available(db, tenant, service, new_start, None, exclude_appointment_id=appt.id, now=now)
            if slot is None:
                raise SlotUnavailable("That time is not available.")
            _lock_resource(db, tenant.id, slot.resource_key)
            slot = slot_is_available(db, tenant, service, new_start, slot.staff_id, exclude_appointment_id=appt.id, now=now)
            if slot is None:
                raise SlotUnavailable("That time is not available.")
            resource_key, chosen_staff, end = slot.resource_key, slot.staff_id, slot.end
        else:
            chosen_staff = target_staff
            resource_key = chosen_staff or TENANT_RESOURCE
            _lock_resource(db, tenant.id, resource_key)
            end = new_start + timedelta(minutes=service.duration_min)
        buffer = timedelta(minutes=service.buffer_min if service.buffer_min is not None else policy.default_buffer_min)
        if _busy(db, tenant.id, resource_key, new_start, end + buffer, appt.id):
            raise SlotUnavailable("That time overlaps another appointment.")
        old_start = appt.start_at
        appt.start_at, appt.end_at, appt.blocked_until = new_start, end, end + buffer
        appt.staff_id, appt.resource_key = chosen_staff, resource_key
        db.flush()
        reminders.schedule_for_appointment(db, appt, now=now)
        notifications.notify_appointment(db, tenant, appt, "appointment_rescheduled", previous_start=old_start)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise SlotUnavailable("That time was just booked by someone else.") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(appt)
    return appt


def change_status(db: Session, tenant: Tenant, appointment_id: str, new_status: str, *, reason: str = "",
                  by_caller: bool = False, now: datetime | None = None) -> Appointment:
    from . import notifications, reminders

    now = now or utcnow()
    appt = _get_appt(db, tenant.id, appointment_id)
    if new_status == appt.status:
        return appt
    if new_status not in ALLOWED_TRANSITIONS.get(appt.status, set()):
        raise AppError(f"Cannot change an appointment from {appt.status} to {new_status}.", code="invalid_transition")
    if new_status == "cancelled" and by_caller:
        policy = get_policy(db, tenant.id)
        if appt.start_at - now < timedelta(hours=policy.cancellation_notice_hours):
            raise AppError(
                f"Appointments can only be cancelled at least {policy.cancellation_notice_hours} hours in advance.",
                code="policy_notice",
            )
    if new_status in ("completed", "no_show") and appt.start_at > now:
        raise AppError("An appointment can only be marked completed or no-show after it starts.", code="not_started")
    appt.status = new_status
    if new_status == "cancelled":
        appt.cancel_reason = reason.strip()[:300]
        reminders.cancel_for_appointment(db, appt)
        notifications.notify_appointment(db, tenant, appt, "appointment_cancelled")
    elif new_status in ("completed", "no_show"):
        reminders.cancel_for_appointment(db, appt)
    db.commit()
    db.refresh(appt)
    return appt


def find_upcoming_for_phone(db: Session, tenant_id: str, phone: str | None, name: str | None = None) -> list[Appointment]:
    e164 = normalize_phone(phone) if phone else None
    if not e164:
        return []
    q = (
        select(Appointment)
        .join(Contact, Contact.id == Appointment.contact_id)
        .where(
            and_(
                Appointment.tenant_id == tenant_id,
                Contact.tenant_id == tenant_id,
                Contact.phone_e164 == e164,
                Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
                Appointment.start_at > utcnow(),
            )
        )
        .order_by(Appointment.start_at)
    )
    return list(db.scalars(q).all())

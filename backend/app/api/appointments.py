from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..deps import TenantContext, require_tenant
from ..errors import AppError, NotFound
from ..models import APPOINTMENT_STATUSES, Appointment, Contact, Service
from ..services import scheduling
from ..services.audit import audit
from ..services.timeutil import local_to_utc, zone
from .common import appointment_out, iso, local_iso

router = APIRouter(prefix="/api/appointments", tags=["appointments"])


class CreateIn(BaseModel):
    service_id: str
    start_at: datetime
    staff_id: str | None = None
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str | None = Field(None, max_length=32)
    customer_email: str = Field("", max_length=254)
    notes: str = Field("", max_length=2000)
    override_hours: bool = False  # staff may book outside configured hours, never overlapping


class RescheduleIn(BaseModel):
    start_at: datetime
    staff_id: str | None = None
    override_hours: bool = False


class StatusIn(BaseModel):
    status: str = Field(pattern="^(pending|confirmed|completed|cancelled|no_show)$")
    reason: str = Field("", max_length=300)


class NotesIn(BaseModel):
    notes: str = Field("", max_length=2000)


def _q(ctx: TenantContext):
    return (select(Appointment).where(Appointment.tenant_id == ctx.tenant_id)
            .options(joinedload(Appointment.contact), joinedload(Appointment.service), joinedload(Appointment.staff)))


@router.get("")
def list_appointments(
    ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
    start: date | None = None, end: date | None = None,
    status: str | None = None, staff_id: str | None = None, service_id: str | None = None,
    q: str | None = Query(None, max_length=100), upcoming: bool = False,
    limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
):
    """Range filters are local dates in the business time zone (inclusive)."""
    tz = zone(ctx.tenant.timezone)
    stmt = _q(ctx)
    if start:
        stmt = stmt.where(Appointment.start_at >= local_to_utc(start, datetime.min.time(), tz))
    if end:
        stmt = stmt.where(Appointment.start_at < local_to_utc(end + timedelta(days=1), datetime.min.time(), tz))
    if upcoming:
        stmt = stmt.where(Appointment.start_at >= datetime.now(tz), Appointment.status.in_(("pending", "confirmed")))
    if status:
        statuses = [s for s in status.split(",") if s in APPOINTMENT_STATUSES]
        stmt = stmt.where(Appointment.status.in_(statuses))
    if staff_id:
        stmt = stmt.where(Appointment.staff_id == staff_id)
    if service_id:
        stmt = stmt.where(Appointment.service_id == service_id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.join(Contact, Contact.id == Appointment.contact_id).where(
            or_(func.lower(Contact.name).like(like), Contact.phone_e164.like(f"%{''.join(ch for ch in q if ch.isdigit()) or q}%"))
        )
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.order_by(Appointment.start_at).limit(limit).offset(offset)).all()
    return {"items": [appointment_out(a, ctx.tenant) for a in rows], "total": total}


@router.get("/availability")
def availability(
    service_id: str, start: date, days: int = Query(7, ge=1, le=31), staff_id: str | None = None,
    exclude_appointment_id: str | None = None,
    ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
):
    service = db.get(Service, service_id)
    if service is None or service.tenant_id != ctx.tenant_id:
        raise NotFound("Service not found.")
    slots = scheduling.find_slots(db, ctx.tenant, service, start, start + timedelta(days=days - 1), staff_id=staff_id,
                                  exclude_appointment_id=exclude_appointment_id, limit=500)
    db.commit()
    return {
        "timezone": ctx.tenant.timezone,
        "slots": [{"start_at": iso(s.start), "end_at": iso(s.end), "start_local": local_iso(s.start, ctx.tenant),
                   "staff_id": s.staff_id, "staff_name": s.staff_name} for s in slots],
    }


@router.get("/{appointment_id}")
def get_appointment(appointment_id: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    a = db.scalar(_q(ctx).where(Appointment.id == appointment_id))
    if a is None:
        raise NotFound("Appointment not found.")
    return appointment_out(a, ctx.tenant)


@router.post("", status_code=201)
def create_appointment(body: CreateIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    if body.start_at.tzinfo is None:
        raise AppError("start_at must include a UTC offset.", code="naive_datetime")
    appt = scheduling.book_appointment(
        db, ctx.tenant, service_id=body.service_id, start=body.start_at, customer_name=body.customer_name,
        customer_phone=body.customer_phone, customer_email=body.customer_email, staff_id=body.staff_id,
        source="manual" if body.override_hours else "dashboard", notes=body.notes, user_id=ctx.user.id,
        enforce_rules=not body.override_hours,
    )
    audit(db, "appointment.created", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="appointment", entity_id=appt.id)
    db.commit()
    return appointment_out(appt, ctx.tenant)


@router.post("/{appointment_id}/reschedule")
def reschedule(appointment_id: str, body: RescheduleIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    if body.start_at.tzinfo is None:
        raise AppError("start_at must include a UTC offset.", code="naive_datetime")
    appt = scheduling.reschedule_appointment(db, ctx.tenant, appointment_id, body.start_at, staff_id=body.staff_id,
                                             enforce_rules=not body.override_hours)
    audit(db, "appointment.rescheduled", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="appointment", entity_id=appt.id)
    db.commit()
    return appointment_out(appt, ctx.tenant)


@router.post("/{appointment_id}/status")
def set_status(appointment_id: str, body: StatusIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    appt = scheduling.change_status(db, ctx.tenant, appointment_id, body.status, reason=body.reason)
    audit(db, f"appointment.{body.status}", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="appointment", entity_id=appt.id)
    db.commit()
    return appointment_out(appt, ctx.tenant)


@router.patch("/{appointment_id}")
def update_notes(appointment_id: str, body: NotesIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    a = db.scalar(_q(ctx).where(Appointment.id == appointment_id))
    if a is None:
        raise NotFound("Appointment not found.")
    a.notes = body.notes.strip()
    db.commit()
    return appointment_out(a, ctx.tenant)

"""Contacts, calls and the overview dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import TenantContext, require_owner, require_tenant
from ..errors import AppError, Conflict, NotFound
from ..models import Appointment, Call, CallEvent, Contact, Notification, TelephonyIntegration
from ..security import normalize_phone
from ..services.audit import audit
from ..services.timeutil import local_to_utc, local_today, zone
from .common import appointment_out, call_out, contact_out, iso

router = APIRouter(prefix="/api", tags=["records"])


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=32)
    email: str = Field("", max_length=254)
    notes: str = Field("", max_length=4000)
    follow_up_status: str = Field("none", pattern="^(none|needed|done)$")


class FollowUpIn(BaseModel):
    follow_up_status: str = Field(pattern="^(none|needed|done)$")


# ------------------------------------------------------------------ contacts
@router.get("/contacts")
def list_contacts(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
                  q: str | None = Query(None, max_length=100), follow_up: str | None = None,
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    stmt = select(Contact).where(Contact.tenant_id == ctx.tenant_id)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        conds = [func.lower(Contact.name).like(f"%{q.lower()}%"), func.lower(Contact.email).like(f"%{q.lower()}%")]
        if digits:
            conds.append(Contact.phone_e164.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    if follow_up in ("none", "needed", "done"):
        stmt = stmt.where(Contact.follow_up_status == follow_up)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Contact.last_interaction_at.desc().nulls_last(), Contact.name).limit(limit).offset(offset)).all()
    counts = dict(db.execute(
        select(Appointment.contact_id, func.count()).where(Appointment.tenant_id == ctx.tenant_id,
                                                           Appointment.contact_id.in_([c.id for c in rows]))
        .group_by(Appointment.contact_id)).all())
    return {"items": [{**contact_out(c), "appointment_count": counts.get(c.id, 0)} for c in rows], "total": total}


def _contact(db: Session, ctx: TenantContext, cid: str) -> Contact:
    c = db.get(Contact, cid)
    if c is None or c.tenant_id != ctx.tenant_id:
        raise NotFound("Contact not found.")
    return c


@router.get("/contacts/{cid}")
def get_contact(cid: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    c = _contact(db, ctx, cid)
    appts = db.scalars(select(Appointment).where(Appointment.tenant_id == ctx.tenant_id, Appointment.contact_id == c.id)
                       .options(joinedload(Appointment.service), joinedload(Appointment.staff), joinedload(Appointment.contact))
                       .order_by(Appointment.start_at.desc())).all()
    calls = db.scalars(select(Call).where(Call.tenant_id == ctx.tenant_id, Call.contact_id == c.id).order_by(Call.started_at.desc()).limit(50)).all()
    return {**contact_out(c), "appointments": [appointment_out(a, ctx.tenant) for a in appts], "calls": [call_out(x) for x in calls]}


def _apply_contact(c: Contact, body: ContactIn) -> None:
    e164 = normalize_phone(body.phone) if body.phone else None
    if body.phone and not e164:
        raise AppError("Enter a valid phone number, including the country code if outside the US.", code="invalid_phone")
    c.name, c.phone_e164, c.email, c.notes, c.follow_up_status = body.name.strip(), e164, body.email.strip(), body.notes, body.follow_up_status


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    c = Contact(tenant_id=ctx.tenant_id)
    _apply_contact(c, body)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("A contact with this phone number already exists.", code="duplicate_contact")
    return contact_out(c)


@router.put("/contacts/{cid}")
def update_contact(cid: str, body: ContactIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    c = _contact(db, ctx, cid)
    _apply_contact(c, body)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("Another contact already uses this phone number.", code="duplicate_contact")
    return contact_out(c)


@router.delete("/contacts/{cid}")
def delete_contact(cid: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    """Privacy deletion: removes the contact, their appointments and call transcripts."""
    c = _contact(db, ctx, cid)
    call_ids = db.scalars(select(Call.id).where(Call.tenant_id == ctx.tenant_id, Call.contact_id == c.id)).all()
    db.execute(delete(CallEvent).where(CallEvent.tenant_id == ctx.tenant_id, CallEvent.call_id.in_(call_ids)))
    db.execute(delete(Call).where(Call.tenant_id == ctx.tenant_id, Call.id.in_(call_ids)))
    db.execute(delete(Appointment).where(Appointment.tenant_id == ctx.tenant_id, Appointment.contact_id == c.id))
    db.delete(c)
    audit(db, "contact.deleted", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="contact", entity_id=cid)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ calls
@router.get("/calls")
def list_calls(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
               q: str | None = Query(None, max_length=100), outcome: str | None = None, follow_up: str | None = None,
               channel: str | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    stmt = select(Call).where(Call.tenant_id == ctx.tenant_id)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        conds = [func.lower(Call.caller_name).like(f"%{q.lower()}%"), func.lower(Call.summary).like(f"%{q.lower()}%")]
        if digits:
            conds.append(Call.caller_number.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    if outcome:
        stmt = stmt.where(Call.outcome == outcome)
    if follow_up in ("none", "needed", "done"):
        stmt = stmt.where(Call.follow_up_status == follow_up)
    if channel in ("browser_demo", "phone"):
        stmt = stmt.where(Call.channel == channel)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Call.started_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [call_out(c) for c in rows], "total": total}


def _call(db: Session, ctx: TenantContext, call_id: str) -> Call:
    c = db.get(Call, call_id)
    if c is None or c.tenant_id != ctx.tenant_id:
        raise NotFound("Call not found.")
    return c


@router.get("/calls/{call_id}")
def get_call(call_id: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    c = _call(db, ctx, call_id)
    events = db.scalars(select(CallEvent).where(CallEvent.call_id == c.id, CallEvent.tenant_id == ctx.tenant_id).order_by(CallEvent.id)).all()
    appt = None
    if c.appointment_id:
        a = db.scalar(select(Appointment).where(Appointment.id == c.appointment_id, Appointment.tenant_id == ctx.tenant_id))
        appt = appointment_out(a, ctx.tenant) if a else None
    return {**call_out(c), "events": [{"ts": iso(e.ts), "kind": e.kind, "text": e.text, "data": e.data} for e in events],
            "appointment": appt}


@router.patch("/calls/{call_id}")
def update_call(call_id: str, body: FollowUpIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    c = _call(db, ctx, call_id)
    c.follow_up_status = body.follow_up_status
    db.commit()
    return call_out(c)


@router.get("/calls/{call_id}/recording")
def get_recording(call_id: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    c = _call(db, ctx, call_id)
    if not c.recording_path:
        raise NotFound("No recording for this call.")
    path = get_settings().data_dir / "recordings" / ctx.tenant_id / c.recording_path
    if not path.exists():
        raise NotFound("Recording file is no longer available.")
    audit(db, "call.recording_accessed", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="call", entity_id=c.id)
    db.commit()
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.delete("/calls/{call_id}")
def delete_call(call_id: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    c = _call(db, ctx, call_id)
    if c.recording_path:
        (get_settings().data_dir / "recordings" / ctx.tenant_id / c.recording_path).unlink(missing_ok=True)
    db.execute(delete(CallEvent).where(CallEvent.call_id == c.id, CallEvent.tenant_id == ctx.tenant_id))
    db.delete(c)
    audit(db, "call.deleted", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="call", entity_id=call_id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    tz = zone(ctx.tenant.timezone)
    today = local_today(tz)
    day_start = local_to_utc(today, datetime.min.time(), tz) or utcnow()
    day_end = day_start + timedelta(days=1)
    tid = ctx.tenant_id

    def count(stmt):
        return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    booked_today = count(select(Appointment.id).where(Appointment.tenant_id == tid, Appointment.created_at >= day_start,
                                                       Appointment.created_at < day_end))
    ai_booked_7d = count(select(Appointment.id).where(Appointment.tenant_id == tid, Appointment.source == "ai_call",
                                                       Appointment.created_at >= day_start - timedelta(days=6)))
    attention = db.scalars(select(Call).where(Call.tenant_id == tid, Call.follow_up_status == "needed").order_by(Call.started_at.desc()).limit(5)).all()
    attention_count = len(attention) if len(attention) < 5 else count(select(Call.id).where(Call.tenant_id == tid, Call.follow_up_status == "needed"))
    upcoming = db.scalars(select(Appointment).where(Appointment.tenant_id == tid, Appointment.start_at >= utcnow(),
                                                    Appointment.status.in_(("pending", "confirmed")))
                          .options(joinedload(Appointment.contact), joinedload(Appointment.service), joinedload(Appointment.staff))
                          .order_by(Appointment.start_at).limit(6)).all()
    upcoming_today = count(select(Appointment.id).where(Appointment.tenant_id == tid, Appointment.start_at >= day_start,
                                                         Appointment.start_at < day_end, Appointment.status.in_(("pending", "confirmed"))))
    recent = db.scalars(select(Notification).where(Notification.tenant_id == tid, Notification.user_id == ctx.user.id)
                        .order_by(Notification.created_at.desc()).limit(8)).all()
    # 7-day call volume for the compact chart: one query, grouped by local day here
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    first = local_to_utc(days[0], datetime.min.time(), tz) or day_start
    per_day = {d: 0 for d in days}
    for (started,) in db.execute(select(Call.started_at).where(Call.tenant_id == tid, Call.started_at >= first, Call.started_at < day_end)):
        local_day = started.astimezone(tz).date()
        if local_day in per_day:
            per_day[local_day] += 1
    series = [{"date": d.isoformat(), "calls": per_day[d]} for d in days]
    calls_today = per_day[today]
    tel = db.scalar(select(TelephonyIntegration).where(TelephonyIntegration.tenant_id == tid))
    return {
        "is_demo_tenant": ctx.tenant.is_demo,
        "calls_today": calls_today, "appointments_booked_today": booked_today, "ai_booked_7d": ai_booked_7d,
        "appointments_today": upcoming_today, "needs_attention": attention_count,
        "attention_calls": [call_out(c) for c in attention],
        "upcoming": [appointment_out(a, ctx.tenant) for a in upcoming],
        "recent_activity": [{"id": n.id, "title": n.title, "body": n.body, "kind": n.kind, "link": n.link,
                             "created_at": iso(n.created_at), "severity": n.severity} for n in recent],
        "call_series": series,
        "phone_status": tel.status if tel else "not_configured",
    }

"""Business profile, hours, holidays, booking policy, onboarding and privacy settings."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantContext, require_owner, require_tenant
from ..errors import AppError, NotFound
from ..models import (
    BusinessHours,
    Call,
    HolidayException,
    KnowledgeEntry,
    Service,
    StaffMember,
    TelephonyIntegration,
)
from ..services.audit import audit
from ..services.scheduling import get_policy
from ..services.tenants import BUSINESS_TYPES
from ..services.timeutil import ALL_TIMEZONES, is_valid_timezone
from .common import hhmm, parse_hhmm

router = APIRouter(prefix="/api/business", tags=["business"])

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ProfileIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=160)
    business_type: str | None = None
    timezone: str | None = None
    address: str | None = Field(None, max_length=300)
    directions: str | None = Field(None, max_length=2000)
    phone: str | None = Field(None, max_length=32)
    website: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=254)
    after_hours_behavior: str | None = Field(None, pattern="^(take_message|book_only|closed_message)$")
    emergency_policy: str | None = Field(None, max_length=1000)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        if v is not None and not is_valid_timezone(v):
            raise ValueError("Unknown time zone.")
        return v

    @field_validator("business_type")
    @classmethod
    def _bt(cls, v):
        if v is not None and v not in BUSINESS_TYPES:
            raise ValueError("Unknown business type.")
        return v

    @field_validator("website")
    @classmethod
    def _web(cls, v):
        if v and not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v


class PrivacyIn(BaseModel):
    recording_enabled: bool
    recording_consent_message: str = Field("", max_length=500)
    ai_disclosure_enabled: bool = True
    transcript_retention_days: int = Field(90, ge=1, le=3650)
    recording_retention_days: int = Field(30, ge=1, le=365)

    @field_validator("recording_consent_message")
    @classmethod
    def _consent(cls, v, info):
        return v.strip()


class DayHours(BaseModel):
    weekday: int = Field(ge=0, le=6)
    is_closed: bool
    open_time: str | None = Field(None, pattern=HHMM)
    close_time: str | None = Field(None, pattern=HHMM)
    break_start: str | None = Field(None, pattern=HHMM)
    break_end: str | None = Field(None, pattern=HHMM)


class HoursIn(BaseModel):
    days: list[DayHours] = Field(min_length=7, max_length=7)


class HolidayIn(BaseModel):
    date: date
    label: str = Field("", max_length=120)
    is_closed: bool = True
    open_time: str | None = Field(None, pattern=HHMM)
    close_time: str | None = Field(None, pattern=HHMM)


class PolicyIn(BaseModel):
    slot_interval_min: int = Field(ge=5, le=240)
    min_notice_min: int = Field(ge=0, le=60 * 24 * 14)
    max_advance_days: int = Field(ge=1, le=365)
    default_buffer_min: int = Field(ge=0, le=240)
    cancellation_notice_hours: int = Field(ge=0, le=24 * 14)
    reschedule_notice_hours: int = Field(ge=0, le=24 * 14)
    cancellation_policy_text: str = Field("", max_length=1000)
    auto_confirm: bool = True


class OnboardingIn(BaseModel):
    step: int = Field(ge=1, le=7)
    completed: bool = False


def profile_out(t) -> dict:
    return {
        "id": t.id, "name": t.name, "business_type": t.business_type, "timezone": t.timezone, "address": t.address,
        "directions": t.directions, "phone": t.phone, "website": t.website, "email": t.email,
        "after_hours_behavior": t.after_hours_behavior, "emergency_policy": t.emergency_policy,
        "onboarding_step": t.onboarding_step, "onboarding_completed": t.onboarding_completed, "is_demo": t.is_demo,
        "privacy": {
            "recording_enabled": t.recording_enabled, "recording_consent_message": t.recording_consent_message,
            "ai_disclosure_enabled": t.ai_disclosure_enabled, "transcript_retention_days": t.transcript_retention_days,
            "recording_retention_days": t.recording_retention_days,
        },
    }


@router.get("")
def get_profile(ctx: TenantContext = Depends(require_tenant)):
    return profile_out(ctx.tenant)


@router.patch("")
def update_profile(body: ProfileIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    t = ctx.tenant
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v.strip() if isinstance(v, str) else v)
    audit(db, "business.profile_updated", tenant_id=t.id, user_id=ctx.user.id)
    db.commit()
    return profile_out(t)


@router.put("/privacy")
def update_privacy(body: PrivacyIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    if body.recording_enabled and len(body.recording_consent_message) < 10:
        raise AppError("Enter the consent message callers will hear before recording starts.", code="consent_required")
    t = ctx.tenant
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    audit(db, "business.privacy_updated", tenant_id=t.id, user_id=ctx.user.id,
          details={"recording_enabled": body.recording_enabled, "ai_disclosure": body.ai_disclosure_enabled})
    db.commit()
    return profile_out(t)


@router.get("/meta")
def meta():
    return {"business_types": BUSINESS_TYPES, "timezones": ALL_TIMEZONES}


@router.get("/hours")
def get_hours(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    rows = {h.weekday: h for h in db.scalars(select(BusinessHours).where(BusinessHours.tenant_id == ctx.tenant_id))}
    days = []
    for wd in range(7):
        h = rows.get(wd)
        days.append({
            "weekday": wd, "is_closed": h.is_closed if h else True,
            "open_time": hhmm(h.open_time) if h else None, "close_time": hhmm(h.close_time) if h else None,
            "break_start": hhmm(h.break_start) if h else None, "break_end": hhmm(h.break_end) if h else None,
        })
    hols = db.scalars(select(HolidayException).where(HolidayException.tenant_id == ctx.tenant_id).order_by(HolidayException.date)).all()
    return {
        "days": days,
        "holidays": [{"id": x.id, "date": x.date.isoformat(), "label": x.label, "is_closed": x.is_closed,
                      "open_time": hhmm(x.open_time), "close_time": hhmm(x.close_time)} for x in hols],
        "timezone": ctx.tenant.timezone,
    }


@router.put("/hours")
def put_hours(body: HoursIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    if sorted(d.weekday for d in body.days) != list(range(7)):
        raise AppError("Provide exactly one entry per weekday.")
    rows = {h.weekday: h for h in db.scalars(select(BusinessHours).where(BusinessHours.tenant_id == ctx.tenant_id))}
    for d in body.days:
        o, c, bs, be = (parse_hhmm(x) for x in (d.open_time, d.close_time, d.break_start, d.break_end))
        if not d.is_closed:
            if not o or not c or o >= c:
                raise AppError(f"Day {d.weekday}: closing time must be after opening time.", code="invalid_hours")
            if (bs is None) != (be is None):
                raise AppError(f"Day {d.weekday}: set both break start and end, or neither.", code="invalid_hours")
            if bs and be and not (o < bs < be < c):
                raise AppError(f"Day {d.weekday}: break must be inside opening hours.", code="invalid_hours")
        h = rows.get(d.weekday) or BusinessHours(tenant_id=ctx.tenant_id, weekday=d.weekday)
        h.is_closed = d.is_closed
        h.open_time, h.close_time = (None, None) if d.is_closed else (o, c)
        h.break_start, h.break_end = (None, None) if d.is_closed else (bs, be)
        db.add(h)
    db.commit()
    return get_hours(ctx, db)


@router.post("/holidays", status_code=201)
def add_holiday(body: HolidayIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    o, c = parse_hhmm(body.open_time), parse_hhmm(body.close_time)
    if not body.is_closed and (not o or not c or o >= c):
        raise AppError("Special hours need an opening time before the closing time.", code="invalid_hours")
    existing = db.scalar(select(HolidayException).where(HolidayException.tenant_id == ctx.tenant_id, HolidayException.date == body.date))
    h = existing or HolidayException(tenant_id=ctx.tenant_id, date=body.date)
    h.label, h.is_closed = body.label.strip(), body.is_closed
    h.open_time, h.close_time = (None, None) if body.is_closed else (o, c)
    db.add(h)
    db.commit()
    return {"id": h.id}


@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    h = db.get(HolidayException, holiday_id)
    if h is None or h.tenant_id != ctx.tenant_id:
        raise NotFound("Holiday not found.")
    db.delete(h)
    db.commit()
    return {"ok": True}


def policy_out(p) -> dict:
    return {k: getattr(p, k) for k in PolicyIn.model_fields}


@router.get("/policy")
def get_policy_api(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    p = get_policy(db, ctx.tenant_id)
    db.commit()
    return policy_out(p)


@router.put("/policy")
def put_policy(body: PolicyIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    p = get_policy(db, ctx.tenant_id)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return policy_out(p)


@router.post("/onboarding")
def onboarding(body: OnboardingIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    t = ctx.tenant
    t.onboarding_step = max(t.onboarding_step, body.step)
    if body.completed:
        t.onboarding_completed = True
        audit(db, "business.onboarding_completed", tenant_id=t.id, user_id=ctx.user.id)
    db.commit()
    return profile_out(t)


@router.get("/setup-status")
def setup_status(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    t = ctx.tenant
    tid = t.id
    # One round trip for all the counts: the database may be far away, and every query costs a full trip there and back.
    n_services, n_kb, n_staff, open_days, tested, tel_status = db.execute(select(
        select(func.count()).select_from(Service).where(Service.tenant_id == tid, Service.is_active.is_(True)).scalar_subquery(),
        select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.tenant_id == tid).scalar_subquery(),
        select(func.count()).select_from(StaffMember).where(StaffMember.tenant_id == tid).scalar_subquery(),
        select(func.count()).select_from(BusinessHours).where(BusinessHours.tenant_id == tid, BusinessHours.is_closed.is_(False)).scalar_subquery(),
        exists().where(Call.tenant_id == tid, Call.channel == "browser_demo"),
        select(TelephonyIntegration.status).where(TelephonyIntegration.tenant_id == tid).limit(1).scalar_subquery(),
    )).one()
    items = [
        {"key": "profile", "label": "Business profile", "done": bool(t.address and t.phone), "href": "/settings?tab=profile"},
        {"key": "hours", "label": "Working hours", "done": open_days > 0, "href": "/settings?tab=hours"},
        {"key": "services", "label": "Services", "done": n_services > 0, "href": "/settings?tab=services"},
        {"key": "staff", "label": "Staff (optional)", "done": n_staff > 0, "href": "/settings?tab=services", "optional": True},
        {"key": "knowledge", "label": "Business knowledge / FAQs", "done": n_kb > 0, "href": "/receptionist?tab=knowledge"},
        {"key": "test", "label": "Test the receptionist in your browser", "done": tested, "href": "/receptionist?tab=test"},
        {"key": "phone", "label": "Connect a phone line", "done": tel_status == "connected", "href": "/integrations"},
    ]
    return {"onboarding_completed": t.onboarding_completed, "onboarding_step": t.onboarding_step, "items": items}

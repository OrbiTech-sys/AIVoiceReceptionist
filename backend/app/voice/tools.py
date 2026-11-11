"""Validated backend tools the receptionist may call. These are the ONLY side effects the
conversation layer can trigger; each validates its inputs and is bound to one tenant."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from ..db import SessionLocal
from ..errors import AppError
from ..models import (
    Appointment,
    BusinessHours,
    Call,
    HolidayException,
    KnowledgeEntry,
    ReceptionistSettings,
    Service,
    Tenant,
)
from ..security import normalize_phone
from ..services import scheduling
from ..services.knowledge_import import is_speakable
from ..services.notifications import notify
from ..services.scheduling import get_policy
from ..services.timeutil import local_today, zone
from .nlu import When


@dataclass
class SlotOption:
    start: datetime  # UTC
    staff_id: str | None
    staff_name: str | None


@dataclass
class ApptInfo:
    id: str
    service_name: str
    service_id: str
    start: datetime
    staff_id: str | None


@dataclass
class ToolResult:
    ok: bool
    message: str = ""
    code: str = ""
    appointment: ApptInfo | None = None


@dataclass
class TenantSnapshot:
    """Read-only facts for one tenant, loaded at call start. Nothing from other tenants is ever included."""

    tenant_id: str
    name: str
    business_type: str
    timezone: str
    address: str
    directions: str
    phone: str
    website: str
    after_hours_behavior: str
    emergency_policy: str
    ai_disclosure: bool
    recording_enabled: bool
    recording_consent: str
    hours: dict[int, list[tuple[time, time]]]
    holidays: dict[date, tuple[str, list[tuple[time, time]]]]
    services: list[dict]
    kb: list[dict]
    cancellation_policy: str
    receptionist_name: str
    voice_id: str
    speed: float
    style: str
    greeting: str
    closing: str
    transfer_number: str

    @property
    def health(self) -> bool:
        return self.business_type in ("dental", "medical", "wellness")


def load_snapshot(tenant_id: str) -> TenantSnapshot:
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise AppError("Unknown business", code="no_tenant")
        rs = db.get(ReceptionistSettings, tenant_id) or ReceptionistSettings(tenant_id=tenant_id)
        hours: dict[int, list[tuple[time, time]]] = {}
        for h in db.scalars(select(BusinessHours).where(BusinessHours.tenant_id == tenant_id)):
            if h.is_closed or not h.open_time or not h.close_time:
                hours[h.weekday] = []
            elif h.break_start and h.break_end:
                hours[h.weekday] = [(h.open_time, h.break_start), (h.break_end, h.close_time)]
            else:
                hours[h.weekday] = [(h.open_time, h.close_time)]
        today = local_today(zone(t.timezone))
        holidays = {}
        for x in db.scalars(select(HolidayException).where(HolidayException.tenant_id == tenant_id,
                                                           HolidayException.date >= today)):
            holidays[x.date] = (x.label, [] if x.is_closed or not x.open_time else [(x.open_time, x.close_time)])
        services = [{"id": s.id, "name": s.name, "duration": s.duration_min, "price": s.price_text, "description": s.description}
                    for s in db.scalars(select(Service).where(Service.tenant_id == tenant_id, Service.is_active.is_(True)).order_by(Service.name))]
        # Only what can be said to a caller: leftovers from imports (template fields, page headers, staff instructions)
        # are never read out, even if they are still saved.
        kb = [{"id": k.id, "kind": k.kind, "q": k.question, "a": k.answer}
              for k in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.tenant_id == tenant_id, KnowledgeEntry.is_active.is_(True)))
              if is_speakable(k.question, k.answer)]
        policy = get_policy(db, tenant_id)
        db.commit()
        return TenantSnapshot(
            tenant_id=t.id, name=t.name, business_type=t.business_type, timezone=t.timezone, address=t.address,
            directions=t.directions, phone=t.phone, website=t.website, after_hours_behavior=t.after_hours_behavior,
            emergency_policy=t.emergency_policy, ai_disclosure=t.ai_disclosure_enabled,
            recording_enabled=t.recording_enabled, recording_consent=t.recording_consent_message,
            hours=hours, holidays=holidays, services=services, kb=kb,
            cancellation_policy=policy.cancellation_policy_text, receptionist_name=rs.name or "Ava",
            voice_id=rs.voice_id, speed=rs.speed, style=rs.style, greeting=rs.greeting, closing=rs.closing,
            transfer_number=rs.transfer_number,
        )


class ReceptionistTools:
    def __init__(self, tenant_id: str, call_id: str | None):
        self.tenant_id = tenant_id
        self.call_id = call_id

    def _tenant(self, db) -> Tenant:
        t = db.get(Tenant, self.tenant_id)
        assert t is not None
        return t

    def find_slots(self, service_id: str, when: When, limit: int = 3, exclude_appointment_id: str | None = None) -> list[SlotOption]:
        with SessionLocal() as db:
            t = self._tenant(db)
            svc = db.get(Service, service_id)
            if svc is None or svc.tenant_id != t.id or not svc.is_active:
                return []
            tz = zone(t.timezone)
            today = local_today(tz)
            policy = get_policy(db, t.id)
            if when.dates:
                start, end = min(when.dates), max(when.dates)
            else:
                start, end = today, today + timedelta(days=min(policy.max_advance_days, 21))
            slots = scheduling.find_slots(db, t, svc, start, end, exclude_appointment_id=exclude_appointment_id, limit=600)
            db.commit()
        # One option per start time (first staff alphabetically)
        uniq: dict[datetime, scheduling.Slot] = {}
        for s in slots:
            uniq.setdefault(s.start, s)
        cand = list(uniq.values())

        def local(s):
            return s.start.astimezone(tz)

        if when.part_of_day:
            rng = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}[when.part_of_day]
            cand = [s for s in cand if rng[0] <= local(s).hour < rng[1]]
        if when.time is not None:
            exact = [s for s in cand if local(s).time() == when.time]
            if exact:
                return [SlotOption(s.start, s.staff_id, s.staff_name) for s in exact[:1]]
            target = when.time.hour * 60 + when.time.minute
            same_days = cand
            cand = sorted(same_days, key=lambda s: (abs(local(s).hour * 60 + local(s).minute - target), s.start))[:limit]
            cand.sort(key=lambda s: s.start)
            return [SlotOption(s.start, s.staff_id, s.staff_name) for s in cand]
        # Spread options: prefer different times of the first available day, then other days.
        picked: list[scheduling.Slot] = []
        if when.dates and len(when.dates) == 1:
            day = cand
            if len(day) <= limit:
                picked = day
            else:
                idx = [0, len(day) // 2, len(day) - 1]
                picked = [day[i] for i in idx]
        else:
            seen_days = set()
            for s in cand:
                d = local(s).date()
                if d not in seen_days:
                    picked.append(s)
                    seen_days.add(d)
                if len(picked) >= limit:
                    break
        return [SlotOption(s.start, s.staff_id, s.staff_name) for s in picked[:limit]]

    def book(self, service_id: str, slot: SlotOption, name: str, phone: str | None, notes: str = "", email: str = "") -> ToolResult:
        if not name or len(name) > 120:
            return ToolResult(False, "invalid name", "invalid_input")
        # A number in another country's local format can't be filed, but it must not cost the caller their appointment.
        e164 = normalize_phone(phone) if phone and not phone.startswith("0") else None
        if phone and not e164:
            notes = f"{notes} Number given by the caller: {phone}".strip()
        with SessionLocal() as db:
            t = self._tenant(db)
            try:
                appt = scheduling.book_appointment(
                    db, t, service_id=service_id, start=slot.start, customer_name=name, customer_phone=e164, customer_email=email,
                    staff_id=slot.staff_id, source="ai_call", notes=notes, call_id=self.call_id)
            except scheduling.SlotUnavailable as exc:
                return ToolResult(False, exc.message, "slot_unavailable")
            except AppError as exc:
                return ToolResult(False, exc.message, exc.code)
            # Persistence succeeded (committed inside book_appointment) — only now may we confirm.
            if self.call_id:
                call = db.get(Call, self.call_id)
                if call is not None and call.tenant_id == t.id:
                    call.appointment_id = appt.id
                    call.contact_id = appt.contact_id
                    call.caller_name = call.caller_name or name
                    db.commit()
            return ToolResult(True, appointment=ApptInfo(appt.id, appt.service.name, appt.service_id, appt.start_at, appt.staff_id))

    def find_appointments(self, phone: str | None) -> list[ApptInfo]:
        with SessionLocal() as db:
            rows = scheduling.find_upcoming_for_phone(db, self.tenant_id, phone)
            return [ApptInfo(a.id, a.service.name, a.service_id, a.start_at, a.staff_id) for a in rows]

    def reschedule(self, appointment_id: str, slot: SlotOption) -> ToolResult:
        with SessionLocal() as db:
            t = self._tenant(db)
            a = db.get(Appointment, appointment_id)
            if a is None or a.tenant_id != t.id:
                return ToolResult(False, "not found", "not_found")
            try:
                appt = scheduling.reschedule_appointment(db, t, appointment_id, slot.start, by_caller=True)
            except AppError as exc:
                return ToolResult(False, exc.message, exc.code)
            if self.call_id:
                call = db.get(Call, self.call_id)
                if call is not None and call.tenant_id == t.id:
                    call.appointment_id = appt.id
                    call.contact_id = appt.contact_id
                    db.commit()
            return ToolResult(True, appointment=ApptInfo(appt.id, appt.service.name, appt.service_id, appt.start_at, appt.staff_id))

    def cancel(self, appointment_id: str) -> ToolResult:
        with SessionLocal() as db:
            t = self._tenant(db)
            a = db.get(Appointment, appointment_id)
            if a is None or a.tenant_id != t.id:
                return ToolResult(False, "not found", "not_found")
            try:
                appt = scheduling.change_status(db, t, appointment_id, "cancelled", reason="Cancelled by caller", by_caller=True)
            except AppError as exc:
                return ToolResult(False, exc.message, exc.code)
            if self.call_id:
                call = db.get(Call, self.call_id)
                if call is not None and call.tenant_id == t.id:
                    call.appointment_id = appt.id
                    db.commit()
            return ToolResult(True, appointment=ApptInfo(appt.id, appt.service.name, appt.service_id, appt.start_at, appt.staff_id))

    def request_callback(self, name: str, phone: str | None, reason: str, urgent: bool = False) -> ToolResult:
        with SessionLocal() as db:
            t = self._tenant(db)
            who = name or "A caller"
            num = phone or "no number given"
            kind = "urgent_request" if urgent else "callback_requested"
            title = ("Urgent: " if urgent else "Callback requested: ") + who
            notify(db, t, kind, title, f"{reason[:300]} · {num}", link=f"/calls?id={self.call_id}" if self.call_id else "/calls",
                   entity_type="call", entity_id=self.call_id or "")
            if self.call_id:
                call = db.get(Call, self.call_id)
                if call is not None and call.tenant_id == t.id:
                    call.follow_up_status = "needed"
                    if name and not call.caller_name:
                        call.caller_name = name
            db.commit()
        return ToolResult(True)

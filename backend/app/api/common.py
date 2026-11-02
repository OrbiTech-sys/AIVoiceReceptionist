"""Shared serialisers for API responses."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from ..models import Appointment, Call, Contact, Notification, Service, StaffMember, Tenant
from ..security import format_phone
from ..services.timeutil import zone


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def hhmm(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


def parse_hhmm(v: str | None) -> time | None:
    if not v:
        return None
    h, m = v.split(":")[:2]
    return time(int(h), int(m))


def local_iso(dt: datetime | None, tenant: Tenant) -> str | None:
    return dt.astimezone(zone(tenant.timezone)).isoformat() if dt else None


def contact_out(c: Contact | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "id": c.id, "name": c.name, "phone": c.phone_e164, "phone_display": format_phone(c.phone_e164),
        "email": c.email, "notes": c.notes, "follow_up_status": c.follow_up_status,
        "last_interaction_at": iso(c.last_interaction_at), "created_at": iso(c.created_at),
    }


def service_out(s: Service) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "description": s.description, "duration_min": s.duration_min,
            "buffer_min": s.buffer_min, "price_text": s.price_text, "is_active": s.is_active}


def staff_out(s: StaffMember) -> dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "title": s.title, "email": s.email, "is_active": s.is_active,
        "service_ids": [l.service_id for l in s.service_links],
        "availability": [{"weekday": a.weekday, "start": hhmm(a.start_time), "end": hhmm(a.end_time)} for a in s.availability],
    }


def appointment_out(a: Appointment, tenant: Tenant) -> dict[str, Any]:
    return {
        "id": a.id, "status": a.status, "source": a.source,
        "start_at": iso(a.start_at), "end_at": iso(a.end_at),
        "start_local": local_iso(a.start_at, tenant), "end_local": local_iso(a.end_at, tenant),
        "service": {"id": a.service.id, "name": a.service.name, "duration_min": a.service.duration_min},
        "staff": {"id": a.staff.id, "name": a.staff.name} if a.staff else None,
        "contact": contact_out(a.contact), "notes": a.notes, "cancel_reason": a.cancel_reason,
        "call_id": a.call_id, "created_at": iso(a.created_at), "updated_at": iso(a.updated_at),
    }


def call_out(c: Call) -> dict[str, Any]:
    return {
        "id": c.id, "channel": c.channel, "caller_number": c.caller_number,
        "caller_number_display": format_phone(c.caller_number), "caller_name": c.caller_name,
        "contact_id": c.contact_id, "started_at": iso(c.started_at), "ended_at": iso(c.ended_at),
        "duration_sec": c.duration_sec, "status": c.status, "outcome": c.outcome, "intent": c.intent,
        "summary": c.summary, "follow_up_status": c.follow_up_status, "error_detail": c.error_detail,
        "appointment_id": c.appointment_id, "is_demo": c.is_demo, "has_recording": bool(c.recording_path),
    }


def notification_out(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id, "kind": n.kind, "severity": n.severity, "title": n.title, "body": n.body, "link": n.link,
        "entity_type": n.entity_type, "entity_id": n.entity_id, "event_time": iso(n.event_time),
        "created_at": iso(n.created_at), "read": n.read_at is not None, "push_status": n.push_status,
    }

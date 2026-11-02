"""Tenant creation with sensible defaults per business type."""
from __future__ import annotations

import re
import secrets
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BookingPolicy, BusinessHours, Membership, ReceptionistSettings, Service, Tenant
from .notifications import get_prefs

BUSINESS_TYPES = {
    "dental": "Dentist / dental clinic",
    "medical": "Medical clinic",
    "wellness": "Wellness / therapy",
    "salon": "Salon / spa",
    "property": "Property management",
    "other": "Other appointment-based business",
}

TEMPLATE_SERVICES: dict[str, list[tuple[str, int, str]]] = {
    "dental": [("Dental cleaning", 60, ""), ("New patient exam", 60, ""), ("Consultation", 30, "")],
    "medical": [("General consultation", 30, ""), ("Follow-up visit", 20, "")],
    "wellness": [("Initial session", 60, ""), ("Follow-up session", 45, "")],
    "salon": [("Haircut", 45, ""), ("Color", 120, ""), ("Manicure", 45, "")],
    "property": [("Property viewing", 30, ""), ("Maintenance visit", 60, "")],
    "other": [("Appointment", 30, "")],
}

HEALTH_TYPES = {"dental", "medical", "wellness"}

DEFAULT_EMERGENCY_POLICY = (
    "If you are experiencing a medical emergency, please hang up and call your local emergency number "
    "right away. I can't give medical advice, but I can ask a member of our team to call you back."
)


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "business"
    return f"{base}-{secrets.token_hex(3)}"


def create_tenant(db: Session, *, name: str, business_type: str, timezone: str, owner_user_id: str,
                  is_demo: bool = False) -> Tenant:
    btype = business_type if business_type in BUSINESS_TYPES else "other"
    t = Tenant(name=name.strip(), slug=slugify(name), business_type=btype, timezone=timezone, is_demo=is_demo,
               emergency_policy=DEFAULT_EMERGENCY_POLICY if btype in HEALTH_TYPES else
               "If this is an emergency, please call your local emergency number.")
    db.add(t)
    db.flush()
    db.add(Membership(user_id=owner_user_id, tenant_id=t.id, role="owner"))
    for wd in range(7):
        closed = wd >= 5
        db.add(BusinessHours(tenant_id=t.id, weekday=wd, is_closed=closed,
                             open_time=None if closed else time(9, 0), close_time=None if closed else time(17, 0),
                             break_start=None if closed else time(12, 0), break_end=None if closed else time(13, 0)))
    db.add(BookingPolicy(tenant_id=t.id, cancellation_policy_text="Please give us at least 24 hours' notice to cancel or reschedule."))
    db.add(ReceptionistSettings(
        tenant_id=t.id,
        greeting=f"Thank you for calling {t.name}. This is Ava, the virtual assistant. How can I help you today?",
        closing=f"Thanks for calling {t.name}. Have a great day!",
        boundaries="Do not give medical, legal or financial advice. Do not quote prices that are not configured."
        if btype in HEALTH_TYPES else "Do not quote prices that are not configured.",
    ))
    for sname, dur, price in TEMPLATE_SERVICES[btype]:
        db.add(Service(tenant_id=t.id, name=sname, duration_min=dur, price_text=price))
    db.flush()
    get_prefs(db, t.id, owner_user_id)
    return t


def first_membership(db: Session, user_id: str) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at))

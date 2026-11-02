"""Seed a clearly-labelled demo business and a platform administrator for local development.

Usage:  python seed.py            (idempotent; skips if the demo user already exists)
        python seed.py --reset    (deletes and recreates the demo tenant)
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Call,
    CallEvent,
    KnowledgeEntry,
    Membership,
    Service,
    StaffAvailability,
    StaffMember,
    StaffService,
    Tenant,
    User,
)
from app.security import hash_password
from app.services import scheduling
from app.services.tenants import create_tenant
from app.services.timeutil import local_to_utc, local_today, zone

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "DemoOwner2026!"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "PlatformAdmin2026!"
DEMO_TZ = "America/New_York"


def main() -> None:
    if get_settings().env == "production" and "--force" not in sys.argv:
        sys.exit("Refusing to seed demo data in production (pass --force to override).")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == ADMIN_EMAIL)) is None:
            db.add(User(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), full_name="Platform Admin",
                        is_platform_admin=True))
            db.commit()
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user and "--reset" in sys.argv:
            for t in db.scalars(select(Tenant).where(Tenant.is_demo.is_(True))):
                db.delete(t)
            db.delete(user)
            db.commit()
            user = None
        if user:
            print("Demo data already present. Use --reset to recreate.")
            return
        user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), full_name="Dana Demo")
        db.add(user)
        db.flush()
        t = create_tenant(db, name="Bright Smile Dental (Demo)", business_type="dental", timezone=DEMO_TZ,
                          owner_user_id=user.id, is_demo=True)
        t.address = "120 Main Street, Suite 4, Springfield"
        t.directions = "We're on the second floor above the pharmacy. Free parking is behind the building."
        t.phone = "+14155550100"
        t.website = "https://example.com"
        t.onboarding_completed = True
        t.onboarding_step = 7
        for s in db.scalars(select(Service).where(Service.tenant_id == t.id)):
            s.price_text = {"Dental cleaning": "$120", "New patient exam": "$95", "Consultation": "free"}.get(s.name, "")
            s.buffer_min = 10 if s.name == "Dental cleaning" else None
        db.add(Service(tenant_id=t.id, name="Teeth whitening", duration_min=90, price_text="$299"))
        db.flush()
        services = {s.name: s for s in db.scalars(select(Service).where(Service.tenant_id == t.id))}
        dr = StaffMember(tenant_id=t.id, name="Dr. Maya Patel", title="Dentist")
        hy = StaffMember(tenant_id=t.id, name="Sam Lee", title="Hygienist")
        db.add_all([dr, hy])
        db.flush()
        for name in ("New patient exam", "Consultation", "Teeth whitening"):
            db.add(StaffService(staff_id=dr.id, service_id=services[name].id, tenant_id=t.id))
        db.add(StaffService(staff_id=hy.id, service_id=services["Dental cleaning"].id, tenant_id=t.id))
        for wd in range(4):  # Sam works Mon-Thu
            db.add(StaffAvailability(tenant_id=t.id, staff_id=hy.id, weekday=wd, start_time=time(9), end_time=time(17)))
        faqs = [
            ("Do you accept insurance?", "We accept most major dental insurance plans, including Delta Dental and Cigna. "
             "Please bring your insurance card to your visit."),
            ("Do you see children?", "Yes, we see patients of all ages, including children from age three."),
            ("Where can I park?", "Free parking is available behind the building."),
            ("What should I bring to my first visit?", "Please bring a photo ID, your insurance card, and a list of any "
             "medications you take."),
        ]
        for q, a in faqs:
            db.add(KnowledgeEntry(tenant_id=t.id, kind="faq", question=q, answer=a))
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if admin is not None:  # convenience: lets the platform admin open the demo dashboard
            db.add(Membership(user_id=admin.id, tenant_id=t.id, role="staff"))
        db.commit()

        # A few demo bookings over the next days (flagged via the demo tenant).
        tz = zone(DEMO_TZ)
        today = local_today(tz)
        examples = [("Alex Rivera", "+14155550111", "Dental cleaning", 1, time(10)),
                    ("Jordan Kim", "+14155550122", "New patient exam", 2, time(14)),
                    ("Priya Shah", "+14155550133", "Consultation", 3, time(11))]
        for name, phone, svc, days, at in examples:
            d = today + timedelta(days=days)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            start = local_to_utc(d, at, tz)
            try:
                scheduling.book_appointment(db, t, service_id=services[svc].id, start=start, customer_name=name,
                                            customer_phone=phone, source="dashboard", notes="Demo booking", enforce_rules=False)
            except scheduling.SlotUnavailable:
                pass
        call = Call(tenant_id=t.id, channel="phone", caller_number="+14155550144", caller_name="Chris Morgan",
                    started_at=datetime.now(timezone.utc) - timedelta(hours=3), status="completed", outcome="answered",
                    duration_sec=74, intent="callback", summary="Chris Morgan asked about whitening prices; requested a callback.",
                    follow_up_status="needed", is_demo=True)
        call.ended_at = call.started_at + timedelta(seconds=74)
        db.add(call)
        db.flush()
        for kind, text in [("agent", "Thank you for calling Bright Smile Dental. I'm Ava, a virtual assistant. How can I help you today?"),
                           ("caller", "Hi, how much is teeth whitening?"),
                           ("agent", "A teeth whitening is $299. Is there anything else I can help with?"),
                           ("caller", "Can someone call me back about payment plans?"),
                           ("agent", "Of course. I'll have a member of our team call you back.")]:
            db.add(CallEvent(call_id=call.id, tenant_id=t.id, kind=kind, text=text))
        db.commit()
    print(f"Seeded demo business. Sign in with {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"Platform admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()

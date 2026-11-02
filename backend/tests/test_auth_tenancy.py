from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import ApiClient, signup

from app.db import SessionLocal
from app.models import Tenant


def test_signup_login_logout_and_me():
    c = signup()
    me = c.get("/api/auth/me").json()
    assert me["user"]["email"] == "owner@example.com"
    assert me["role"] == "owner"
    assert me["tenant"]["name"] == "Test Dental"

    c2 = ApiClient()
    bad = c2.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-password1A"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"
    ok = c2.post("/api/auth/login", json={"email": "OWNER@example.com", "password": "Sup3rSecret!"})
    assert ok.status_code == 200
    assert c2.post("/api/auth/logout").status_code == 200
    assert c2.get("/api/auth/me").status_code == 401


def test_weak_password_and_duplicate_email_rejected():
    c = ApiClient()
    r = c.post("/api/auth/signup", json={"email": "a@example.com", "password": "short", "full_name": "A",
                                         "business_name": "Biz", "business_type": "other", "timezone": "UTC"})
    assert r.status_code == 422
    signup(email="dupe@example.com")
    r = ApiClient().post("/api/auth/signup", json={"email": "dupe@example.com", "password": "Sup3rSecret!", "full_name": "B",
                                                   "business_name": "Biz", "business_type": "other", "timezone": "UTC"})
    assert r.status_code == 409


def test_csrf_required_for_mutations(owner):
    r = owner.c.patch("/api/business", json={"address": "1 Main St"})  # no CSRF header
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    assert owner.patch("/api/business", json={"address": "1 Main St"}).status_code == 200


def test_login_rate_limited():
    c = ApiClient()
    codes = [c.post("/api/auth/login", json={"email": "x@example.com", "password": "nope-nope-1A"}).status_code for _ in range(10)]
    assert 429 in codes


def test_tenant_isolation(owner, other_owner):
    svc = owner.get("/api/services").json()[0]
    start = _next_slot(owner, svc["id"])
    appt = owner.post("/api/appointments", json={"service_id": svc["id"], "start_at": start,
                                                 "customer_name": "Alice", "customer_phone": "+14155550101"}).json()
    contact_id = appt["contact"]["id"]

    # The other business cannot see or modify any of it.
    assert other_owner.get(f"/api/appointments/{appt['id']}").status_code == 404
    assert other_owner.get(f"/api/contacts/{contact_id}").status_code == 404
    assert other_owner.post(f"/api/appointments/{appt['id']}/status", json={"status": "cancelled"}).status_code == 404
    assert other_owner.get("/api/appointments").json()["total"] == 0
    assert other_owner.get("/api/contacts").json()["total"] == 0
    # Booking with another tenant's service id fails.
    r = other_owner.post("/api/appointments", json={"service_id": svc["id"], "start_at": start, "customer_name": "Mallory"})
    assert r.status_code == 404
    # Switching to a tenant without membership is refused (tenant id from the browser is not trusted).
    with SessionLocal() as db:
        t1 = db.query(Tenant).filter(Tenant.name == "Test Dental").one().id
    assert other_owner.post("/api/auth/switch-tenant", json={"tenant_id": t1}).status_code == 403
    # Notifications are per tenant and per user.
    assert other_owner.get("/api/notifications").json()["total"] == 0
    assert owner.get("/api/notifications").json()["total"] >= 1


def test_staff_role_cannot_change_owner_settings(owner):
    from app.models import Membership, User
    from app.security import hash_password

    with SessionLocal() as db:
        t = db.query(Tenant).one()
        u = User(email="staff@example.com", password_hash=hash_password("Sup3rSecret!"), full_name="Staff")
        db.add(u)
        db.flush()
        db.add(Membership(user_id=u.id, tenant_id=t.id, role="staff"))
        db.commit()
    staff = ApiClient()
    assert staff.post("/api/auth/login", json={"email": "staff@example.com", "password": "Sup3rSecret!"}).status_code == 200
    assert staff.patch("/api/business", json={"name": "Hijacked"}).status_code == 403
    assert staff.get("/api/appointments").status_code == 200  # staff can work with appointments


def _next_slot(client, service_id: str) -> str:
    start = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    slots = client.get(f"/api/appointments/availability?service_id={service_id}&start={start}&days=14").json()["slots"]
    assert slots, "expected availability"
    return slots[0]["start_at"]

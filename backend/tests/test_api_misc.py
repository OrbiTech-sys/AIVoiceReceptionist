from __future__ import annotations

import io
import struct
import wave

from app.db import SessionLocal
from app.models import Notification, TelephonyIntegration


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<400h", *([0] * 400)))
    return buf.getvalue()


def test_business_hours_validation(owner):
    days = owner.get("/api/business/hours").json()["days"]
    days[0] = {**days[0], "is_closed": False, "open_time": "17:00", "close_time": "09:00"}
    r = owner.put("/api/business/hours", json={"days": days})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_hours"
    days[0] = {**days[0], "open_time": "08:00", "close_time": "18:00", "break_start": "19:00", "break_end": "19:30"}
    assert owner.put("/api/business/hours", json={"days": days}).status_code == 400
    days[0] = {**days[0], "break_start": None, "break_end": None}
    assert owner.put("/api/business/hours", json={"days": days}).status_code == 200
    assert owner.get("/api/business/hours").json()["days"][0]["open_time"] == "08:00"


def test_invalid_timezone_and_privacy_consent(owner):
    assert owner.patch("/api/business", json={"timezone": "Mars/Base"}).status_code == 422
    r = owner.put("/api/business/privacy", json={"recording_enabled": True, "recording_consent_message": "",
                                                  "ai_disclosure_enabled": True, "transcript_retention_days": 30,
                                                  "recording_retention_days": 30})
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"


def test_sound_upload_validation(owner):
    ok = owner.post("/api/notifications/sounds", files={"file": ("ding.wav", _wav_bytes(), "audio/wav")})
    assert ok.status_code == 201, ok.text
    sid = ok.json()["id"]
    # A script disguised with an audio content type is rejected by magic-byte sniffing.
    bad = owner.post("/api/notifications/sounds", files={"file": ("evil.mp3", b"<script>alert(1)</script>" * 10, "audio/mpeg")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_file"
    big = owner.post("/api/notifications/sounds", files={"file": ("big.wav", _wav_bytes() + b"\0" * 1_100_000, "audio/wav")})
    assert big.status_code == 400 and big.json()["error"]["code"] == "file_too_large"
    # The uploaded sound can be selected as the reminder sound and is served back with nosniff.
    prefs = owner.get("/api/notifications/preferences").json()
    prefs.update(routine_sound=f"upload:{sid}", reminder_offsets=[15, 60])
    for k in ("email_available", "builtin_sounds", "custom_sounds", "push_subscriptions"):
        prefs.pop(k)
    r = owner.put("/api/notifications/preferences", json=prefs)
    assert r.status_code == 200 and r.json()["routine_sound"] == f"upload:{sid}"
    f = owner.get(f"/api/notifications/sounds/{sid}")
    assert f.status_code == 200 and f.headers["x-content-type-options"] == "nosniff"
    prefs["routine_sound"] = "upload:doesnotexist"
    assert owner.put("/api/notifications/preferences", json=prefs).status_code == 400


def test_knowledge_import(owner):
    csv_data = b"question,answer\nDo you take insurance?,Yes we accept most plans.\nWhere do I park?,Behind the building.\n"
    r = owner.post("/api/knowledge/import", files={"file": ("faq.csv", csv_data, "text/csv")})
    assert r.status_code == 200 and r.json()["imported"] == 2
    bad = owner.post("/api/knowledge/import", files={"file": ("faq.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert bad.status_code == 400


def test_notification_read_flow(owner):
    owner.post("/api/notifications/test")
    feed = owner.get("/api/notifications").json()
    assert feed["unread"] == 1
    nid = feed["items"][0]["id"]
    assert owner.post(f"/api/notifications/{nid}/read").json()["read"] is True
    assert owner.get("/api/notifications").json()["unread"] == 0


def test_event_preferences_suppress_notifications(owner):
    prefs = owner.get("/api/notifications/preferences").json()
    prefs["events"]["appointment_booked"] = False
    for k in ("email_available", "builtin_sounds", "custom_sounds", "push_subscriptions"):
        prefs.pop(k)
    assert owner.put("/api/notifications/preferences", json=prefs).status_code == 200
    svc = owner.get("/api/services").json()[0]
    from test_auth_tenancy import _next_slot

    owner.post("/api/appointments", json={"service_id": svc["id"], "start_at": _next_slot(owner, svc["id"]), "customer_name": "Zed"})
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.kind == "appointment_booked").count() == 0


def test_telephony_config_secrets_and_dialplan_auth(owner, other_owner):
    r = owner.put("/api/telephony", json={"did_number": "+1 415 555 0100", "sip_host": "sip.example.net",
                                          "sip_username": "acct1", "sip_password": "s3cret-pass"})
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["inbound_token"]
    assert body["status"] == "pending_validation"  # never "connected" before a real call
    assert "s3cret-pass" not in r.text
    again = owner.get("/api/telephony").json()
    assert "inbound_token" not in again and again["has_password"] is True and "sip_password" not in again
    with SessionLocal() as db:
        stored = db.query(TelephonyIntegration).one().sip_password_enc
        assert stored and "s3cret-pass" not in stored
    cfg = owner.get("/api/telephony/asterisk-config")
    assert cfg.status_code == 200 and "s3cret-pass" not in cfg.text and "<SIP_PASSWORD>" in cfg.text
    # Another business cannot claim the same number.
    r2 = other_owner.put("/api/telephony", json={"did_number": "+14155550100", "sip_host": "sip.example.net",
                                                 "sip_username": "x", "sip_password": "y"})
    assert r2.status_code == 409
    # Dialplan callbacks require the inbound token.
    uuid = "12345678-1234-1234-1234-123456789abc"
    assert owner.c.get(f"/api/telephony/asterisk/precall?uuid={uuid}&did=%2B14155550100&token=wrong").status_code == 401
    ok = owner.c.get(f"/api/telephony/asterisk/precall?uuid={uuid}&did=%2B14155550100&caller=%2B14155550999&token={token}")
    assert ok.status_code == 200 and ok.text == "ok"
    assert owner.c.get(f"/api/telephony/asterisk/next-action?uuid={uuid}&token={token}").text == "hangup"
    v = owner.post("/api/telephony/validate").json()
    assert v["problems"]  # listener disabled in tests and no test call yet


def test_health_and_security_headers(owner):
    r = owner.get("/api/health")
    assert r.json()["database"] is True
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"

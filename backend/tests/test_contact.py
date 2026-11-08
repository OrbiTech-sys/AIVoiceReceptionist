from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import ContactMessage, User
from conftest import ApiClient, signup

GOOD = {"name": "Ana Ruiz", "email": "ana@maplestreet.example.com", "business": "Maple Street", "topic": "pricing",
        "message": "We get about forty calls a day. What would it cost?"}


def _messages() -> list[ContactMessage]:
    with Session(get_engine()) as db:
        return list(db.scalars(select(ContactMessage)).all())


def _make_admin(email: str) -> None:
    with Session(get_engine()) as db:
        db.scalar(select(User).where(User.email == email)).is_platform_admin = True
        db.commit()


def test_contact_form_is_public_and_stores_the_message():
    r = ApiClient().post("/api/contact", json=GOOD)
    assert r.status_code == 201, r.text
    rows = _messages()
    assert len(rows) == 1
    assert rows[0].email == GOOD["email"] and rows[0].topic == "pricing" and rows[0].handled_at is None


def test_contact_form_rejects_bad_input():
    c = ApiClient()
    assert c.post("/api/contact", json={**GOOD, "email": "not-an-email"}).status_code == 422
    assert c.post("/api/contact", json={**GOOD, "message": "short"}).status_code == 422
    assert c.post("/api/contact", json={**GOOD, "name": "   "}).status_code == 422
    assert _messages() == []


def test_unknown_topic_is_filed_as_other():
    assert ApiClient().post("/api/contact", json={**GOOD, "topic": "<script>"}).status_code == 201
    assert _messages()[0].topic == "other"


def test_honeypot_is_accepted_but_not_stored():
    assert ApiClient().post("/api/contact", json={**GOOD, "website": "http://spam.example.com"}).status_code == 201
    assert _messages() == []


def test_contact_form_is_rate_limited():
    c = ApiClient()
    for _ in range(5):
        assert c.post("/api/contact", json=GOOD).status_code == 201
    assert c.post("/api/contact", json=GOOD).status_code == 429
    assert len(_messages()) == 5


def test_only_platform_admins_can_read_the_inbox(owner):
    ApiClient().post("/api/contact", json=GOOD)
    assert ApiClient().get("/api/admin/contact-messages").status_code == 401
    assert owner.get("/api/admin/contact-messages").status_code == 403


def test_admin_can_handle_and_delete_a_message():
    admin = signup(email="admin@example.com", business="Platform")
    _make_admin("admin@example.com")
    ApiClient().post("/api/contact", json=GOOD)
    listed = admin.get("/api/admin/contact-messages").json()
    assert len(listed) == 1 and listed[0]["handled"] is False and listed[0]["topic"] == "Pricing"
    mid = listed[0]["id"]
    assert admin.patch(f"/api/admin/contact-messages/{mid}", json={"handled": True}).json()["handled"] is True
    assert admin.delete(f"/api/admin/contact-messages/{mid}").status_code == 204
    assert _messages() == []
    assert admin.delete(f"/api/admin/contact-messages/{mid}").status_code == 404


def test_migration_creates_the_table(tmp_path, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{(tmp_path / 'mig.db').as_posix()}")
    backend = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")
    import sqlite3

    tables = {r[0] for r in sqlite3.connect(tmp_path / "mig.db").execute("select name from sqlite_master where type='table'")}
    assert "contact_messages" in tables

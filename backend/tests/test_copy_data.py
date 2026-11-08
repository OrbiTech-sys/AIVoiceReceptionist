"""Copying data from one database to another (local SQLite to Supabase)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import make_engine  # noqa: E402
from app.models import KnowledgeEntry, Tenant  # noqa: E402
import copy_data  # noqa: E402


def _migrated(tmp_path, name):
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    import os

    old = os.environ.get("VR_DATABASE_URL")
    os.environ["VR_DATABASE_URL"] = url
    try:
        from app.config import get_settings

        get_settings.cache_clear()
        command.upgrade(cfg, "head")
    finally:
        os.environ["VR_DATABASE_URL"] = old or ""
        if not old:
            os.environ.pop("VR_DATABASE_URL", None)
        get_settings.cache_clear()
    return url


def test_knowledge_and_its_business_are_copied_once_and_never_duplicated(tmp_path):
    src, dst = _migrated(tmp_path, "a.db"), _migrated(tmp_path, "b.db")
    from sqlalchemy.orm import Session

    with Session(make_engine(src)) as db:
        t = Tenant(name="Bright Smile", slug="bright-smile", timezone="America/New_York")
        db.add(t)
        db.flush()
        db.add_all([KnowledgeEntry(tenant_id=t.id, kind="general", question="Insurance", answer="We accept Delta Dental."),
                    KnowledgeEntry(tenant_id=t.id, kind="faq", question="Parking", answer="Behind the building.")])
        db.commit()

    lines: list[str] = []
    first = copy_data.copy(src, dst, out=lines.append)
    assert first["tenants"] == 1 and first["knowledge_entries"] == 2
    with Session(make_engine(dst)) as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeEntry)) == 2
        assert db.scalar(select(KnowledgeEntry.answer).where(KnowledgeEntry.question == "Insurance")) == "We accept Delta Dental."

    again = copy_data.copy(src, dst, out=lines.append)  # nothing is added twice
    assert again == {} and any("skipped" in ln for ln in lines)
    with Session(make_engine(dst)) as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeEntry)) == 2


def test_copying_a_database_onto_itself_is_refused(tmp_path):
    url = _migrated(tmp_path, "c.db")
    with pytest.raises(SystemExit):
        copy_data.copy(url, url)

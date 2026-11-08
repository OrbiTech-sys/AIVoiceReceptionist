"""Database addresses pasted from Supabase and similar services are repaired instead of failing confusingly."""
from __future__ import annotations

import pytest
from sqlalchemy import make_url

from app.config import normalize_database_url

HOST = "aws-0-us-east-1.pooler.supabase.com"


def parts(url: str):
    u = make_url(url)
    return u.drivername, u.username, u.password, u.host, u.port, dict(u.query)


def test_a_password_with_at_and_dollar_signs_stays_the_password():
    fixed = normalize_database_url(f'"postgresql://postgres.abc:pa$$@word@{HOST}:6543/postgres"')
    # the password is kept whole, and the host is the real host, not the end of the password
    assert parts(fixed) == ("postgresql+psycopg", "postgres.abc", "pa$$@word", HOST, 5432, {"sslmode": "require"})


def test_an_already_correct_address_is_left_alone():
    url = f"postgresql+psycopg://postgres.abc:pa%24%24%40word@{HOST}:5432/postgres?sslmode=require"
    assert normalize_database_url(url) == url


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_the_driver_is_named(scheme):
    assert normalize_database_url(f"{scheme}://u:p@db.example.com:5432/app").startswith("postgresql+psycopg://")


def test_ssl_is_added_only_for_supabase_and_the_transaction_pooler_is_swapped_for_the_session_pooler():
    assert "sslmode" not in normalize_database_url("postgresql://u:p@db.internal:5432/app")
    assert parts(normalize_database_url(f"postgresql://u:p@{HOST}:6543/postgres"))[4:] == (5432, {"sslmode": "require"})
    assert parts(normalize_database_url("postgresql://u:p@db.abcdef.supabase.co:5432/postgres?application_name=x"))[5] == {
        "application_name": "x", "sslmode": "require"}


@pytest.mark.parametrize("url", ["", "sqlite:///C:/data/app.db", "sqlite:////tmp/x.db", "not a url"])
def test_other_values_are_not_touched(url):
    assert normalize_database_url(url) == url

"""Security checks that apply to the whole API, so a new endpoint can't quietly skip them."""
from __future__ import annotations

import re

import pytest
from conftest import ApiClient  # noqa: F401  (fixtures come from conftest)
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main
from app.api import appointments, auth, business, catalog, notifications, receptionist, records, system, telephony
from app.config import Settings, get_settings
from app.main import app as fastapi_app
from app.services import fish_voices

MODULES = (appointments, auth, business, catalog, notifications, receptionist, records, system, telephony)

# Everything reachable without a login. A new public endpoint must be added here on purpose, after thinking about abuse.
PUBLIC = {
    ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/signup"),
    ("GET", "/api/business/meta"), ("GET", "/api/health"),
    ("GET", "/api/telephony/asterisk/precall"), ("GET", "/api/telephony/asterisk/next-action"),
}


def _routes() -> list[tuple[str, str]]:
    out = []
    for mod in MODULES:
        for r in mod.router.routes:
            if isinstance(r, APIRoute):
                out += [(m, r.path) for m in sorted(r.methods - {"HEAD", "OPTIONS"})]
    return out


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_the_list_of_endpoints_open_without_a_login_is_exactly_the_reviewed_one():
    # Routes whose handler checks a login/role dependency are not public: find the ones that have none.
    from app.deps import require_auth

    def needs_login(route: APIRoute) -> bool:
        seen = []

        def walk(d):
            for sub in d.dependencies:
                seen.append(sub.call)
                walk(sub)

        walk(route.dependant)
        return require_auth in seen

    open_routes = {(m, r.path) for mod in MODULES for r in mod.router.routes if isinstance(r, APIRoute) and not needs_login(r)
                   for m in r.methods - {"HEAD", "OPTIONS"}}
    assert open_routes == PUBLIC, f"unexpected public endpoints: {sorted(open_routes ^ PUBLIC)}"


@pytest.mark.parametrize("method, path", [(m, p) for m, p in _routes() if (m, p) not in PUBLIC])
def test_every_protected_endpoint_refuses_a_visitor_who_is_not_signed_in(method, path):
    r = TestClient(fastapi_app, base_url="http://localhost:3000").request(method, _url(path))
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize("method, path", [(m, p) for m, p in _routes() if (m, p) not in PUBLIC and m != "GET"])
def test_every_state_changing_endpoint_refuses_a_signed_in_request_without_the_csrf_token(owner, method, path):
    r = owner.c.request(method, _url(path))  # the cookie is sent, the x-csrf-token header is not
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed", (method, path, r.status_code, r.text[:120])


def test_a_wrong_csrf_token_is_refused_too(owner):
    r = owner.c.post("/api/notifications/read-all", headers={"x-csrf-token": "not-the-token"})
    assert r.status_code == 403


def test_the_live_call_websocket_refuses_other_sites_and_visitors_without_a_login(owner):
    c = TestClient(fastapi_app, base_url="http://localhost:3000")
    with pytest.raises(WebSocketDisconnect) as e:  # no session at all
        with c.websocket_connect("/api/receptionist/ws", headers={"origin": "http://localhost:3000"}):
            pass
    assert e.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as e:  # a signed-in browser, but on another site
        with owner.c.websocket_connect("/api/receptionist/ws", headers={"origin": "https://evil.example"}):
            pass
    assert e.value.code == 4403


# ------------------------------------------------------------------ headers and cookies
def test_api_answers_carry_the_security_headers():
    r = TestClient(fastapi_app).get("/api/health")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store"
    assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert h["cross-origin-opener-policy"] == "same-origin" and "strict-origin" in h["referrer-policy"]
    assert "strict-transport-security" not in h  # only in production: over plain HTTP it would do nothing


def test_the_session_cookie_cannot_be_read_by_scripts_and_the_csrf_cookie_can(owner):
    set_cookie = TestClient(fastapi_app, base_url="http://localhost:3000").post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "Sup3rSecret!"}).headers.get_list("set-cookie")
    session = next(c for c in set_cookie if c.startswith("vr_session="))
    csrf = next(c for c in set_cookie if c.startswith("vr_csrf="))
    assert "httponly" in session.lower() and "samesite=lax" in session.lower()
    assert "httponly" not in csrf.lower()  # the page has to read it to send it back


def test_in_production_cookies_are_secure_whatever_the_setting_says_and_hsts_is_sent(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "env", "production")
    monkeypatch.setattr(s, "cookie_secure", False)
    assert s.secure_cookies is True
    client = TestClient(fastapi_app, base_url="https://localhost:3000")
    client.post("/api/auth/signup", json={"email": "prod@example.com", "password": "Sup3rSecret!", "full_name": "P",
                                          "business_name": "Prod Clinic", "business_type": "dental", "timezone": "UTC"})
    r = client.post("/api/auth/login", json={"email": "prod@example.com", "password": "Sup3rSecret!"})
    assert all("secure" in c.lower() for c in r.headers.get_list("set-cookie"))
    assert "max-age=63072000" in r.headers["strict-transport-security"]
    assert Settings(env="development", cookie_secure=False, secret_key="x").secure_cookies is False


def test_api_text_is_served_as_json_so_stored_markup_is_never_run(owner):
    owner.post("/api/contacts", json={"name": "<script>alert(1)</script>", "phone": "+14155550100"})
    r = owner.get("/api/contacts")
    assert r.headers["content-type"].startswith("application/json") and r.headers["x-content-type-options"] == "nosniff"
    assert "<script>" in r.text  # stored as text, and escaped by the page when shown


# ------------------------------------------------------------------ small guards
@pytest.mark.parametrize("url, ok", [
    ("https://platform.r2.fish.audio/task/x.mp3", True),
    ("http://platform.r2.fish.audio/task/x.mp3", False),
    ("https://fish.audio.evil.example/x.mp3", False),
    ("https://evil.example/platform.r2.fish.audio.mp3", False),
    ("https://user:pw@platform.r2.fish.audio/x.mp3", False),
    ("javascript:alert(1)", False),
    (None, False),
])
def test_only_fish_audios_own_https_clips_are_offered_for_playback(url, ok):
    assert (fish_voices._safe_sample_url(url) != "") is ok

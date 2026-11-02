from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Configure an isolated environment BEFORE the app is imported.
_TMP = Path(tempfile.mkdtemp(prefix="voicerep-test-"))
os.environ.update({
    "VR_ENV": "test",
    "VR_DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "VR_DATA_DIR": str(_TMP / "data"),
    "VR_MODELS_DIR": str(_TMP / "models"),  # empty: voice engines report "not installed"
    "VR_SECRET_KEY": "test-secret-key-for-unit-tests-only",
    "VR_RUN_WORKERS": "false",
    "VR_AUDIOSOCKET_ENABLED": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.ratelimit import limiter  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    yield


class ApiClient:
    """TestClient wrapper that sends the CSRF header like the browser does."""

    def __init__(self) -> None:
        self.c = TestClient(app, base_url="http://localhost:3000")
        self.csrf = ""

    def _h(self, method: str) -> dict:
        return {"x-csrf-token": self.csrf} if method != "GET" else {}

    def req(self, method: str, path: str, **kw):
        r = self.c.request(method, path, headers=self._h(method), **kw)
        if path.startswith("/api/auth/") and r.status_code < 300 and isinstance(r.json(), dict) and "csrf_token" in r.json():
            self.csrf = r.json()["csrf_token"]
        return r

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, **kw):
        return self.req("POST", path, **kw)

    def put(self, path, **kw):
        return self.req("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.req("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.req("DELETE", path, **kw)


def signup(email="owner@example.com", business="Test Dental", btype="dental", tz="America/New_York") -> ApiClient:
    c = ApiClient()
    r = c.post("/api/auth/signup", json={"email": email, "password": "Sup3rSecret!", "full_name": "Owner",
                                         "business_name": business, "business_type": btype, "timezone": tz})
    assert r.status_code == 201, r.text
    return c


@pytest.fixture
def owner() -> ApiClient:
    return signup()


@pytest.fixture
def other_owner() -> ApiClient:
    return signup(email="other@example.com", business="Other Salon", btype="salon", tz="Europe/London")

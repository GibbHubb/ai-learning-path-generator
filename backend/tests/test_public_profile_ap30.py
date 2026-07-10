"""AP30 — opt-in public profile.

Verifies the owner visibility toggle requires auth, the public stats endpoint
returns 200 for an opted-in profile and 404 for a private / missing one, and
that the public payload carries no email/PII.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_ap30.db"
os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import User  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_module._magic_link_requests.clear()
    yield


@pytest.fixture
def captured_tokens(monkeypatch):
    import secrets as secrets_mod
    captured = []
    real = secrets_mod.token_urlsafe

    def _capture(*args, **kwargs):
        token = real(*args, **kwargs)
        captured.append(token)
        return token

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", _capture)
    return captured


def _sign_in(client, captured_tokens, email):
    client.post("/api/auth/request-link", json={"email": email})
    token = captured_tokens[-1]
    res = client.post("/api/auth/verify", json={"token": token})
    assert res.status_code == 200, res.text


def _user_id(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).first().id
    finally:
        db.close()


def test_visibility_toggle_requires_auth():
    anon = TestClient(app)
    res = anon.patch("/api/me/profile/visibility", json={"is_public_profile": True})
    assert res.status_code == 401


def test_public_stats_404_when_private(captured_tokens):
    client = TestClient(app)
    _sign_in(client, captured_tokens, "priv@example.com")
    uid = _user_id("priv@example.com")
    # Default is private → public endpoint 404s.
    anon = TestClient(app)
    assert anon.get(f"/api/u/{uid}/stats").status_code == 404


def test_public_stats_200_after_opt_in_and_no_pii(captured_tokens):
    client = TestClient(app)
    _sign_in(client, captured_tokens, "pub@example.com")
    uid = _user_id("pub@example.com")

    toggle = client.patch("/api/me/profile/visibility", json={"is_public_profile": True})
    assert toggle.status_code == 200
    assert toggle.json()["is_public_profile"] is True

    anon = TestClient(app)  # no session cookie
    res = anon.get(f"/api/u/{uid}/stats")
    assert res.status_code == 200
    body = res.json()
    # Shape matches /me/stats and carries no email/PII.
    for key in ("total_xp", "best_streak", "completed_paths", "total_paths", "earned_badges", "badges"):
        assert key in body
    assert "email" not in body
    assert "pub@example.com" not in res.text


def test_public_stats_404_for_missing_user():
    anon = TestClient(app)
    assert anon.get("/api/u/999999/stats").status_code == 404


def test_toggle_back_to_private_404s(captured_tokens):
    client = TestClient(app)
    _sign_in(client, captured_tokens, "flip@example.com")
    uid = _user_id("flip@example.com")
    client.patch("/api/me/profile/visibility", json={"is_public_profile": True})
    anon = TestClient(app)
    assert anon.get(f"/api/u/{uid}/stats").status_code == 200
    client.patch("/api/me/profile/visibility", json={"is_public_profile": False})
    assert anon.get(f"/api/u/{uid}/stats").status_code == 404

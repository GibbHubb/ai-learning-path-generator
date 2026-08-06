"""AP9 — auth flow tests.

Stubs out actual email send by exercising the in-memory `_send_magic_link_email`
side effect via env var (no RESEND_API_KEY → console-log fallback). The link
URL is captured from the most recent `MagicLink` row to drive verification.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure the resend client falls back to console-log instead of HTTP.
os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, MagicLink, Session, User  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_module._magic_link_requests.clear()  # reset rate-limit bucket
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _request_link(client, email):
    res = client.post("/api/auth/request-link", json={"email": email})
    assert res.status_code == 200, res.text
    return res


def _latest_token_for(email):
    """Read the raw MagicLink row to recover the test token."""
    # We can't reverse the hash; instead, monkey-patch the request flow to
    # capture the raw token. Simpler: re-implement here by calling the same
    # internals. But the cleanest is to emit the token via a side channel —
    # in this test harness we skip Resend and look at the most recent row's
    # token by patching `secrets.token_urlsafe`.
    raise NotImplementedError  # see captured_token fixture


@pytest.fixture
def captured_tokens(monkeypatch):
    """Patch the token generator to capture every issued token in order."""
    import secrets as secrets_mod
    captured = []
    real = secrets_mod.token_urlsafe

    def _capture(*args, **kwargs):
        token = real(*args, **kwargs)
        captured.append(token)
        return token

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", _capture)
    return captured


def test_request_link_creates_magic_link_row(client, captured_tokens):
    _request_link(client, "alice@example.com")
    db = SessionLocal()
    try:
        rows = db.query(MagicLink).all()
        assert len(rows) == 1
        assert rows[0].email == "alice@example.com"
    finally:
        db.close()
    assert len(captured_tokens) == 1


def test_verify_token_creates_user_and_session(client, captured_tokens):
    _request_link(client, "bob@example.com")
    token = captured_tokens[-1]
    res = client.post("/api/auth/verify", json={"token": token})
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "bob@example.com"
    # Session cookie set
    assert "ap_session" in client.cookies
    # /auth/me returns the user
    me = client.get("/api/auth/me").json()
    assert me["email"] == "bob@example.com"


def test_verify_rejects_used_token(client, captured_tokens):
    _request_link(client, "carol@example.com")
    token = captured_tokens[-1]
    client.post("/api/auth/verify", json={"token": token})
    # Second use must fail
    second = client.post("/api/auth/verify", json={"token": token})
    assert second.status_code == 400


def test_verify_rejects_invalid_token(client):
    res = client.post("/api/auth/verify", json={"token": "totally-bogus"})
    assert res.status_code == 400


def test_logout_clears_session(client, captured_tokens):
    _request_link(client, "dave@example.com")
    token = captured_tokens[-1]
    client.post("/api/auth/verify", json={"token": token})
    assert client.get("/api/auth/me").json() is not None

    client.post("/api/auth/logout")
    me = client.get("/api/auth/me").json()
    assert me is None


def test_rate_limit_blocks_burst(client):
    for _ in range(auth_module.MAGIC_LINK_RATE_LIMIT):
        client.post("/api/auth/request-link", json={"email": "ed@example.com"})
    res = client.post("/api/auth/request-link", json={"email": "ed@example.com"})
    assert res.status_code == 429


def test_paths_me_requires_auth(client):
    res = client.get("/api/paths/me")
    assert res.status_code == 401


def test_paths_me_returns_only_user_paths(client, captured_tokens):
    # Sign in as user1 and create a path
    _request_link(client, "u1@example.com")
    client.post("/api/auth/verify", json={"token": captured_tokens[-1]})

    db = SessionLocal()
    try:
        u1 = db.query(User).filter(User.email == "u1@example.com").first()
        # Direct insert two paths — one for u1, one for someone else
        db.add(LearningPath(
            title="u1's path", description="x", experience_level="beginner",
            time_commitment="5h", user_id=u1.id,
        ))
        db.add(LearningPath(
            title="u2's path", description="x", experience_level="beginner",
            time_commitment="5h", user_id=u1.id + 9999,  # not u1
        ))
        db.commit()
    finally:
        db.close()

    res = client.get("/api/paths/me")
    assert res.status_code == 200
    titles = [p["title"] for p in res.json()]
    assert titles == ["u1's path"]


def test_anonymous_path_claimed_on_verify(client, captured_tokens, monkeypatch):
    """Generate a path while anonymous, then sign in — the path should attach."""
    # Stub the AI generation so we don't hit OpenAI
    import routes as routes_mod
    monkeypatch.setattr(routes_mod, "generate_learning_path", lambda *a, **kw: {
        "path_title": "Anon Path", "path_description": "x",
        "category": "Programming",
        "milestones": [{"title": "M1", "description": "x", "estimated_hours": 1.0, "resources": []}],
    })

    # Create as anonymous (no session cookie) — backend sets ap_anon_id
    res = client.post("/api/generate", json={
        "goal": "Test", "experience_level": "beginner", "time_commitment": "5h",
    })
    assert res.status_code == 200, res.text
    assert "ap_anon_id" in client.cookies

    # Now sign in
    _request_link(client, "claim@example.com")
    client.post("/api/auth/verify", json={"token": captured_tokens[-1]})

    # /paths/me should include the anonymously-created path
    res = client.get("/api/paths/me")
    titles = [p["title"] for p in res.json()]
    assert "Anon Path" in titles

"""AP32 — the five mutating routes that took no auth at all.

Confirmed against the LIVE deployment on 2026-09-03 before any code changed: an
anonymous `DELETE /api/paths/999999999` came back `{"detail":"Learning path not
found"}` — the *handler's* wording, not FastAPI's `"Not Found"` — proving the request
reached the handler. With a real id it would have deleted someone's path.

Every test here is written to fail against the pre-fix code. The `_authorize_path`
guard raises **404, not 403**, so a wrong owner cannot tell "exists but not yours" from
"no such row" — the same rule the rest of `routes.py` already applies.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, engine  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone  # noqa: E402
from database import SessionLocal  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_module._magic_link_requests.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def captured_tokens(monkeypatch):
    """Same trick test_auth.py uses: capture the raw magic-link token."""
    tokens = []
    real = auth_module.secrets.token_urlsafe

    def fake(n=32):
        t = real(n)
        tokens.append(t)
        return t

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", fake)
    return tokens


def _sign_in(client, captured_tokens, email):
    client.post("/api/auth/request-link", json={"email": email})
    token = captured_tokens[-1]
    res = client.post("/api/auth/verify", json={"token": token})
    assert res.status_code == 200, res.text
    return res.json()


def _make_path(owner_user_id=None, anon_id=None, title="Victim path"):
    """Insert a path + one milestone directly, so the test does not depend on the
    generate route (which calls OpenAI)."""
    db = SessionLocal()
    try:
        p = LearningPath(
            title=title, description="d", experience_level="beginner",
            time_commitment="2h", is_public=False, total_xp=0, streak_days=0,
            user_id=owner_user_id, anon_session_id=anon_id,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        m = Milestone(learning_path_id=p.id, title="m1", description="d",
                      order=1, estimated_hours=1.0, resources="[]", completed=False)
        db.add(m)
        db.commit()
        db.refresh(m)
        return p.id, m.id
    finally:
        db.close()


def _path_exists(path_id):
    db = SessionLocal()
    try:
        return db.query(LearningPath).filter(LearningPath.id == path_id).first() is not None
    finally:
        db.close()


def _milestone_completed(milestone_id):
    db = SessionLocal()
    try:
        m = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        return None if m is None else m.completed
    finally:
        db.close()


# ── anonymous callers ──────────────────────────────────────────────────────────
def test_anon_cannot_delete_a_path(client):
    """The headline hole: DELETE by id, no credentials."""
    path_id, _ = _make_path(owner_user_id=None, anon_id="somebody-elses-cookie")
    res = client.delete(f"/api/paths/{path_id}")
    assert res.status_code == 404, res.text
    # Read the row back — a status code is not proof the row survived.
    assert _path_exists(path_id) is True


def test_anon_cannot_share_a_path(client):
    path_id, _ = _make_path(owner_user_id=None, anon_id="somebody-elses-cookie")
    res = client.patch(f"/api/paths/{path_id}/share", json={"is_public": True})
    assert res.status_code == 404, res.text
    db = SessionLocal()
    try:
        assert db.query(LearningPath).filter(LearningPath.id == path_id).first().is_public is False
    finally:
        db.close()


def test_anon_cannot_toggle_a_milestone(client):
    _, milestone_id = _make_path(owner_user_id=None, anon_id="somebody-elses-cookie")
    res = client.patch(f"/api/milestones/{milestone_id}", json={"completed": True})
    assert res.status_code == 404, res.text
    assert _milestone_completed(milestone_id) is False


def test_anon_cannot_trigger_feedback_regeneration(client):
    """This one deletes rows AND spends money on gpt-4o."""
    _, milestone_id = _make_path(owner_user_id=None, anon_id="somebody-elses-cookie")
    res = client.post(f"/api/milestones/{milestone_id}/feedback",
                      json={"milestone_id": milestone_id, "feedback": "too_hard"})
    assert res.status_code == 404, res.text


# ── a signed-in user who does not own the row ──────────────────────────────────
def test_wrong_owner_gets_404_on_delete(client, captured_tokens):
    victim_id, _ = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "attacker@example.com")
    res = client.delete(f"/api/paths/{victim_id}")
    assert res.status_code == 404, res.text
    assert _path_exists(victim_id) is True


def test_wrong_owner_gets_404_on_share(client, captured_tokens):
    victim_id, _ = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "attacker2@example.com")
    res = client.patch(f"/api/paths/{victim_id}/share", json={"is_public": True})
    assert res.status_code == 404, res.text


def test_wrong_owner_gets_404_on_milestone(client, captured_tokens):
    _, victim_ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "attacker3@example.com")
    res = client.patch(f"/api/milestones/{victim_ms}", json={"completed": True})
    assert res.status_code == 404, res.text
    assert _milestone_completed(victim_ms) is False


# ── the legitimate owners still work — the half that makes this a fix, not a wall ──
def test_signed_in_owner_can_still_delete(client, captured_tokens):
    me = _sign_in(client, captured_tokens, "owner@example.com")
    path_id, _ = _make_path(owner_user_id=me["id"])
    res = client.delete(f"/api/paths/{path_id}")
    assert res.status_code == 200, res.text
    assert _path_exists(path_id) is False


def test_signed_in_owner_can_still_share(client, captured_tokens):
    me = _sign_in(client, captured_tokens, "owner2@example.com")
    path_id, _ = _make_path(owner_user_id=me["id"])
    res = client.patch(f"/api/paths/{path_id}/share", json={"is_public": True})
    assert res.status_code == 200, res.text


def test_signed_in_owner_can_still_toggle_a_milestone(client, captured_tokens):
    me = _sign_in(client, captured_tokens, "owner3@example.com")
    _, milestone_id = _make_path(owner_user_id=me["id"])
    res = client.patch(f"/api/milestones/{milestone_id}", json={"completed": True})
    assert res.status_code == 200, res.text
    assert _milestone_completed(milestone_id) is True


def test_anonymous_owner_keeps_their_cookie_path(client):
    """AP9's anonymous flow must survive the fix: a visitor who generated a path
    before signing up still edits it via `ap_anon_id`. If this breaks, the guard is
    too strict and the app loses a real feature."""
    path_id, milestone_id = _make_path(owner_user_id=None, anon_id="my-own-cookie")
    client.cookies.set("ap_anon_id", "my-own-cookie")
    res = client.patch(f"/api/milestones/{milestone_id}", json={"completed": True})
    assert res.status_code == 200, res.text
    assert _milestone_completed(milestone_id) is True
    res = client.delete(f"/api/paths/{path_id}")
    assert res.status_code == 200, res.text


# ── the cron job that sends real email ─────────────────────────────────────────
def test_run_reminders_is_disabled_when_no_secret_is_set(client, monkeypatch):
    """An unset secret must mean DISABLED, never 'no check needed'."""
    monkeypatch.delenv("CRON_SECRET", raising=False)
    res = client.post("/api/jobs/run-reminders")
    assert res.status_code == 503, res.text


def test_run_reminders_rejects_a_missing_or_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "the-real-secret")
    called = []
    monkeypatch.setattr("routes.send_reminders", lambda db: called.append(1) or {})

    assert client.post("/api/jobs/run-reminders").status_code == 401
    assert client.post("/api/jobs/run-reminders",
                       headers={"X-Cron-Secret": "wrong"}).status_code == 401
    # Asserted with the mock, not inferred from the status: the pipeline must never
    # have been entered.
    assert called == []


def test_run_reminders_accepts_the_right_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "the-real-secret")
    called = []
    monkeypatch.setattr("routes.send_reminders", lambda db: called.append(1) or {"sent": 0})
    res = client.post("/api/jobs/run-reminders", headers={"X-Cron-Secret": "the-real-secret"})
    assert res.status_code == 200, res.text
    assert called == [1]

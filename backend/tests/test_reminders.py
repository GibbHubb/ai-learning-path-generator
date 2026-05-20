"""AP11 — daily reminder email tests.

The Resend HTTP send is short-circuited via env (no `RESEND_API_KEY` →
the helper logs and returns True), so these tests run fully offline.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_ap11.db"
os.environ.pop("RESEND_API_KEY", None)
os.environ["SECRET_KEY"] = "ap11-test-secret"

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, ReminderLog, User  # noqa: E402
from reminders import (  # noqa: E402
    find_candidates,
    make_unsubscribe_token,
    send_reminders,
    verify_unsubscribe_token,
)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_module._magic_link_requests.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _make_user_with_idle_path(*, email: str, opt_in: bool, last_active_days_ago: int | None) -> int:
    db = SessionLocal()
    try:
        u = User(email=email, reminder_opt_in=opt_in)
        db.add(u)
        db.flush()
        path = LearningPath(
            title=f"{email}'s path", description="d",
            experience_level="beginner", time_commitment="3h/wk",
            user_id=u.id,
            last_active_date=(datetime.utcnow().date() - timedelta(days=last_active_days_ago))
                              if last_active_days_ago is not None else None,
        )
        db.add(path)
        db.flush()
        db.add(Milestone(
            learning_path_id=path.id, title="m1", description="x",
            order=0, estimated_hours=1, resources="[]", completed=False,
        ))
        db.commit()
        return u.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Token unit tests
# ---------------------------------------------------------------------------


def test_unsubscribe_token_roundtrip():
    t = make_unsubscribe_token(123)
    assert verify_unsubscribe_token(t) == 123


def test_unsubscribe_token_rejects_tampering():
    t = make_unsubscribe_token(123)
    tampered = t[:-1] + ("x" if t[-1] != "x" else "y")
    assert verify_unsubscribe_token(tampered) is None


def test_unsubscribe_token_rejects_garbage():
    assert verify_unsubscribe_token("") is None
    assert verify_unsubscribe_token("not-a-token") is None
    assert verify_unsubscribe_token("123") is None  # missing signature


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def test_picks_optin_user_with_4_day_idle():
    _make_user_with_idle_path(email="alice@a.dev", opt_in=True, last_active_days_ago=4)
    db = SessionLocal()
    try:
        targets = list(find_candidates(db))
        assert len(targets) == 1 and targets[0].email == "alice@a.dev"
    finally:
        db.close()


def test_skips_optout_users():
    _make_user_with_idle_path(email="bob@b.dev", opt_in=False, last_active_days_ago=10)
    db = SessionLocal()
    try:
        assert list(find_candidates(db)) == []
    finally:
        db.close()


def test_skips_recently_active():
    _make_user_with_idle_path(email="active@a.dev", opt_in=True, last_active_days_ago=1)
    db = SessionLocal()
    try:
        assert list(find_candidates(db)) == []
    finally:
        db.close()


def test_skips_when_already_sent_today():
    user_id = _make_user_with_idle_path(email="dedupe@a.dev", opt_in=True, last_active_days_ago=5)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        u.reminder_sent_at = datetime.utcnow()
        db.commit()
        assert list(find_candidates(db)) == []
    finally:
        db.close()


def test_send_loop_is_idempotent_per_day():
    _make_user_with_idle_path(email="x@a.dev", opt_in=True, last_active_days_ago=4)
    db = SessionLocal()
    try:
        first = send_reminders(db)
        second = send_reminders(db)
    finally:
        db.close()
    assert first["sent"] == 1
    assert second["sent"] == 0


def test_cooloff_after_10_silent_reminders():
    user_id = _make_user_with_idle_path(email="silent@a.dev", opt_in=True, last_active_days_ago=4)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        u.no_activity_reminders_sent = 10
        u.reminder_sent_at = datetime.utcnow() - timedelta(days=2)
        db.commit()
        assert list(find_candidates(db)) == []
    finally:
        db.close()


def test_cooloff_expires_after_90_days():
    user_id = _make_user_with_idle_path(email="back@a.dev", opt_in=True, last_active_days_ago=4)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        u.no_activity_reminders_sent = 10
        u.reminder_sent_at = datetime.utcnow() - timedelta(days=91)
        db.commit()
        assert len(list(find_candidates(db))) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _sign_in(client, captured_tokens, email):
    client.post("/api/auth/request-link", json={"email": email})
    client.post("/api/auth/verify", json={"token": captured_tokens[-1]})


@pytest.fixture
def captured_tokens(monkeypatch):
    import secrets as secrets_mod
    captured = []
    real = secrets_mod.token_urlsafe

    def _capture(*a, **kw):
        token = real(*a, **kw)
        captured.append(token)
        return token

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", _capture)
    return captured


def test_toggle_endpoint_requires_auth(client):
    res = client.patch("/api/auth/me/reminders", json={"reminder_opt_in": True})
    assert res.status_code == 401


def test_toggle_flips_opt_in_and_resets_streak(client, captured_tokens):
    _sign_in(client, captured_tokens, "tog@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "tog@a.dev").first().id

    # Pretend the user was already in 5-strikes-deep silence
    db = SessionLocal()
    db.query(User).filter(User.id == user_id).update({"no_activity_reminders_sent": 5})
    db.commit()
    db.close()

    res = client.patch("/api/auth/me/reminders", json={"reminder_opt_in": True})
    assert res.status_code == 200
    body = res.json()
    assert body["reminder_opt_in"] is True

    # Re-fetch from DB — counter should be reset by the toggle
    fresh = SessionLocal().query(User).filter(User.id == user_id).first()
    assert fresh.reminder_opt_in is True
    assert fresh.no_activity_reminders_sent == 0


def test_unsubscribe_endpoint_disables_reminders(client):
    user_id = _make_user_with_idle_path(email="u@a.dev", opt_in=True, last_active_days_ago=4)
    token = make_unsubscribe_token(user_id)
    res = client.get(f"/api/unsubscribe?token={token}")
    assert res.status_code == 200
    fresh = SessionLocal().query(User).filter(User.id == user_id).first()
    assert fresh.reminder_opt_in is False


def test_unsubscribe_rejects_bad_token(client):
    res = client.get("/api/unsubscribe?token=garbage")
    assert res.status_code == 400


def test_milestone_complete_resets_inactivity_counter(client, captured_tokens):
    _sign_in(client, captured_tokens, "ap4@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "ap4@a.dev").first().id
    # Seed a path owned by this user with one milestone + a non-zero counter
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    user.no_activity_reminders_sent = 7
    path = LearningPath(
        title="t", description="d", experience_level="beginner",
        time_commitment="3h/wk", user_id=user_id,
    )
    db.add(path); db.flush()
    m = Milestone(learning_path_id=path.id, title="m", description="x",
                  order=0, estimated_hours=1, resources="[]", completed=False)
    db.add(m); db.commit()
    m_id = m.id
    db.close()

    res = client.patch(f"/api/milestones/{m_id}", json={"completed": True})
    assert res.status_code == 200
    fresh = SessionLocal().query(User).filter(User.id == user_id).first()
    assert fresh.no_activity_reminders_sent == 0

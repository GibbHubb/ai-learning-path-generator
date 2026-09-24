"""AP35 — the durable, per-visitor rate limiter.

Covers the plan's deterministic acceptance criteria (§3):
  1. trusted X-Forwarded-For keys per-visitor (6 different -> 6x200; 6 same -> 5x200+1x429)
  2. a spoofed X-Forwarded-For is ignored with no trust boundary configured
  3. bucket state survives across two independently constructed app instances
  4. /milestones/{id}/feedback and /milestones/{id}/quiz/regenerate are limited too
  5. every 429 carries Retry-After + X-RateLimit-Limit/Remaining
  7. stale rows are pruned on write

Criterion 4 (10 concurrent requests -> exactly 5x200 + 5x429 against a real
uvicorn) is covered separately in test_rate_limit_concurrency_ap35.py.
"""
import importlib
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
import quizzes as quizzes_module  # noqa: E402
import rate_limit  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, RateLimitHit  # noqa: E402
from schemas import GeneratedPath  # noqa: E402 — AP33: generate_learning_path returns this now


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
    res = client.post("/api/auth/verify", json={"token": captured_tokens[-1]})
    assert res.status_code == 200, res.text
    return res.json()


def _generate_mock():
    # AP33 — GeneratedPath requires >=1 milestone, matching real generation output.
    return patch("routes.generate_learning_path", return_value=GeneratedPath(
        path_title="Rate Limit Test",
        path_description="Desc",
        milestones=[
            {"title": "m", "description": "d", "estimated_hours": 1.0, "resources": []},
        ],
    ))


def _make_owned_milestone(owner_id: int, body: str | None = None) -> tuple[int, int]:
    body = body or (
        "This milestone covers the basics of recursion. "
        "You will learn how a function can call itself, what a base case is, "
        "and why stack depth matters in practice for languages like Python."
    )
    db = SessionLocal()
    try:
        p = LearningPath(
            title="t", description="d", experience_level="beginner",
            time_commitment="3h/wk", user_id=owner_id,
        )
        db.add(p)
        db.flush()
        m = Milestone(
            learning_path_id=p.id, title="m", description=body,
            order=0, estimated_hours=1.0, resources="[]", completed=False,
        )
        db.add(m)
        db.commit()
        return p.id, m.id
    finally:
        db.close()


# ── criterion 1: trusted X-Forwarded-For keys per-visitor ──────────────────────
def test_six_different_forwarded_for_values_all_succeed_when_trusted(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HEADER", "1")
    with _generate_mock():
        for i in range(6):
            res = client.post("/api/generate", json={
                "goal": f"goal-{i}", "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            }, headers={"X-Forwarded-For": f"10.0.0.{i}"})
            assert res.status_code == 200, res.text


def test_same_forwarded_for_value_trips_the_limit_on_the_sixth(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HEADER", "1")
    with _generate_mock():
        for i in range(6):
            res = client.post("/api/generate", json={
                "goal": f"goal-{i}", "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            }, headers={"X-Forwarded-For": "10.0.0.9"})
            if i < 5:
                assert res.status_code == 200, res.text
            else:
                assert res.status_code == 429, res.text


# ── criterion 2: a spoofed header with no trust boundary is ignored ────────────
def test_spoofed_forwarded_for_ignored_without_trusted_proxy_config(client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HEADER", raising=False)
    with _generate_mock():
        for i in range(6):
            res = client.post("/api/generate", json={
                "goal": f"goal-{i}", "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            }, headers={"X-Forwarded-For": f"10.0.0.{i}"})  # a different forged value every time
            if i < 5:
                assert res.status_code == 200, res.text
            else:
                # All 6 land on the SAME key (the real TestClient peer) because the
                # header is untrusted, so the 6th is still blocked.
                assert res.status_code == 429, res.text


# ── criterion 3: bucket state survives across two app instances ────────────────
def test_state_shared_across_two_independently_constructed_app_instances(monkeypatch):
    """Simulates two Vercel function instances: two separately constructed
    FastAPI `app` objects, sharing only the database (never reimports
    `database`, so `engine`/`SessionLocal` — and therefore the DB file — stay
    the same object). Mirrors the reimport trick test_config_ap38.py already
    uses; monkeypatch.delitem restores the original `main` module at
    teardown."""
    app1 = app  # the module-level import at the top of this file
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main2 = importlib.import_module("main")
    app2 = main2.app
    assert app2 is not app1

    client1 = TestClient(app1)
    client2 = TestClient(app2)

    with _generate_mock():
        for i, c in enumerate([client1, client1, client1, client2, client2]):
            res = c.post("/api/generate", json={
                "goal": f"goal-{i}", "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            })
            assert res.status_code == 200, res.text
        # 6th request, on the OTHER instance again — must still be blocked,
        # proving the count lives in the database, not in either process.
        res = client2.post("/api/generate", json={
            "goal": "goal-6", "experience_level": "beginner",
            "time_commitment": "5-10 hours/week",
        })
        assert res.status_code == 429, res.text


# ── criterion 5: the two model-calling routes AP35 adds coverage for ───────────
def test_feedback_route_is_rate_limited(client, captured_tokens):
    me = _sign_in(client, captured_tokens, "feedback-limit@example.com")
    for i in range(6):
        _, milestone_id = _make_owned_milestone(me["id"])
        res = client.post(f"/api/milestones/{milestone_id}/feedback",
                           json={"milestone_id": milestone_id, "feedback": "just_right"})
        if i < 5:
            assert res.status_code == 200, res.text
        else:
            assert res.status_code == 429, res.text


def test_quiz_regenerate_route_is_rate_limited(client, captured_tokens, monkeypatch):
    monkeypatch.setattr(quizzes_module, "_call_claude", lambda m: [
        quizzes_module.QuizQuestion(question="Q", options=["a", "b", "c", "d"],
                                     correct_index=0, explanation="e"),
        quizzes_module.QuizQuestion(question="Q2", options=["a", "b", "c", "d"],
                                     correct_index=1, explanation="e2"),
        quizzes_module.QuizQuestion(question="Q3", options=["a", "b", "c", "d"],
                                     correct_index=2, explanation="e3"),
    ])
    me = _sign_in(client, captured_tokens, "quiz-limit@example.com")
    # A fresh milestone per call — regenerate has its OWN 24h-per-milestone
    # limiter (quizzes.REGENERATE_RATE_LIMIT_SECONDS), a different guarantee
    # AP35 does not touch; reusing one milestone would trip that instead.
    for i in range(6):
        _, milestone_id = _make_owned_milestone(me["id"])
        res = client.post(f"/api/milestones/{milestone_id}/quiz/regenerate")
        if i < 5:
            assert res.status_code == 200, res.text
        else:
            assert res.status_code == 429, res.text


# ── criterion 6: Retry-After + X-RateLimit-* on the 429 ────────────────────────
def test_429_carries_retry_after_and_ratelimit_headers(client):
    with _generate_mock():
        for i in range(6):
            res = client.post("/api/generate", json={
                "goal": f"goal-{i}", "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            })
    assert res.status_code == 429, res.text
    retry_after = res.headers.get("Retry-After")
    assert retry_after is not None
    assert 1 <= int(retry_after) <= 60
    assert res.headers.get("X-RateLimit-Limit") == "5"
    assert res.headers.get("X-RateLimit-Remaining") == "0"


def test_200_also_carries_ratelimit_headers(client):
    with _generate_mock():
        res = client.post("/api/generate", json={
            "goal": "goal", "experience_level": "beginner",
            "time_commitment": "5-10 hours/week",
        })
    assert res.status_code == 200, res.text
    assert res.headers.get("X-RateLimit-Limit") == "5"
    assert res.headers.get("X-RateLimit-Remaining") == "4"


# ── criterion 7: stale rows are pruned on write ─────────────────────────────────
def test_stale_rows_are_pruned_on_write():
    db = SessionLocal()
    try:
        stale = datetime.utcnow() - timedelta(seconds=rate_limit.RATE_LIMIT_WINDOW + 3600)
        db.bulk_save_objects([
            RateLimitHit(key=f"stale-{i}", route="/api/generate", created_at=stale)
            for i in range(1000)
        ])
        db.commit()
        assert db.query(RateLimitHit).count() == 1000
    finally:
        db.close()

    client_ = TestClient(app)
    with _generate_mock():
        res = client_.post("/api/generate", json={
            "goal": "goal", "experience_level": "beginner",
            "time_commitment": "5-10 hours/week",
        })
    assert res.status_code == 200, res.text

    db = SessionLocal()
    try:
        remaining = db.query(RateLimitHit).count()
    finally:
        db.close()
    assert remaining < 10, remaining

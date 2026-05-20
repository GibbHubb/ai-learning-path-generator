"""AP8 — milestone quiz tests.

Claude is stubbed at the `quizzes._call_claude` boundary so tests run
offline + deterministic.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_ap8.db"
os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
import quizzes as quizzes_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, MilestoneQuiz, QuizAttempt, User  # noqa: E402
from quizzes import grade_attempt, validate_quiz, PASS_THRESHOLD, QuizQuestion  # noqa: E402


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
    import secrets as secrets_mod
    captured = []
    real = secrets_mod.token_urlsafe

    def _capture(*a, **kw):
        token = real(*a, **kw)
        captured.append(token)
        return token

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", _capture)
    return captured


def _sign_in(client, captured_tokens, email):
    client.post("/api/auth/request-link", json={"email": email})
    client.post("/api/auth/verify", json={"token": captured_tokens[-1]})


def _make_path_with_milestone(*, owner_id: int | None = None, body: str | None = None) -> tuple[int, int]:
    body = body if body is not None else (
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
            learning_path_id=p.id, title="Recursion basics",
            description=body, order=0, estimated_hours=2,
            resources="[]", completed=False,
        )
        db.add(m)
        db.commit()
        return p.id, m.id
    finally:
        db.close()


def _stub_questions() -> list[QuizQuestion]:
    """Three deterministic MCQs — answers indexed [0, 1, 2]."""
    return [
        QuizQuestion(question="Q1", options=["a", "b", "c", "d"], correct_index=0, explanation="e1"),
        QuizQuestion(question="Q2", options=["a", "b", "c", "d"], correct_index=1, explanation="e2"),
        QuizQuestion(question="Q3", options=["a", "b", "c", "d"], correct_index=2, explanation="e3"),
    ]


@pytest.fixture
def stub_claude(monkeypatch):
    """Replace the live Claude call with a deterministic 3-Q quiz."""
    monkeypatch.setattr(quizzes_module, "_call_claude", lambda m: _stub_questions())


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


def test_validate_rejects_too_few():
    assert validate_quiz([{"question": "q", "options": ["a", "b", "c", "d"], "correct_index": 0, "explanation": ""}] * 2) is None


def test_validate_rejects_wrong_option_count():
    assert validate_quiz([{"question": "q", "options": ["a", "b", "c"], "correct_index": 0, "explanation": ""}] * 3) is None


def test_validate_rejects_correct_index_out_of_range():
    assert validate_quiz([{"question": "q", "options": ["a", "b", "c", "d"], "correct_index": 5, "explanation": ""}] * 3) is None


def test_validate_accepts_valid():
    parsed = validate_quiz([{"question": "q", "options": ["a", "b", "c", "d"], "correct_index": 0, "explanation": ""}] * 3)
    assert parsed is not None
    assert len(parsed) == 3


def test_grade_perfect_score():
    quiz = MilestoneQuiz(
        milestone_id=1,
        questions_json=quizzes_module._serialise(_stub_questions()),
    )
    g = grade_attempt(quiz, [0, 1, 2])
    assert g["score"] == 1.0
    assert g["passed"] is True


def test_grade_below_threshold():
    quiz = MilestoneQuiz(
        milestone_id=1,
        questions_json=quizzes_module._serialise(_stub_questions()),
    )
    # 1/3 correct = 33% < 70%
    g = grade_attempt(quiz, [0, 0, 0])
    assert round(g["score"], 2) == 0.33
    assert g["passed"] is False


def test_grade_pads_missing_answers_as_wrong():
    quiz = MilestoneQuiz(
        milestone_id=1,
        questions_json=quizzes_module._serialise(_stub_questions()),
    )
    g = grade_attempt(quiz, [0])  # short answer list
    assert g["results"][1]["your_answer"] == -1
    assert g["results"][1]["correct"] is False


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_get_quiz_requires_auth(client):
    _, m_id = _make_path_with_milestone()
    res = client.get(f"/api/milestones/{m_id}/quiz")
    assert res.status_code == 401


def test_get_quiz_generates_on_miss(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    res = client.get(f"/api/milestones/{m_id}/quiz")
    assert res.status_code == 200
    body = res.json()
    assert body["milestone_id"] == m_id
    assert len(body["questions"]) == 3
    # Cache row exists
    assert SessionLocal().query(MilestoneQuiz).filter(MilestoneQuiz.milestone_id == m_id).count() == 1


def test_get_quiz_returns_cached_on_repeat(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    client.get(f"/api/milestones/{m_id}/quiz")
    first_count = SessionLocal().query(MilestoneQuiz).count()
    client.get(f"/api/milestones/{m_id}/quiz")
    second_count = SessionLocal().query(MilestoneQuiz).count()
    assert first_count == second_count == 1


def test_get_quiz_refuses_short_milestone(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id, body="too short")
    res = client.get(f"/api/milestones/{m_id}/quiz")
    assert res.status_code == 422


def test_regenerate_rate_limited(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    client.get(f"/api/milestones/{m_id}/quiz")  # initial generate
    res = client.post(f"/api/milestones/{m_id}/quiz/regenerate")
    assert res.status_code == 429


def test_attempt_passing_marks_milestone_complete_and_fires_xp(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    client.get(f"/api/milestones/{m_id}/quiz")
    res = client.post(f"/api/milestones/{m_id}/quiz/attempt", json={"answers": [0, 1, 2]})
    assert res.status_code == 200
    body = res.json()
    assert body["passed"] is True
    assert body["milestone_completed"] is True
    assert body["total_xp"] == 10  # one milestone × 10 XP

    fresh = SessionLocal().query(Milestone).filter(Milestone.id == m_id).first()
    assert fresh.completed is True


def test_attempt_failing_does_not_complete(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    client.get(f"/api/milestones/{m_id}/quiz")
    res = client.post(f"/api/milestones/{m_id}/quiz/attempt", json={"answers": [3, 3, 3]})
    body = res.json()
    assert body["passed"] is False
    assert body["milestone_completed"] is False
    fresh = SessionLocal().query(Milestone).filter(Milestone.id == m_id).first()
    assert fresh.completed is False


def test_attempt_logs_audit_row(client, captured_tokens, stub_claude):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    client.get(f"/api/milestones/{m_id}/quiz")
    client.post(f"/api/milestones/{m_id}/quiz/attempt", json={"answers": [3, 3, 3]})  # fail
    client.post(f"/api/milestones/{m_id}/quiz/attempt", json={"answers": [0, 1, 2]})  # pass

    db = SessionLocal()
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user_id, QuizAttempt.milestone_id == m_id,
    ).order_by(QuizAttempt.id).all()
    db.close()
    assert len(attempts) == 2
    assert attempts[0].passed is False and attempts[1].passed is True


def test_attempt_404_when_no_quiz(client, captured_tokens):
    _sign_in(client, captured_tokens, "u@a.dev")
    user_id = SessionLocal().query(User).filter(User.email == "u@a.dev").first().id
    _, m_id = _make_path_with_milestone(owner_id=user_id)

    res = client.post(f"/api/milestones/{m_id}/quiz/attempt", json={"answers": [0]})
    assert res.status_code == 404

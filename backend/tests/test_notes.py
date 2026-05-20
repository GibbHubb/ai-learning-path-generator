"""AP12 — milestone notes / reflections tests."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_ap12.db"
os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, MilestoneNote, User  # noqa: E402
from notes_service import parse_difficulty_flag  # noqa: E402


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


def _seed_path(user_id: int | None = None, is_public: bool = False) -> tuple[int, int]:
    """Returns (path_id, milestone_id)."""
    db = SessionLocal()
    try:
        path = LearningPath(
            title="Test path", description="d",
            experience_level="beginner", time_commitment="5h/wk",
            user_id=user_id, is_public=is_public,
        )
        db.add(path)
        db.flush()
        milestone = Milestone(
            learning_path_id=path.id, title="m1", description="x",
            order=0, estimated_hours=2, resources="[]",
        )
        db.add(milestone)
        db.commit()
        return path.id, milestone.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# parse_difficulty_flag — pure-function unit tests
# ---------------------------------------------------------------------------


def test_parse_negative_flag_for_too_easy():
    assert parse_difficulty_flag("This was too easy") == -1


def test_parse_neutral_flag_for_blank():
    assert parse_difficulty_flag("") == 0
    assert parse_difficulty_flag("   ") == 0


def test_parse_hard_flag():
    assert parse_difficulty_flag("Found this too hard tbh") == 1


def test_parse_confused_flag():
    assert parse_difficulty_flag("I got confused at the cascade step") == 2


def test_parse_negation_strips_cue():
    # Naive but useful: "not confused" should NOT trip the confused cue.
    assert parse_difficulty_flag("not confused, learned a lot") == 0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_get_note_requires_auth(client):
    _, m_id = _seed_path()
    res = client.get(f"/api/milestones/{m_id}/note")
    assert res.status_code == 401


def test_upsert_creates_note_and_computes_flag(client, captured_tokens):
    _sign_in(client, captured_tokens, "user@finly.dev")
    user_id = SessionLocal().query(User).filter(User.email == "user@finly.dev").first().id
    _, m_id = _seed_path(user_id=user_id)

    res = client.put(
        f"/api/milestones/{m_id}/note",
        json={"content": "I got stuck on the recursion bit", "is_private": False},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["content"] == "I got stuck on the recursion bit"
    assert body["difficulty_flag"] == 2  # "stuck"
    assert body["is_private"] is False


def test_upsert_updates_existing_note(client, captured_tokens):
    _sign_in(client, captured_tokens, "user@finly.dev")
    user_id = SessionLocal().query(User).filter(User.email == "user@finly.dev").first().id
    _, m_id = _seed_path(user_id=user_id)

    client.put(f"/api/milestones/{m_id}/note", json={"content": "v1", "is_private": False})
    res = client.put(f"/api/milestones/{m_id}/note", json={"content": "v2 — too easy", "is_private": True})
    assert res.status_code == 200
    body = res.json()
    assert body["content"] == "v2 — too easy"
    assert body["is_private"] is True
    assert body["difficulty_flag"] == -1
    # Only one row in DB
    assert SessionLocal().query(MilestoneNote).count() == 1


def test_upsert_with_blank_content_deletes(client, captured_tokens):
    _sign_in(client, captured_tokens, "user@finly.dev")
    user_id = SessionLocal().query(User).filter(User.email == "user@finly.dev").first().id
    _, m_id = _seed_path(user_id=user_id)

    client.put(f"/api/milestones/{m_id}/note", json={"content": "v1", "is_private": False})
    res = client.put(f"/api/milestones/{m_id}/note", json={"content": "   ", "is_private": False})
    assert res.status_code == 200
    assert res.json() is None
    assert SessionLocal().query(MilestoneNote).count() == 0


def test_get_note_returns_caller_only(client, captured_tokens):
    _sign_in(client, captured_tokens, "alice@finly.dev")
    alice_id = SessionLocal().query(User).filter(User.email == "alice@finly.dev").first().id
    _, m_id = _seed_path(user_id=alice_id)
    client.put(f"/api/milestones/{m_id}/note", json={"content": "alice note", "is_private": False})

    # Bob signs in (separate browser would normally — TestClient shares cookie jar
    # so sign-in here also rotates the active cookie to Bob).
    _sign_in(client, captured_tokens, "bob@finly.dev")
    res = client.get(f"/api/milestones/{m_id}/note")
    assert res.status_code == 200
    assert res.json() is None  # Bob has no note for this milestone


def test_delete_note_clears(client, captured_tokens):
    _sign_in(client, captured_tokens, "user@finly.dev")
    user_id = SessionLocal().query(User).filter(User.email == "user@finly.dev").first().id
    _, m_id = _seed_path(user_id=user_id)
    client.put(f"/api/milestones/{m_id}/note", json={"content": "byebye", "is_private": False})

    res = client.delete(f"/api/milestones/{m_id}/note")
    assert res.status_code == 204
    assert SessionLocal().query(MilestoneNote).count() == 0


def test_public_notes_endpoint_includes_only_public(client, captured_tokens):
    # Alice creates a note (default public)
    _sign_in(client, captured_tokens, "alice@finly.dev")
    alice_id = SessionLocal().query(User).filter(User.email == "alice@finly.dev").first().id
    path_id, m_id = _seed_path(user_id=alice_id, is_public=True)
    client.put(f"/api/milestones/{m_id}/note", json={"content": "Loved this", "is_private": False})

    # Bob adds a private note on the same milestone
    _sign_in(client, captured_tokens, "bob@finly.dev")
    client.put(f"/api/milestones/{m_id}/note", json={"content": "secret thoughts", "is_private": True})

    # Anonymous fetch
    anon = TestClient(app)  # fresh cookie jar
    res = anon.get(f"/api/paths/{path_id}/notes/public")
    assert res.status_code == 200
    body = res.json()
    notes = body[str(m_id)]
    contents = {n["content"] for n in notes}
    assert "Loved this" in contents
    assert "secret thoughts" not in contents
    # Email is masked
    assert all("@" in n["author"] and "*" in n["author"] for n in notes)


def test_public_notes_404_when_path_not_public(client, captured_tokens):
    _sign_in(client, captured_tokens, "alice@finly.dev")
    alice_id = SessionLocal().query(User).filter(User.email == "alice@finly.dev").first().id
    path_id, _ = _seed_path(user_id=alice_id, is_public=False)

    anon = TestClient(app)
    res = anon.get(f"/api/paths/{path_id}/notes/public")
    assert res.status_code == 404


def test_fork_does_not_copy_notes(client, captured_tokens):
    """AP12 spec: forked paths start with clean reflection fields."""
    _sign_in(client, captured_tokens, "alice@finly.dev")
    alice_id = SessionLocal().query(User).filter(User.email == "alice@finly.dev").first().id
    path_id, m_id = _seed_path(user_id=alice_id, is_public=True)
    client.put(f"/api/milestones/{m_id}/note", json={"content": "alice's reflection", "is_private": False})

    # Bob forks
    _sign_in(client, captured_tokens, "bob@finly.dev")
    res = client.post(f"/api/paths/{path_id}/fork")
    assert res.status_code == 200
    fork = res.json()

    # The new fork's milestones have NO notes for Bob
    fork_milestone_id = fork["milestones"][0]["id"]
    note = client.get(f"/api/milestones/{fork_milestone_id}/note")
    assert note.status_code == 200
    assert note.json() is None

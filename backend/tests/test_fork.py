"""AP10 — fork-a-public-path tests."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, User  # noqa: E402


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


def _seed_public_path(owner_user_id):
    db = SessionLocal()
    try:
        p = LearningPath(
            title="Master Rust",
            description="Original description",
            experience_level="intermediate",
            time_commitment="10h/wk",
            category="Programming",
            is_public=True,
            user_id=owner_user_id,
        )
        db.add(p)
        db.flush()
        for i, t in enumerate(["Setup", "Ownership", "Async"]):
            db.add(Milestone(
                learning_path_id=p.id,
                title=t, description="x",
                order=i, estimated_hours=2.0,
                resources='["docs.rs"]',
                completed=(i == 0),  # mark milestone 0 as completed on the source
            ))
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _seed_private_path(owner_user_id):
    db = SessionLocal()
    try:
        p = LearningPath(
            title="Secret stuff",
            description="x", experience_level="beginner",
            time_commitment="2h/wk", category="Other",
            is_public=False, user_id=owner_user_id,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _make_user(email):
    db = SessionLocal()
    try:
        u = User(email=email)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def test_fork_requires_auth(client):
    owner_id = _make_user("author@example.com")
    src_id = _seed_public_path(owner_id)
    res = client.post(f"/api/paths/{src_id}/fork")
    assert res.status_code == 401


def test_fork_public_path_succeeds(client, captured_tokens):
    owner_id = _make_user("author@example.com")
    src_id = _seed_public_path(owner_id)

    _sign_in(client, captured_tokens, "viewer@example.com")
    res = client.post(f"/api/paths/{src_id}/fork")
    assert res.status_code == 200, res.text
    body = res.json()

    # Title prefixed
    assert body["title"] == "My copy of Master Rust"
    # Progress reset
    assert all(m["completed"] is False for m in body["milestones"])
    # Same milestone count + order preserved
    assert [m["title"] for m in body["milestones"]] == ["Setup", "Ownership", "Async"]
    # Forked path is private
    assert body["is_public"] is False
    # XP / streak reset
    assert body["total_xp"] == 0
    assert body["streak_days"] == 0


def test_fork_private_path_blocked(client, captured_tokens):
    owner_id = _make_user("author@example.com")
    private_id = _seed_private_path(owner_id)
    _sign_in(client, captured_tokens, "viewer@example.com")
    res = client.post(f"/api/paths/{private_id}/fork")
    assert res.status_code == 403


def test_fork_404_for_missing(client, captured_tokens):
    _sign_in(client, captured_tokens, "viewer@example.com")
    res = client.post("/api/paths/99999/fork")
    assert res.status_code == 404


def test_fork_increments_source_count_and_records_lineage(client, captured_tokens):
    owner_id = _make_user("author@example.com")
    src_id = _seed_public_path(owner_id)

    _sign_in(client, captured_tokens, "viewer@example.com")
    fork_a = client.post(f"/api/paths/{src_id}/fork").json()
    fork_b = client.post(f"/api/paths/{src_id}/fork").json()

    db = SessionLocal()
    try:
        src = db.query(LearningPath).filter(LearningPath.id == src_id).first()
        assert src.fork_count == 2

        copy_a = db.query(LearningPath).filter(LearningPath.id == fork_a["id"]).first()
        assert copy_a.forked_from_id == src_id
        assert copy_a.original_author_id == owner_id
        assert copy_a.user_id != owner_id  # owner is the forker, not the original author

        copy_b = db.query(LearningPath).filter(LearningPath.id == fork_b["id"]).first()
        assert copy_b.forked_from_id == src_id
    finally:
        db.close()


def test_fork_of_fork_attributes_to_original_author(client, captured_tokens):
    """Forks-of-forks should keep `original_author_id` pointing at the true author."""
    owner_id = _make_user("author@example.com")
    src_id = _seed_public_path(owner_id)

    _sign_in(client, captured_tokens, "viewer@example.com")
    first_fork = client.post(f"/api/paths/{src_id}/fork").json()

    # Make the fork itself public so a third user can re-fork
    db = SessionLocal()
    try:
        f = db.query(LearningPath).filter(LearningPath.id == first_fork["id"]).first()
        f.is_public = True
        db.commit()
    finally:
        db.close()

    # New user re-forks the fork
    client.post("/api/auth/logout")
    _sign_in(client, captured_tokens, "third@example.com")
    second_fork = client.post(f"/api/paths/{first_fork['id']}/fork").json()

    db = SessionLocal()
    try:
        copy = db.query(LearningPath).filter(LearningPath.id == second_fork["id"]).first()
        # Lineage points at nearest ancestor
        assert copy.forked_from_id == first_fork["id"]
        # Author attribution skips the middle hop
        assert copy.original_author_id == owner_id
    finally:
        db.close()


def test_fork_appears_in_my_paths(client, captured_tokens):
    owner_id = _make_user("author@example.com")
    src_id = _seed_public_path(owner_id)

    _sign_in(client, captured_tokens, "viewer@example.com")
    client.post(f"/api/paths/{src_id}/fork")

    res = client.get("/api/paths/me")
    titles = [p["title"] for p in res.json()]
    assert "My copy of Master Rust" in titles

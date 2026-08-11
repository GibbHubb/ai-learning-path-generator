"""AP30-fu1 — per-user Open Graph card for public profiles.

AP30 shipped the public profile but left a single static OG card, so every
shared link unfurled identically. These tests cover the two new top-level
routes: the crawler HTML shell at ``/u/{id}`` and the generated card at
``/u/{id}/card.png``.

The privacy rule inherited from AP30 is the important one: a private or
missing profile must 404 identically, and no email may ever reach the card.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("RESEND_API_KEY", None)

from database import Base, engine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone, User  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _make_user(email: str, public: bool, xp: int = 0, streak: int = 0,
               completed: bool = False) -> int:
    """Create a user with one path so the stats are non-trivial."""
    db = SessionLocal()
    try:
        user = User(email=email, is_public_profile=public)
        db.add(user)
        db.commit()
        db.refresh(user)

        path = LearningPath(title="Test path", user_id=user.id,
                            total_xp=xp, streak_days=streak)
        db.add(path)
        db.commit()
        db.refresh(path)

        db.add(Milestone(learning_path_id=path.id, title="M1",
                         completed=completed))
        db.commit()
        return user.id
    finally:
        db.close()


# ── crawler HTML ──────────────────────────────────────────────────────────

def test_unfurl_returns_html_with_per_user_meta(client):
    uid = _make_user("public@example.com", public=True, xp=1500, streak=7,
                     completed=True)
    r = client.get(f"/u/{uid}", headers={"User-Agent": "Twitterbot/1.0"})

    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]

    body = r.text
    for prop in ('property="og:title"', 'property="og:description"',
                 'property="og:image"', 'name="twitter:card"',
                 'name="twitter:image"'):
        assert prop in body, f"missing {prop}"

    assert 'content="summary_large_image"' in body
    # the numbers must be THIS user's, not a generic card
    assert "1,500" in body
    assert "7-day best streak" in body
    # absolute og:image — crawlers reject relative URLs
    assert f'property="og:image" content="http' in body
    assert f"/u/{uid}/card.png" in body


def test_unfurl_contains_no_pii(client):
    uid = _make_user("secret.person@example.com", public=True, xp=10)
    body = client.get(f"/u/{uid}").text
    assert "secret.person@example.com" not in body
    assert "@example.com" not in body
    assert f"Learner #{uid}" in body


def test_unfurl_redirects_humans_to_the_spa(client):
    uid = _make_user("public@example.com", public=True)
    body = client.get(f"/u/{uid}").text
    # both belt and braces: crawlers ignore each, humans follow one
    assert 'http-equiv="refresh"' in body
    assert "location.replace(" in body
    assert f"/u/{uid}" in body


@pytest.mark.parametrize("public", [False])
def test_unfurl_404s_for_private_profile(client, public):
    uid = _make_user("private@example.com", public=public)
    assert client.get(f"/u/{uid}").status_code == 404


def test_unfurl_404s_for_missing_user(client):
    assert client.get("/u/999999").status_code == 404


def test_private_and_missing_are_indistinguishable(client):
    """A private profile must not leak its existence (AP30 rule)."""
    uid = _make_user("private@example.com", public=False)
    private = client.get(f"/u/{uid}")
    missing = client.get("/u/999999")
    assert private.status_code == missing.status_code == 404
    assert private.json() == missing.json()


# ── card.png ──────────────────────────────────────────────────────────────

def test_card_returns_a_real_png(client):
    uid = _make_user("public@example.com", public=True, xp=2500, streak=11,
                     completed=True)
    r = client.get(f"/u/{uid}/card.png")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "not PNG magic bytes"
    assert "max-age" in r.headers.get("cache-control", "")

    # decodes, and is the size every unfurler crops to
    from io import BytesIO

    from PIL import Image
    img = Image.open(BytesIO(r.content))
    assert img.size == (1200, 630)


def test_card_404s_for_private_and_missing(client):
    uid = _make_user("private@example.com", public=False)
    assert client.get(f"/u/{uid}/card.png").status_code == 404
    assert client.get("/u/999999/card.png").status_code == 404


# ── regression: AP30 surfaces unchanged ───────────────────────────────────

def test_ap30_stats_endpoint_still_works(client):
    uid = _make_user("public@example.com", public=True, xp=600, streak=5)
    r = client.get(f"/api/u/{uid}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_xp"] == 600
    assert body["best_streak"] == 5
    assert "email" not in body


def test_root_and_health_still_reachable(client):
    """The new top-level /u routes must not shadow existing app routes."""
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200

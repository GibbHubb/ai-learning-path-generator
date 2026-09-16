"""AP32 close-out, round 2 of /code-review — private paths readable by id, and notes
writable on other users' private milestones.

Found by auditing EVERY id-taking route (not just the mutations the AP32 guard
enumerates):

* `GET /paths/{path_id}` and `GET /paths/{path_id}/calendar.ics` took no credentials at
  all and never checked `is_public`, so anyone could step through ids and read every
  private path's milestones, progress and XP.
* `PUT /milestones/{milestone_id}/note` saved the caller's own note on ANY milestone.
  A non-private note left on someone else's private path would then appear on that
  path's public share view the day its owner shares it.

The policy these tests pin:
  * a PUBLIC path is readable by anyone (the share flow depends on it);
  * a PRIVATE path is readable only by its owner: the signed-in user who owns it, or the
    anonymous visitor holding its `ap_anon_id` cookie (AP9's pre-signup flow);
  * anyone else gets **404**, never 403, so ids cannot be enumerated;
  * a note may be written on a milestone the caller owns, or one on a public path.
Each "stranger" test was run against the unfixed code first and failed there.
"""
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from database import SessionLocal  # noqa: E402
from models import LearningPath, MilestoneNote  # noqa: E402
from test_route_authz_ap32 import (  # noqa: E402,F401 — fixtures are collected by name
    _make_path, _sign_in, _user_id, captured_tokens, client,
)


def _set_public(path_id):
    db = SessionLocal()
    try:
        db.query(LearningPath).filter(LearningPath.id == path_id).update({"is_public": True})
        db.commit()
    finally:
        db.close()


def _note_count(milestone_id):
    db = SessionLocal()
    try:
        return db.query(MilestoneNote).filter(MilestoneNote.milestone_id == milestone_id).count()
    finally:
        db.close()


# ── GET /paths/{path_id} ───────────────────────────────────────────────────────
def test_anon_cannot_read_a_private_path(client):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie", title="Secret plan")
    res = client.get(f"/api/paths/{pid}")
    assert res.status_code == 404, res.text
    assert "Secret plan" not in res.text


def test_stranger_cannot_read_a_private_path(client, captured_tokens):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie", title="Secret plan")
    _sign_in(client, captured_tokens, "path-reader@example.com")
    res = client.get(f"/api/paths/{pid}")
    assert res.status_code == 404, res.text
    assert "Secret plan" not in res.text


def test_owner_can_read_their_private_path(client, captured_tokens):
    _sign_in(client, captured_tokens, "path-owner@example.com")
    pid, _ = _make_path(owner_user_id=_user_id("path-owner@example.com"), title="Mine")
    res = client.get(f"/api/paths/{pid}")
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Mine"


def test_anon_cookie_owner_can_read_their_private_path(client):
    """AP9: a visitor who generated a path before signing up keeps it via the cookie."""
    pid, _ = _make_path(owner_user_id=None, anon_id="my-cookie", title="Pre-signup")
    client.cookies.set("ap_anon_id", "my-cookie")
    res = client.get(f"/api/paths/{pid}")
    assert res.status_code == 200, res.text


def test_anyone_can_read_a_public_path(client):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie", title="Shared plan")
    _set_public(pid)
    res = client.get(f"/api/paths/{pid}")
    assert res.status_code == 200, res.text


def test_missing_path_is_404(client):
    assert client.get("/api/paths/999999").status_code == 404


# ── GET /paths/{path_id}/calendar.ics ───────────────────────────────────────────
def test_anon_cannot_download_a_private_paths_calendar(client):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie", title="Secret plan")
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 404, res.text[:200]
    assert "BEGIN:VCALENDAR" not in res.text


def test_stranger_cannot_download_a_private_paths_calendar(client, captured_tokens):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie", title="Secret plan")
    _sign_in(client, captured_tokens, "cal-reader@example.com")
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 404, res.text[:200]


def test_owner_can_download_their_calendar(client, captured_tokens):
    _sign_in(client, captured_tokens, "cal-owner@example.com")
    pid, _ = _make_path(owner_user_id=_user_id("cal-owner@example.com"))
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 200, res.text[:200]
    assert "BEGIN:VCALENDAR" in res.text


def test_anon_cookie_owner_can_download_their_calendar(client):
    """The calendar route takes `request` with a default; this proves FastAPI still
    injects it — `_authorize_path` reads the anon cookie from it, and the signed-in
    owner test above never touches that branch."""
    pid, _ = _make_path(owner_user_id=None, anon_id="my-cookie")
    client.cookies.set("ap_anon_id", "my-cookie")
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 200, res.text[:200]
    assert "BEGIN:VCALENDAR" in res.text


def test_anyone_can_download_a_public_paths_calendar(client):
    pid, _ = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _set_public(pid)
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 200, res.text[:200]


# ── PUT /milestones/{milestone_id}/note ─────────────────────────────────────────
def test_stranger_cannot_note_someone_elses_private_milestone(client, captured_tokens):
    _, ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "graffiti@example.com")
    res = client.put(f"/api/milestones/{ms}/note", json={"content": "graffiti"})
    assert res.status_code == 404, res.text
    assert _note_count(ms) == 0  # read back: nothing was written


def test_stranger_cannot_note_even_a_PUBLIC_path(client, captured_tokens):
    """Notes are OWNER-ONLY (review round 3). "Owner or public" was tried first and is
    wrong: `NoteUpsert.is_private` defaults to False, so a stranger's note publishes onto
    the owner's share page through `/paths/{id}/notes/public` — spam and content injection
    on someone else's page. No UI flow writes a note on a path you do not own:
    SharePathPage only READS `getPublicNotes`, and the editor lives on your own path."""
    pid, ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _set_public(pid)
    _sign_in(client, captured_tokens, "reader-notes@example.com")
    res = client.put(f"/api/milestones/{ms}/note", json={"content": "useful tip"})
    assert res.status_code == 404, res.text
    assert _note_count(ms) == 0


def test_owner_can_note_their_own_private_milestone(client, captured_tokens):
    _sign_in(client, captured_tokens, "note-owner@example.com")
    _, ms = _make_path(owner_user_id=_user_id("note-owner@example.com"))
    res = client.put(f"/api/milestones/{ms}/note", json={"content": "my own note"})
    assert res.status_code == 200, res.text
    assert _note_count(ms) == 1

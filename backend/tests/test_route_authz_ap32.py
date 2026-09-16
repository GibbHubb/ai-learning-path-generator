"""AP32 — the five mutating routes that took no auth at all.

Confirmed against the LIVE deployment on 2026-09-03 before any code changed: an
anonymous `DELETE /api/paths/999999999` came back `{"detail":"Learning path not
found"}` — the *handler's* wording, not FastAPI's `"Not Found"` — proving the request
reached the handler. With a real id it would have deleted someone's path.

Every test here is written to fail against the pre-fix code. The `_authorize_path`
guard raises **404, not 403**, so a wrong owner cannot tell "exists but not yours" from
"no such row" — the same rule the rest of `routes.py` already applies.
"""
import ast
import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from database import Base, engine  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone  # noqa: E402
from database import SessionLocal  # noqa: E402
import routes as routes_module  # noqa: E402


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


def test_wrong_owner_gets_404_on_feedback(client, captured_tokens):
    """The route criterion 2 names explicitly alongside share/delete/milestone.
    A wrong-owner request must never reach adjust_difficulty (mocked so a stray
    call would be visible, not silently swallowed by a real network failure)."""
    _, victim_ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "attacker4@example.com")
    called = []
    with patch("routes.adjust_difficulty", lambda **kw: called.append(1) or []):
        res = client.post(f"/api/milestones/{victim_ms}/feedback",
                          json={"milestone_id": victim_ms, "feedback": "too_hard"})
    assert res.status_code == 404, res.text
    assert called == []
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


def test_signed_in_owner_can_still_get_feedback_recorded(client, captured_tokens):
    """Criterion 3's fourth data route. feedback="just_right" never reaches
    adjust_difficulty (see routes.py:1060), so this exercises the owner-200 path
    with no AI call and no mock needed."""
    me = _sign_in(client, captured_tokens, "owner4@example.com")
    _, milestone_id = _make_path(owner_user_id=me["id"])
    res = client.post(f"/api/milestones/{milestone_id}/feedback",
                      json={"milestone_id": milestone_id, "feedback": "just_right"})
    assert res.status_code == 200, res.text


def test_signed_in_owner_feedback_regeneration_reaches_adjust_difficulty(client, captured_tokens):
    """The regeneration branch (too_hard/too_easy) — mocked per §8 of the plan so
    the owner-200 case is proven without a paid gpt-4o call. Needs a SECOND,
    later-order incomplete milestone: routes.py:1073-1076 only regenerates
    milestones with order > the anchor's, and _make_path only makes one."""
    me = _sign_in(client, captured_tokens, "owner5@example.com")
    path_id, milestone_id = _make_path(owner_user_id=me["id"])
    db = SessionLocal()
    try:
        db.add(Milestone(learning_path_id=path_id, title="m2", description="d",
                          order=2, estimated_hours=1.0, resources="[]", completed=False))
        db.commit()
    finally:
        db.close()
    called = []

    def fake_adjust(**kwargs):
        called.append(kwargs)
        return [{"title": "new m", "description": "d", "estimated_hours": 1, "resources": []}]

    with patch("routes.adjust_difficulty", fake_adjust):
        res = client.post(f"/api/milestones/{milestone_id}/feedback",
                          json={"milestone_id": milestone_id, "feedback": "too_hard"})
    assert res.status_code == 200, res.text
    assert len(called) == 1


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


# ── the guard: no NEXT unguarded route slips in unnoticed ───────────────────────
#
# Rewritten at close-out after /code-review showed the first version passed routes that
# authorize nothing. It accepted `require_user` (sign-in, not ownership) on id routes,
# which is exactly how the three quiz routes let any signed-in user act on anyone's
# milestone, and it matched a guard NAME anywhere in the function (a bare reference, a
# nested helper that never runs, a call on the wrong id, a `_load_owned_*` look-alike).
import re  # noqa: E402

_ROUTES_PATH = os.path.join(BACKEND_DIR, "routes.py")
_MAIN_PATH = os.path.join(BACKEND_DIR, "main.py")
# Round 4: routes live in TWO files. Parsing only routes.py meant a route added to
# main.py (which already serves /u/{user_id}) was invisible to this guard.
_SOURCE_FILES = (_ROUTES_PATH, _MAIN_PATH)


def _all_sources():
    return [io_open_utf8(p) for p in _SOURCE_FILES]


def io_open_utf8(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()
_OWNERSHIP_HELPERS = {"_load_owned_milestone", "_load_owned_task", "_load_owned_path", "_authorize_path"}
# A READ may also be satisfied by the public-or-owner gate. Deliberately NOT in the set
# above: "public counts" is not ownership, so a MUTATION guarded only by it would be
# open to any stranger on a public path (review round 3).
# `_public_user_or_404` lives in main.py and refuses unless the profile is public —
# a READ gate, never ownership.
_READ_HELPERS = _OWNERSHIP_HELPERS | {"_authorize_path_read", "_public_user_or_404"}
# These take the loaded ROW (or an expression that builds it), not the id, so they are
# matched by taint: something in the argument must derive from the route own id param.
_PATH_OBJECT_HELPERS = {"_authorize_path", "_authorize_path_read"}
# A gate with NO caller argument: it refuses on a public flag, so there is no
# `current` to pass. `_public_user_or_404(user_id, db)` is the only one today; the
# argument rule below would otherwise flag two correctly-gated main.py routes.
_NO_CALLER_HELPERS = {"_public_user_or_404"}
_DB_WRITES = {"add", "delete", "commit", "merge", "flush"}


def _walk_own_body(fn):
    """Every node in the handler's OWN body — not inside a nested def/lambda/class,
    which may never run."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _iter_mutation_handlers(src):
    """Yield (METHOD, path, FunctionDef) for every @router.get/patch/post/put/delete in
    `src`. GET is included since review round 3: GET /paths/{id} and its calendar export
    leaked every private path by id, and a mutations-only guard could not see them."""
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in ("get", "patch", "post", "put", "delete")
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id in ("router", "app")  # main.py mounts on `app`
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                yield dec.func.attr.upper(), dec.args[0].value, node


def _owns_route_id(fn, params, helpers=_OWNERSHIP_HELPERS):
    """True only if the handler, in its own body, CALLS an ownership helper on a value
    derived from one of the route's own id params:
      * `_load_owned_*(<param>, ...)`: the first argument IS the param;
      * `_authorize_path(<var>, ...)`: `<var>` was assigned (possibly transitively,
        e.g. milestone -> its path) from an expression that uses the param.
    """
    tainted = set(params)
    changed = True
    while changed:  # transitive: path = query(... milestone.learning_path_id), milestone = query(... milestone_id)
        changed = False
        for n in _walk_own_body(fn):
            if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id in tainted
                                                 for x in ast.walk(n.value)):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id not in tainted:
                        tainted.add(t.id)
                        changed = True
    first_write = next((i for i, st in enumerate(fn.body)
                        if any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                               and x.func.attr in _DB_WRITES and isinstance(x.func.value, ast.Name)
                               and x.func.value.id == "db" for x in ast.walk(st))), len(fn.body))
    for st in fn.body[:first_write]:
        # Only a statement the handler ALWAYS executes counts: a bare call or an
        # assignment of one, at the top of its own body. A call under `if False:`, inside
        # `try/except: pass`, or after the delete/commit is not a guard.
        n = st.value if isinstance(st, (ast.Expr, ast.Assign)) else None
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in helpers):
            continue
        if n.func.id not in _NO_CALLER_HELPERS:
            if len(n.args) < 2 or not (isinstance(n.args[1], ast.Name)
                                       and n.args[1].id == "current"):
                continue  # ownership must be checked against the CALLER, not another user
        first = n.args[0]
        if n.func.id in _PATH_OBJECT_HELPERS:
            # The path object may be a variable OR an inline expression, e.g.
            # `_authorize_path(db.query(LearningPath).filter(LearningPath.id ==
            # milestone.learning_path_id).first(), ...)` — accepted when anything in it
            # derives from the route's id. A bare reference (`_ = _authorize_path`) is
            # not a Call, so it never reaches this branch.
            if any(isinstance(x, ast.Name) and x.id in tainted for x in ast.walk(first)):
                return True
        elif isinstance(first, ast.Name) and first.id in params:
            return True
    return False


def _unguarded(src, public, scoped_inline, public_reads=frozenset()):
    out = []
    for method, path, fn in _iter_mutation_handlers(src):
        if (method, path) in public:
            continue
        # a "read" exemption excuses a READ. Listing a POST there must not disarm
        # ownership for it (round 4).
        if method == "GET" and (method, path) in public_reads:
            continue
        params = set(re.findall(r"\{(\w+)\}", path))
        if params:
            # a GET may be satisfied by the read gate (public OR owner); a mutation may not
            helpers = _READ_HELPERS if method == "GET" else _OWNERSHIP_HELPERS
            if (method, path) in scoped_inline or _owns_route_id(fn, params, helpers):
                continue
            out.append(f"{method} {path}")
        elif method != "GET" and not any(isinstance(n, ast.Name) and n.id == "require_user"
                                         for n in ast.walk(fn.args)):
            out.append(f"{method} {path}")  # an id-less GET lists only the caller's own rows
    return sorted(out)


_CONTROL_SRC = '''
@router.post("/things/{thing_id}/signin_only")
async def a(thing_id: int, current=Depends(require_user)):
    return 1

@router.post("/things/{thing_id}/referenced_not_called")
async def b(thing_id: int, current=Depends(require_user)):
    _ = _authorize_path

@router.post("/things/{thing_id}/wrong_id")
async def c(thing_id: int, other_id: int, current=Depends(require_user)):
    _load_owned_path(other_id, current, db)

@router.post("/things/{thing_id}/nested_never_runs")
async def d(thing_id: int, current=Depends(require_user)):
    def never_called():
        _load_owned_path(thing_id, current, db)

@router.post("/things/{thing_id}/lookalike")
async def e(thing_id: int, current=Depends(require_user)):
    _load_owned_pathway(thing_id, current, db)

@router.post("/things/no_id_no_auth")
async def f(payload: dict):
    return 1

@router.post("/things/{thing_id}/ok_load_owned")
async def ok1(thing_id: int, current=Depends(require_user)):
    _load_owned_path(thing_id, current, db)

@router.post("/things/{thing_id}/ok_authorize_transitive")
async def ok2(thing_id: int, request, current=None):
    child = db.query(C).filter(C.id == thing_id).first()
    row = db.query(X).filter(X.id == child.parent_id).first()
    _authorize_path(row, current, request)

@router.post("/things/no_id_signed_in")
async def ok3(payload: dict, current=Depends(require_user)):
    return 1

@router.post("/things/{thing_id}/ok_authorize_inline_expr")
async def ok4(thing_id: int, request, current=None):
    child = db.query(C).filter(C.id == thing_id).first()
    _authorize_path(db.query(X).filter(X.id == child.parent_id).first(), current, request)

@router.post("/things/{thing_id}/inline_expr_not_from_id")
async def g(thing_id: int, other_id: int, request, current=None):
    _authorize_path(db.query(X).filter(X.id == other_id).first(), current, request)

@router.post("/things/{thing_id}/after_commit")
async def h(thing_id: int, current=Depends(require_user)):
    db.delete(db.query(X).filter(X.id == thing_id).first())
    db.commit()
    _load_owned_path(thing_id, current, db)

@router.post("/things/{thing_id}/under_if_false")
async def i(thing_id: int, current=Depends(require_user)):
    if False:
        _load_owned_path(thing_id, current, db)

@router.post("/things/{thing_id}/swallowed")
async def j(thing_id: int, current=Depends(require_user)):
    try:
        _load_owned_path(thing_id, current, db)
    except HTTPException:
        pass

@router.post("/things/{thing_id}/other_user_passed")
async def k(thing_id: int, other_user, current=Depends(require_user)):
    _load_owned_path(thing_id, other_user, db)

@router.put("/things/{thing_id}/put_signin_only")
async def l(thing_id: int, current=Depends(require_user)):
    return 1

@router.get("/things/{thing_id}/get_wide_open")
async def m(thing_id: int):
    return db.query(X).filter(X.id == thing_id).first()

@router.get("/things/{thing_id}/get_read_gated")
async def n(thing_id: int, request, current=None):
    row = db.query(X).filter(X.id == thing_id).first()
    _authorize_path_read(row, current, request)

@router.post("/things/{thing_id}/mutation_with_read_gate_only")
async def o(thing_id: int, request, current=None):
    row = db.query(X).filter(X.id == thing_id).first()
    _authorize_path_read(row, current, request)
    db.delete(row)
'''


def test_the_guard_checker_rejects_every_way_to_fake_a_guard():
    """Positive AND negative controls for the checker itself, on synthetic source:
    each of the review's five bypasses must be flagged, and each real guard shape
    must pass. A green guard test below means nothing unless this holds.

    `ok_authorize_inline_expr` is the shape PATCH /milestones/{id} and feedback use;
    `inline_expr_not_from_id` keeps that widened branch able to fail."""
    assert _unguarded(_CONTROL_SRC, set(), set()) == sorted([
        "POST /things/{thing_id}/signin_only",
        "POST /things/{thing_id}/referenced_not_called",
        "POST /things/{thing_id}/wrong_id",
        "POST /things/{thing_id}/nested_never_runs",
        "POST /things/{thing_id}/lookalike",
        "POST /things/{thing_id}/inline_expr_not_from_id",
        "POST /things/{thing_id}/after_commit",
        "POST /things/{thing_id}/under_if_false",
        "POST /things/{thing_id}/swallowed",
        "POST /things/{thing_id}/other_user_passed",
        "PUT /things/{thing_id}/put_signin_only",
        "GET /things/{thing_id}/get_wide_open",
        "POST /things/{thing_id}/mutation_with_read_gate_only",
        "POST /things/no_id_no_auth",
    ])


def test_the_route_enumerator_actually_finds_routes():
    """A green guard test would prove nothing if the enumerator found zero routes."""
    found = {(m, p) for m, p, _ in _iter_mutation_handlers(open(_ROUTES_PATH, encoding="utf-8").read())}
    assert ("DELETE", "/paths/{path_id}") in found
    assert ("PATCH", "/milestones/{milestone_id}") in found
    assert len(found) >= 15


def test_every_mutation_route_is_guarded_or_allowlisted():
    """Criterion 6. Every @router.patch/post/delete in routes.py either:
      * takes an id and calls an ownership helper ON THAT ID in its own body, or
      * takes no id and requires sign-in, or
      * is on routes._PUBLIC_MUTATIONS / routes._ID_ROUTES_SCOPED_INLINE, each with a
        written reason.
    Sign-in alone is not ownership for a route that names someone's row.

    WHAT THIS CAN AND CANNOT PROVE (review round 2). It is a static tripwire for the
    common failure — a new route that FORGETS the ownership check, or checks too late /
    conditionally / against the wrong user. It cannot prove a check is CORRECT: a value-
    level trick such as `.filter(LearningPath.id != milestone_id)` or authorising the
    caller's own row and then mutating a different one still passes. The behavioural
    tests in this file and test_route_authz_private_reads.py (a real stranger, a real
    404, the row read back) are the proof; this test only stops the next omission."""
    unguarded = []
    for src in _all_sources():
        unguarded += _unguarded(src, routes_module._PUBLIC_MUTATIONS,
                                getattr(routes_module, "_ID_ROUTES_SCOPED_INLINE", set()),
                                getattr(routes_module, "_PUBLIC_READS", set()))
    unguarded = sorted(unguarded)
    assert unguarded == [], (
        "route(s) with no ownership guard on their own id and not allowlisted: "
        + ", ".join(unguarded))


_REFUSAL = re.compile(r"not\s+\w+\.is_public(?:_profile)?\b|user_id\s*==\s*current\.id")


def _code_only(source):
    """Source with comments and string literals BLANKED IN PLACE, so a check named in a
    comment or docstring cannot satisfy the refusal pattern — while real code keeps its
    exact spelling.

    2026-09-16: the first version joined tokens with spaces, which turned
    `src.is_public` into `src . is_public` and made the reason test fail against an
    intact routes.py. Found by running it, not by reading it."""
    import io as _io
    import tokenize as _tokenize
    lines = source.splitlines(keepends=True)
    try:
        toks = list(_tokenize.generate_tokens(_io.StringIO(source).readline))
    except (_tokenize.TokenError, IndentationError):
        return source  # unparseable fragment: fall back rather than pass silently
    for tok in toks:
        if tok.type not in (_tokenize.COMMENT, _tokenize.STRING):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            line = lines[row - 1]
            a = scol if row == srow else 0
            b = ecol if row == erow else len(line)
            # everything from `b` on is untouched, so a trailing newline survives
            lines[row - 1] = line[:a] + " " * max(0, b - a) + line[b:]
    return "".join(lines)


def _handler_source(src, method, path):
    for m, p, fn in _iter_mutation_handlers(src):
        if (m, p) == (method, path):
            return ast.get_source_segment(src, fn)
    raise AssertionError(f"route not found: {method} {path}")


def test_inline_scoped_exemptions_still_carry_their_reason():
    '''Review round 3: `_ID_ROUTES_SCOPED_INLINE` exempts a route FOREVER and its
    reason lives only in a comment. Delete fork's `is_public` line and the guard stays
    green while private paths become forkable. So pin each exemption's actual check.'''
    src = open(_ROUTES_PATH, encoding="utf-8").read()
    fork = _handler_source(src, "POST", "/paths/{path_id}/fork")
    assert _REFUSAL.search(_code_only(fork)), (
        "fork is exempt BECAUSE it REFUSES private paths (if not src.is_public) — that check is gone")
    note = _handler_source(src, "DELETE", "/milestones/{milestone_id}/note")
    assert re.search(r"user_id\s*==\s*current\.id", note), (
        "DELETE note is exempt BECAUSE it is scoped to the caller's own row — that scoping is gone")
    for method, path in routes_module._PUBLIC_READS:
        body = _handler_source(src, method, path)
        assert _REFUSAL.search(_code_only(body)), (
            f"{method} {path} is on _PUBLIC_READS but REFUSES nothing")


def test_a_refusal_named_only_in_a_comment_does_not_count():
    """Control for _code_only: the check is deleted but still named in a comment and a
    docstring — the reason test must NOT accept that."""
    lines = [
        '@router.post("/paths/{path_id}/fork")',
        "async def fork_path(path_id: int, current=Depends(require_user)):",
        '    # was: if not src.is_public: raise HTTPException(403)',
        '    \"\"\"Forking refuses private paths (if not src.is_public).\"\"\"',
        "    src = db.query(LearningPath).filter(LearningPath.id == path_id).first()",
        "    return src",
    ]
    fake = chr(10).join(lines) + chr(10)
    body = _handler_source(fake, "POST", "/paths/{path_id}/fork")
    assert _REFUSAL.search(body), "the raw text DOES mention it — that is the trap"
    assert not _REFUSAL.search(_code_only(body)), (
        "a refusal named only in a comment/docstring was accepted as a real check")


def test_a_caller_less_gate_counts_for_a_read_but_not_for_a_mutation():
    """`_public_user_or_404(user_id, db)` has no caller argument: it refuses on a public
    flag. That satisfies a GET. The same call must NOT satisfy a mutation, which needs
    ownership, not visibility."""
    lines = [
        '@app.get("/u/{user_id}/thing")',
        "async def read_thing(user_id: int, db=Depends(get_db)):",
        "    user = _public_user_or_404(user_id, db)",
        "    return user",
        '',
        '@app.post("/u/{user_id}/thing")',
        "async def write_thing(user_id: int, db=Depends(get_db)):",
        "    user = _public_user_or_404(user_id, db)",
        "    db.commit()",
    ]
    src = chr(10).join(lines) + chr(10)
    assert _unguarded(src, set(), set(), set()) == ["POST /u/{user_id}/thing"]


def test_a_post_on_the_public_reads_list_is_not_exempted():
    """Control: _PUBLIC_READS excuses reads. A mutation listed there must still be
    flagged, or the allowlist becomes a way to disarm ownership."""
    lines = [
        '@router.post("/things/{thing_id}/sneaky")',
        "async def sneaky(thing_id: int):",
        "    return db.query(X).filter(X.id == thing_id).first()",
    ]
    src = chr(10).join(lines) + chr(10)
    listed = {("POST", "/things/{thing_id}/sneaky")}
    assert _unguarded(src, set(), set(), listed) == [
        "POST /things/{thing_id}/sneaky"], "a POST on the reads allowlist was exempted"


def test_routes_defined_in_main_py_are_enumerated_too():
    """Round 4: the guard parsed only routes.py, so main.py's /u/{user_id} routes were
    never checked. Both files are enumerated now."""
    found = set()
    for src in _all_sources():
        found |= {(m, p) for m, p, _ in _iter_mutation_handlers(src)}
    assert ("GET", "/u/{user_id}") in found, "main.py routes are not being enumerated"
    assert ("GET", "/paths/{path_id}") in found


def test_the_reason_check_can_fail():
    '''Control: the same assertion against a handler whose check was removed must
    fail, or it is only a rumour of a check.'''
    # 2026-09-16: the first version of this control used a handler with NO mention of
    # is_public, so it passed trivially. The real failure shape MENTIONS the flag (on
    # the row it creates) while refusing nothing — that is what stayed green when
    # fork's `if not src.is_public:` was neutralised.
    fake_lines = [
        '@router.post("/paths/{path_id}/fork")',
        "async def fork_path(path_id: int, current=Depends(require_user)):",
        "    src = db.query(LearningPath).filter(LearningPath.id == path_id).first()",
        "    new_path = LearningPath(title=src.title, is_public=False)",
        "    return new_path",
    ]
    fake = chr(10).join(fake_lines) + chr(10)
    body = _handler_source(fake, "POST", "/paths/{path_id}/fork")
    assert "is_public" in body, "the control must still MENTION the flag, or it proves nothing"
    assert not _REFUSAL.search(body), (
        "the refusal pattern matched a handler that refuses nothing — it cannot fail")


# ── quiz routes: sign-in is not ownership (found by /code-review at AP32 close) ──
import json  # noqa: E402

from models import User  # noqa: E402


def _add_quiz(milestone_id):
    """Two MCQs, answers [0, 1] — the same JSON shape quizzes._serialise writes."""
    db = SessionLocal()
    try:
        from models import MilestoneQuiz
        db.add(MilestoneQuiz(milestone_id=milestone_id, questions_json=json.dumps([
            {"question": "Q1", "options": ["a", "b", "c", "d"], "correct_index": 0, "explanation": "e"},
            {"question": "Q2", "options": ["a", "b", "c", "d"], "correct_index": 1, "explanation": "e"},
        ])))
        db.commit()
    finally:
        db.close()


def _user_id(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).first().id
    finally:
        db.close()


def test_wrong_owner_cannot_read_someone_elses_quiz(client, captured_tokens):
    """The GET ships `correct_index` to the caller and generates via Claude on a miss."""
    _, victim_ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _add_quiz(victim_ms)
    _sign_in(client, captured_tokens, "quiz-reader@example.com")
    called = []
    with patch("routes.get_or_generate_quiz", lambda *a, **k: called.append(1) or (None, "x")):
        res = client.get(f"/api/milestones/{victim_ms}/quiz")
    assert res.status_code == 404, res.text
    assert "correct_index" not in res.text
    assert called == []


def test_wrong_owner_cannot_regenerate_someone_elses_quiz(client, captured_tokens):
    """A paid Claude generation, spent on another user's milestone."""
    _, victim_ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _sign_in(client, captured_tokens, "quiz-regen@example.com")
    called = []
    with patch("routes.get_or_generate_quiz", lambda *a, **k: called.append(1) or (None, "x")):
        res = client.post(f"/api/milestones/{victim_ms}/quiz/regenerate")
    assert res.status_code == 404, res.text
    assert called == []


def test_wrong_owner_cannot_complete_someone_elses_milestone_by_quiz(client, captured_tokens):
    """Passing the quiz completes the milestone, ticks its tasks and awards XP."""
    _, victim_ms = _make_path(owner_user_id=None, anon_id="victim-cookie")
    _add_quiz(victim_ms)
    _sign_in(client, captured_tokens, "quiz-cheat@example.com")
    res = client.post(f"/api/milestones/{victim_ms}/quiz/attempt", json={"answers": [0, 1]})
    assert res.status_code == 404, res.text
    assert _milestone_completed(victim_ms) is False


def test_owner_can_still_pass_their_own_quiz(client, captured_tokens):
    """The fix must not be too strict: the owner's pass still completes the milestone."""
    _sign_in(client, captured_tokens, "quiz-owner@example.com")
    _, ms = _make_path(owner_user_id=_user_id("quiz-owner@example.com"))
    _add_quiz(ms)
    res = client.post(f"/api/milestones/{ms}/quiz/attempt", json={"answers": [0, 1]})
    assert res.status_code == 200, res.text
    assert res.json()["passed"] is True
    assert _milestone_completed(ms) is True


def test_owner_can_still_read_their_own_quiz(client, captured_tokens):
    """The fix must not lock the owner out. The quiz service is stubbed: its own
    description-length rule ("too short to generate a meaningful quiz") is not what
    this test is about, and a red from it would read like a broken guard."""
    from types import SimpleNamespace
    _sign_in(client, captured_tokens, "quiz-owner2@example.com")
    _, ms = _make_path(owner_user_id=_user_id("quiz-owner2@example.com"))
    cached = SimpleNamespace(milestone_id=ms, updated_at=None, questions_json=json.dumps([
        {"question": "Q1", "options": ["a", "b"], "correct_index": 0, "explanation": "e"},
        {"question": "Q2", "options": ["a", "b"], "correct_index": 1, "explanation": "e"},
    ]))
    with patch("routes.get_or_generate_quiz", lambda *a, **k: (cached, None)):
        res = client.get(f"/api/milestones/{ms}/quiz")
    assert res.status_code == 200, res.text
    assert len(res.json()["questions"]) == 2

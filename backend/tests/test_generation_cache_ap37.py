"""AP37 — the generation cache is a durable, cross-process, normalised,
TTL-bounded `generation_cache` table instead of a process-local `CACHE = {}`
dict (which almost never hit on Vercel: every recycled function instance got
its own empty dict, it never evicted, and it matched almost nothing because
the key was a raw, un-normalised string).

No network: the OpenAI client is replaced with a recorder (same technique as
test_usage_ap36.py) so `call_count` is the number of times the mocked
provider's `.create()` actually ran — the number every criterion below is
checked against. `replies` maps a MODEL string to its reply, so the main
(path-generation) and light (enrichment) models can be driven independently —
enrichment is explicitly OUT OF SCOPE for this cache (plan §4) and just needs
to not blow up; it is not what these assertions are about.
"""
import importlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import ai_service  # noqa: E402
import llm  # noqa: E402
from database import Base, engine, SessionLocal  # noqa: E402
from models import GenerationCache  # noqa: E402
from main import app  # noqa: E402

REQ = {"goal": "Learn Rust", "experience_level": "beginner", "time_commitment": "5-10 hours/week"}


def _valid_path_json(title: str = "AP37 test path", n_milestones: int = 1) -> str:
    return json.dumps({
        "path_title": title,
        "path_description": "D",
        "category": "Programming",
        "milestones": [
            {"title": f"M{i}", "description": "d", "estimated_hours": 1, "resources": []}
            for i in range(n_milestones)
        ],
    })


class _Recorder:
    """Stands in for openai.OpenAI (see test_usage_ap36.py's _Recorder). `replies`
    maps a MODEL string to the reply it returns; anything not in `replies` gets
    `default_reply` (a harmless empty JSON array — the shape the light/enrichment
    model's json_object=False call expects, so it parses without a warning but
    never actually enriches anything, which this file does not test)."""
    instances: list["_Recorder"] = []
    replies: dict[str, str] = {}
    default_reply = "[]"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        _Recorder.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = _Recorder.replies.get(kwargs.get("model"), _Recorder.default_reply)
        choice = SimpleNamespace(message=SimpleNamespace(content=reply), finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


def _calls_for(model: str) -> int:
    return sum(1 for r in _Recorder.instances for c in r.calls if c.get("model") == model)


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    _Recorder.instances = []
    _Recorder.replies = {llm.DEFAULT_GEMINI_MODEL: _valid_path_json()}
    _Recorder.default_reply = "[]"
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _Recorder(**kw))
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_LIGHT_MODEL", raising=False)
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    monkeypatch.delenv("CACHE_TTL_DAYS", raising=False)
    return _Recorder


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _cache_rows() -> list[GenerationCache]:
    db = SessionLocal()
    try:
        return db.query(GenerationCache).all()
    finally:
        db.close()


# ── criterion 1: a generation_cache table; two identical requests -> 1 call ──────

def test_two_identical_requests_produce_one_call_and_identical_titles(fake_openai):
    client = TestClient(app)
    res1 = client.post("/api/generate", json=REQ)
    res2 = client.post("/api/generate", json=REQ)

    assert res1.status_code == 200, res1.text
    assert res2.status_code == 200, res2.text
    assert res1.json()["title"] == res2.json()["title"] == "AP37 test path"
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1

    rows = _cache_rows()
    assert len(rows) == 1
    assert rows[0].model == llm.DEFAULT_GEMINI_MODEL
    assert rows[0].hit_count == 1  # bumped on the SECOND (cache-hit) request


# ── criterion 2: state survives across two independently constructed app instances ──

def test_cache_hit_holds_across_two_independently_constructed_app_instances(fake_openai, monkeypatch):
    """Mirrors test_rate_limit_ap35.py's identical technique: two separately
    constructed FastAPI `app` objects sharing only the database (never
    reimports `database`, so `engine`/`SessionLocal` — and the DB file — stay
    the same object). `ai_service`/`llm` are not reimported either, matching
    that file's convention; the point of this test is that the CACHE STATE
    lives in the database, not that the Python process was literally forked."""
    app1 = app
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main2 = importlib.import_module("main")
    app2 = main2.app
    assert app2 is not app1

    client1 = TestClient(app1)
    client2 = TestClient(app2)

    res1 = client1.post("/api/generate", json=REQ)
    res2 = client2.post("/api/generate", json=REQ)

    assert res1.status_code == 200, res1.text
    assert res2.status_code == 200, res2.text
    assert res1.json()["title"] == res2.json()["title"]
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1


# ── criterion 3: SHA-256 of a normalised tuple, model id included ────────────────

def test_cache_key_unit_normalisation_collides_model_id_does_not():
    k1 = ai_service._cache_key("learn python", "beginner", "5-10 hours/week", "en", "model-a")
    k2 = ai_service._cache_key("Learn  Python", "beginner", "5-10 hours/week", "en", "model-a")
    k3 = ai_service._cache_key(" learn python ", "beginner", "5-10 hours/week", "en", "model-a")
    assert k1 == k2 == k3

    k_other_model = ai_service._cache_key("learn python", "beginner", "5-10 hours/week", "en", "model-b")
    assert k_other_model != k1


def test_case_and_whitespace_variants_collide_end_to_end(fake_openai):
    client = TestClient(app)
    titles = []
    for goal in ("learn python", "Learn  Python", " learn python "):
        res = client.post("/api/generate", json={**REQ, "goal": goal})
        assert res.status_code == 200, res.text
        titles.append(res.json()["title"])

    assert len(set(titles)) == 1
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1


def test_changing_the_model_id_does_not_collide(fake_openai, monkeypatch):
    client = TestClient(app)
    res1 = client.post("/api/generate", json=REQ)
    assert res1.status_code == 200, res1.text
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1

    monkeypatch.setenv("LLM_MODEL", "gemini-other-model")
    _Recorder.replies["gemini-other-model"] = _valid_path_json(title="Other Model Path")

    res2 = client.post("/api/generate", json=REQ)
    assert res2.status_code == 200, res2.text
    assert res2.json()["title"] == "Other Model Path"
    assert _calls_for("gemini-other-model") == 1  # a REAL call, not served from the other model's row
    assert len(_cache_rows()) == 2


# ── criterion 4: entries older than CACHE_TTL_DAYS are not served ────────────────

def test_a_fresh_row_within_ttl_is_served_without_a_call(fake_openai):
    key_hash = ai_service._cache_key(
        REQ["goal"], REQ["experience_level"], REQ["time_commitment"], "en", llm.DEFAULT_GEMINI_MODEL)
    db = SessionLocal()
    try:
        db.add(GenerationCache(
            key_hash=key_hash, payload_json=_valid_path_json(title="FROM-CACHE"),
            model=llm.DEFAULT_GEMINI_MODEL, created_at=datetime.utcnow(), hit_count=0,
        ))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    res = client.post("/api/generate", json=REQ)
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "FROM-CACHE"
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 0  # positive control: proves the read path works at all


def test_entries_older_than_ttl_are_not_served(fake_openai, monkeypatch):
    monkeypatch.setenv("CACHE_TTL_DAYS", "30")
    key_hash = ai_service._cache_key(
        REQ["goal"], REQ["experience_level"], REQ["time_commitment"], "en", llm.DEFAULT_GEMINI_MODEL)
    db = SessionLocal()
    try:
        db.add(GenerationCache(
            key_hash=key_hash, payload_json=_valid_path_json(title="STALE"),
            model=llm.DEFAULT_GEMINI_MODEL,
            created_at=datetime.utcnow() - timedelta(days=31),
            hit_count=0,
        ))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    res = client.post("/api/generate", json=REQ)
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "AP37 test path"  # the FRESH reply, not the stale row's
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1  # a real call happened


# ── criterion 5: generate_stream makes exactly one provider call, empty cache ────

def test_stream_endpoint_makes_exactly_one_provider_call_with_empty_cache(fake_openai):
    client = TestClient(app)
    with client.stream("POST", "/api/generate/stream", json=REQ) as res:
        body = res.read().decode()

    assert "event: error" not in body, body
    assert "event: done" in body, body
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1


def test_stream_endpoint_second_identical_request_is_also_one_call_total(fake_openai):
    """The double-call this ticket closes (plan §2) only shows up when BOTH
    calls in the old code would have been cache misses — a single request
    already exercises that path (test above). This adds the miss-then-hit
    shape: two /generate/stream requests for the same input make ONE provider
    call in total, not two per request."""
    client = TestClient(app)
    with client.stream("POST", "/api/generate/stream", json=REQ) as res1:
        res1.read()
    with client.stream("POST", "/api/generate/stream", json=REQ) as res2:
        res2.read()
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1


# ── criterion 6: the in-process CACHE dict is gone ────────────────────────────────

def test_the_in_process_cache_dict_is_gone():
    assert not hasattr(ai_service, "CACHE")
    src_path = os.path.join(BACKEND_DIR, "ai_service.py")
    src = open(src_path, encoding="utf-8").read()
    assert not re.search(r"(?m)^CACHE\s*=", src), (
        "a module-level CACHE dict is back in ai_service.py")


# ── criterion 7: only a schema-VALID result is ever cached ───────────────────────

def test_invalid_payload_after_the_retry_writes_zero_cache_rows(monkeypatch):
    import openai

    class _AlwaysBadRecorder(_Recorder):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            choice = SimpleNamespace(
                message=SimpleNamespace(content='{"path_title": "t"}'),  # missing everything else
                finish_reason="stop",
            )
            return SimpleNamespace(choices=[choice],
                                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    _Recorder.instances = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _AlwaysBadRecorder(**kw))
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    client = TestClient(app)
    res = client.post("/api/generate", json=REQ)

    assert res.status_code == 502, res.text
    assert _cache_rows() == []


# ── AP37's hard constraint (a): the cache must not draw from the app's ───────────
# ── size-1 non-SQLite pool from inside a request that already holds it ───────────

def test_isolated_session_never_takes_the_apps_one_pooled_connection(monkeypatch):
    """Mirrors usage.py's identical guard (test_usage_ap36.py). generate_learning_path
    runs inside a request handler that may already hold the app's ONE pooled
    non-SQLite connection (database.py: pool_size=1, max_overflow=0); a second
    session from that same pool would deadlock. database.isolated_session() must
    use its own NullPool engine, not SessionLocal(), on a non-SQLite deployment."""
    import database
    from sqlalchemy.pool import NullPool

    monkeypatch.setattr(database, "is_sqlite", False)
    database._isolated_engine = None
    captured = {}

    def fake_create_engine(url, **kw):
        captured.update(kw)
        return database.engine  # harmless stand-in; only the kwargs are asserted

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    try:
        database.isolated_session().close()
    finally:
        database._isolated_engine = None

    assert captured.get("poolclass") is NullPool, (
        "the generation cache must not draw from the app's size-1 pool")


# ── AP37's hard constraint (b): a cache hit must not look like — or be — a ───────
# ── free/uncounted provider call; document which it is ───────────────────────────

def test_cache_hit_bypasses_over_budget_and_writes_no_model_calls_row(fake_openai, monkeypatch):
    """AP37 decision (documented in ai_service._read_cache): a cache hit makes
    NO usage.py/model_calls row (it consumed no provider quota, so a row would
    misrepresent what that table counts — "one row per llm.chat_json call")
    and is NOT subject to usage.over_budget_now(), because it never reaches
    chat_json at all. The cache's own effectiveness is tracked separately,
    via hit_count (see the first test in this file), not folded into
    provider-call accounting.

    Deliberately calls ai_service.generate_learning_path directly rather than
    going through POST /api/generate: main.py carries its OWN, separate,
    pre-existing per-REQUEST budget gate (AP36's `_over_budget` middleware,
    unchanged by this ticket) that runs before any handler — cache-aware or
    not — and would 503 a real request once the budget is spent regardless of
    whether it would have been a cache hit. That HTTP-layer gate is out of
    this ticket's scope (not in the plan's files touched); what AP37 owns and
    must prove is the narrower claim below: inside generate_learning_path
    itself, a hit never touches usage.py."""
    from models import ModelCall
    import usage

    result1 = ai_service.generate_learning_path(
        REQ["goal"], REQ["experience_level"], REQ["time_commitment"])
    assert result1.path_title == "AP37 test path"

    db = SessionLocal()
    try:
        rows_after_miss = db.query(ModelCall).count()
    finally:
        db.close()
    assert rows_after_miss == 1  # the miss WAS recorded by usage.py, as normal

    # Exhaust the daily call budget so a REAL provider call would now be
    # refused (usage.over_budget_now() -> True, chat_json raises LLMUnavailable).
    monkeypatch.setenv("DAILY_CALL_BUDGET", str(rows_after_miss))
    assert usage.over_budget_now() is True

    # A cache HIT must still succeed: it never calls chat_json, so it never
    # reaches the over_budget_now() check inside it.
    result2 = ai_service.generate_learning_path(
        REQ["goal"], REQ["experience_level"], REQ["time_commitment"])
    assert result2.path_title == result1.path_title
    assert _calls_for(llm.DEFAULT_GEMINI_MODEL) == 1  # still just the one real call

    db = SessionLocal()
    try:
        rows_after_hit = db.query(ModelCall).count()
    finally:
        db.close()
    assert rows_after_hit == rows_after_miss, (
        "a cache hit must not add a model_calls row")

    rows = _cache_rows()
    assert len(rows) == 1
    assert rows[0].hit_count == 1


# ── /code-review fixes, 2026-09-24 ──────────────────────────────────────────

def test_a_broken_cache_read_is_a_miss_not_a_500(monkeypatch):
    """A cache is an optimisation. A pooler blip or a missing table must fall
    through to a real generation, never surface as a 500 on a request the model
    could have served (the write path already swallowed this class of error)."""
    def boom():
        raise RuntimeError("pooler said no")
    monkeypatch.setattr(ai_service, "isolated_session", boom)
    assert ai_service._read_cache("any-key-hash") is None


def test_the_cache_key_carries_a_prompt_version():
    """Without it, a prompt fix keeps serving pre-fix answers for the whole TTL
    with no way to invalidate short of changing the model id."""
    args = ("learn rust", "beginner", "5-10 hours/week", "en", "gemini-3.6-flash")
    before = ai_service._cache_key(*args)
    original = ai_service.PROMPT_VERSION
    try:
        ai_service.PROMPT_VERSION = str(int(original) + 1)
        after = ai_service._cache_key(*args)
    finally:
        ai_service.PROMPT_VERSION = original
    assert before != after, "bumping PROMPT_VERSION must change the key"
    assert ai_service._cache_key(*args) == before, "and restoring it must restore the key"


def test_expired_rows_are_pruned(monkeypatch):
    """Rows for keys nobody asks for again are never read, so only the write
    path can evict them — on a free-tier database with a hard storage cap."""
    from models import GenerationCache

    db = SessionLocal()
    try:
        db.query(GenerationCache).delete()
        db.add(GenerationCache(key_hash="stale-row", model="m", payload_json="{}",
                               created_at=datetime.utcnow() - timedelta(days=99)))
        db.add(GenerationCache(key_hash="fresh-row", model="m", payload_json="{}",
                               created_at=datetime.utcnow()))
        db.commit()
        ai_service._prune_expired(db, datetime.utcnow() - timedelta(days=30))
        db.commit()
        left = {r.key_hash for r in db.query(GenerationCache).all()}
        assert left == {"fresh-row"}, f"expected only the fresh row, got {left}"
    finally:
        db.query(GenerationCache).delete()
        db.commit()
        db.close()

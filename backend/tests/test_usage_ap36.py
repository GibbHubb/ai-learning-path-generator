"""AP36 — nothing capped or counted what the model calls cost. Re-planned 2026-09-24
against AP46's llm.py: every AI feature goes through ONE choke point, `llm.chat_json`,
so this pins that chat_json (a) sends a real max_tokens/timeout to the client, (b)
records a `model_calls` row on both the success AND the failure path, (c) never guesses
a token count or a price, and that (d) the middleware budget gate stops a request
BEFORE the provider client is ever constructed.

No network: the OpenAI client is replaced with a recorder, same technique as
test_llm_ap46.py, extended here to vary the reply by MODEL (a single test can mock a
full-path call and a light/enrichment call differently) and to control whether the
mocked response carries a `usage` attribute at all — which shape Gemini returns
through the OpenAI SDK is not established, so both are asserted.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import llm  # noqa: E402
import usage  # noqa: E402
import ai_service  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import ModelCall  # noqa: E402
from main import app  # noqa: E402


class _Recorder:
    """Stands in for openai.OpenAI (see test_llm_ap46.py's _Recorder). `replies` maps a
    MODEL string to the reply it should return, so ONE test can drive the main model and
    the light model differently (the fan-out test needs a full path from one and a
    resource-array from the other); anything not in `replies` gets `default_reply`.
    `include_usage=False` means the mocked response carries NO `usage` attribute at
    all — the shape chat_json must survive without raising or dropping the row."""
    instances: list["_Recorder"] = []
    replies: dict[str, str] = {}
    default_reply = '{"ok": true}'
    include_usage = True
    usage_tokens = (11, 22)  # (prompt, completion) when include_usage

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        _Recorder.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = _Recorder.replies.get(kwargs.get("model"), _Recorder.default_reply)
        choice = SimpleNamespace(
            message=SimpleNamespace(content=reply),
            finish_reason="stop",
        )
        response = SimpleNamespace(choices=[choice])
        if _Recorder.include_usage:
            p, c = _Recorder.usage_tokens
            response.usage = SimpleNamespace(prompt_tokens=p, completion_tokens=c)
        return response


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    _Recorder.instances = []
    _Recorder.replies = {}
    _Recorder.default_reply = '{"ok": true}'
    _Recorder.include_usage = True
    _Recorder.usage_tokens = (11, 22)
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _Recorder(**kw))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_LIGHT_MODEL", raising=False)
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    return _Recorder


def _valid_path_json(n_milestones=1):
    return json.dumps({
        "path_title": "AP36 test path",
        "path_description": "D",
        "category": "Programming",
        "milestones": [
            {"title": f"M{i}", "description": "d", "estimated_hours": 1, "resources": []}
            for i in range(n_milestones)
        ],
    })


def _rows() -> list[ModelCall]:
    db = SessionLocal()
    try:
        return db.query(ModelCall).all()
    finally:
        db.close()


# ── criterion: max_tokens / timeout reach the client with the EXACT default values ──

def test_max_tokens_and_timeout_defaults_reach_the_client(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    llm.chat_json("sys", "user ap36 defaults main")
    llm.chat_json(None, "user ap36 defaults light", light=True)
    main_call, light_call = (c.calls[0] for c in fake_openai.instances)

    assert llm.DEFAULT_MAX_TOKENS == 4000
    assert llm.DEFAULT_LIGHT_MAX_TOKENS == 1024
    assert main_call["max_tokens"] == llm.DEFAULT_MAX_TOKENS
    assert light_call["max_tokens"] == llm.DEFAULT_LIGHT_MAX_TOKENS
    # not merely present — the EXACT value, and the same default for both calls
    assert main_call["timeout"] == llm.DEFAULT_TIMEOUT_SECONDS
    assert light_call["timeout"] == llm.DEFAULT_TIMEOUT_SECONDS


def test_max_tokens_and_timeout_can_be_overridden(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    llm.chat_json("sys", "user ap36 override", max_tokens=99, timeout=1.5)
    assert fake_openai.instances[0].calls[0]["max_tokens"] == 99
    assert fake_openai.instances[0].calls[0]["timeout"] == 1.5


# ── criterion: the model_calls table + one row per real generation, real tokens ──

def test_one_generation_writes_exactly_one_row_with_the_mocked_usage_tokens(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _Recorder.default_reply = _valid_path_json(1)
    _Recorder.usage_tokens = (123, 45)

    result = ai_service.generate_learning_path(
        "ap36 unique goal — one row real tokens", "beginner", "1-5 hours/week")

    assert result.path_title == "AP36 test path"
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    # tokens come from the mocked response.usage, NOT estimated from string length
    assert row.prompt_tokens == 123
    assert row.completion_tokens == 45
    assert row.ok is True
    assert row.model == llm.DEFAULT_GEMINI_MODEL
    assert row.light is False
    assert row.finish_reason == "stop"


# ── criterion: POST /api/generate's background fan-out is counted, not just the foreground call ──

def test_generate_endpoint_with_six_milestones_writes_seven_rows(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _Recorder.replies = {
        llm.DEFAULT_GEMINI_MODEL: _valid_path_json(6),
        llm.DEFAULT_GEMINI_LIGHT_MODEL: json.dumps(["Resource A", "Resource B"]),
    }

    client = TestClient(app)
    response = client.post("/api/generate", json={
        "goal": "ap36 unique goal — six milestone fan-out",
        "experience_level": "beginner",
        "time_commitment": "5-10 hours/week",
    })

    assert response.status_code == 200, response.text
    assert len(response.json()["milestones"]) == 6

    rows = _rows()
    assert len(rows) == 7, [(r.route, r.model, r.light) for r in rows]
    assert sum(1 for r in rows if r.light is False) == 1   # the main generation call
    assert sum(1 for r in rows if r.light is True) == 6    # one enrichment call per milestone
    assert all(r.ok for r in rows)


# ── criterion: a response with NO `usage` attribute still writes a row (tokens NULL, ok=True) ──

def test_missing_usage_attribute_still_writes_a_row_with_null_tokens(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _Recorder.include_usage = False

    llm.chat_json("sys", "user ap36 missing usage shape")

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].prompt_tokens is None
    assert rows[0].completion_tokens is None
    assert rows[0].ok is True


def test_present_usage_attribute_is_captured_exactly(monkeypatch, fake_openai):
    """The sibling of the test above — both shapes are asserted, since which one Gemini
    returns through the OpenAI SDK is not established (plan §9)."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _Recorder.include_usage = True
    _Recorder.usage_tokens = (7, 3)

    llm.chat_json("sys", "user ap36 present usage shape")

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 7
    assert rows[0].completion_tokens == 3


# ── criterion: a failed call writes a row with ok=False and the exception TYPE in finish_reason ──

def test_failed_call_writes_a_row_with_the_exception_type_name(monkeypatch):
    import openai

    def boom(**_kw):
        raise RuntimeError("simulated provider failure")

    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(openai, "OpenAI", lambda **_kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=boom))))

    with pytest.raises(RuntimeError):
        llm.chat_json("sys", "user ap36 failure path")

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].finish_reason == "RuntimeError"
    assert rows[0].prompt_tokens is None
    assert rows[0].completion_tokens is None


# ── /code-review, 2026-09-24: an empty `choices` list must not record ok=True and
# ── then crash with an uncaught IndexError one line later ──

def test_empty_choices_response_writes_a_failed_row_and_raises_cleanly(monkeypatch, fake_openai):
    """Regression for a review finding: a response with `choices == []` (e.g.
    safety-filtered) used to be recorded as ok=True (the finish_reason read was the
    only thing guarded) and THEN raise an unguarded IndexError on the next line. Now
    it is recorded as a failure and raises a typed RuntimeError instead."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    class _EmptyChoicesRecorder(_Recorder):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[])

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _EmptyChoicesRecorder(**kw))

    with pytest.raises(RuntimeError):
        llm.chat_json("sys", "user ap36 empty choices")

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].finish_reason == "empty_choices"


# ── /code-review, 2026-09-24: a malformed DAILY_CALL_BUDGET must not crash the app
# ── (warn_if_unbounded runs at IMPORT time) ──

def test_malformed_daily_budget_is_ignored_not_a_crash(caplog, monkeypatch):
    import logging
    monkeypatch.setenv("DAILY_CALL_BUDGET", "not-a-number")
    with caplog.at_level(logging.ERROR, logger="usage"):
        assert usage.daily_budget() is None  # falls back to unlimited, does not raise
    errors = [r.getMessage() for r in caplog.records if r.name == "usage"]
    assert len(errors) == 1
    assert "not-a-number" in errors[0]


# ── criterion: GET /api/admin/usage — CRON_SECRET-gated, numbers match the rows ──

def test_admin_usage_disabled_with_no_cron_secret(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = TestClient(app)
    res = client.get("/api/admin/usage")
    assert res.status_code == 503


def test_admin_usage_requires_the_correct_secret(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "the-real-secret")
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 401
    assert client.get("/api/admin/usage",
                       headers={"X-Cron-Secret": "wrong"}).status_code == 401


def test_admin_usage_reports_numbers_that_match_the_seeded_rows(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "the-real-secret")
    usage.record_call(route="r1", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                       prompt_tokens=10, completion_tokens=5, finish_reason="stop", ok=True)
    usage.record_call(route="r1", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                       prompt_tokens=20, completion_tokens=15, finish_reason="stop", ok=True)
    usage.record_call(route="r2", model=llm.DEFAULT_GEMINI_LIGHT_MODEL, light=True,
                       prompt_tokens=None, completion_tokens=None,
                       finish_reason="RateLimitError", ok=False)

    client = TestClient(app)
    res = client.get("/api/admin/usage", headers={"X-Cron-Secret": "the-real-secret"})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["total_calls"] == 3
    main_bucket = body["per_model"][llm.DEFAULT_GEMINI_MODEL]
    assert main_bucket["calls"] == 2
    assert main_bucket["prompt_tokens"] == 30
    assert main_bucket["completion_tokens"] == 20
    assert main_bucket["ok"] == 2
    light_bucket = body["per_model"][llm.DEFAULT_GEMINI_LIGHT_MODEL]
    assert light_bucket["calls"] == 1
    assert light_bucket["failed"] == 1
    assert body["oldest_price_date"] == usage.oldest_price_date()


# ── criterion: DAILY_CALL_BUDGET below the seeded count -> 503, client never constructed ──

def test_budget_gate_returns_503_and_never_constructs_the_provider_client(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    for _ in range(3):
        usage.record_call(route="seed", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                           prompt_tokens=1, completion_tokens=1, finish_reason="stop", ok=True)
    monkeypatch.setenv("DAILY_CALL_BUDGET", "3")  # already AT the seeded count

    client = TestClient(app)
    response = client.post("/api/generate", json={
        "goal": "ap36 unique goal — budget gate",
        "experience_level": "beginner",
        "time_commitment": "5-10 hours/week",
    })

    assert response.status_code == 503
    assert fake_openai.instances == []  # the provider client was never constructed
    assert len(_rows()) == 3            # unchanged — no new row from a call that never happened


def test_budget_gate_positive_control_unset_still_reaches_the_client(monkeypatch, fake_openai):
    """Without this pair the 503 test above proves only that the route can 503, not
    that the gate is what's firing (plan §9's positive control)."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    _Recorder.default_reply = _valid_path_json(1)

    client = TestClient(app)
    response = client.post("/api/generate", json={
        "goal": "ap36 unique goal — budget gate positive control",
        "experience_level": "beginner",
        "time_commitment": "5-10 hours/week",
    })

    assert response.status_code == 200, response.text
    # >=1, not ==1: the path has 1 milestone, so background enrichment ALSO
    # constructs its own client — the exact count is the fan-out test's job above,
    # this test only needs to prove the client WAS reachable this time (unlike the
    # 503 test, where fake_openai.instances stays empty).
    assert len(fake_openai.instances) >= 1
    assert len(_rows()) >= 1


def test_daily_budget_unset_means_unlimited(monkeypatch):
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    assert usage.daily_budget() is None
    db = SessionLocal()
    try:
        assert usage.over_budget(db) is False
    finally:
        db.close()


def test_daily_budget_startup_warning_when_unset(caplog, monkeypatch):
    import logging
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    with caplog.at_level(logging.WARNING, logger="usage"):
        usage.warn_if_unbounded()
    warnings = [r.getMessage() for r in caplog.records if r.name == "usage"]
    assert len(warnings) == 1
    assert "DAILY_CALL_BUDGET" in warnings[0]


def test_daily_budget_no_warning_when_set(caplog, monkeypatch):
    import logging
    monkeypatch.setenv("DAILY_CALL_BUDGET", "100")
    with caplog.at_level(logging.WARNING, logger="usage"):
        usage.warn_if_unbounded()
    assert [r.getMessage() for r in caplog.records if r.name == "usage"] == []


# ── criterion: _PRICES covers every model llm.py's defaults can reach; unknown -> None, never guessed ──

def test_every_model_llms_defaults_can_reach_has_a_price_entry():
    for model in (llm.DEFAULT_GEMINI_MODEL, llm.DEFAULT_GEMINI_LIGHT_MODEL,
                  llm.DEFAULT_OPENAI_MODEL, llm.DEFAULT_OPENAI_LIGHT_MODEL):
        assert usage.price_entry(model) is not None, (
            f"{model} is reachable from llm.py's defaults but has no usage._PRICES entry")


def test_unknown_model_prices_as_none_never_guessed():
    """Negative control: a model with no table entry must come back None (unknown),
    never a silently-invented $0.00 for a real cost."""
    assert usage.estimate_cost_usd("totally-unknown-model-xyz", 1000, 1000) is None


def test_free_tier_models_price_at_exactly_zero_not_none():
    """0.0 is the HONEST value for a priced free-tier model — distinct from None
    (unpriced/unknown)."""
    assert usage.estimate_cost_usd(llm.DEFAULT_GEMINI_MODEL, 1_000_000, 1_000_000) == 0.0
    assert usage.estimate_cost_usd(llm.DEFAULT_GEMINI_LIGHT_MODEL, 500, 500) == 0.0


def test_missing_token_counts_price_as_none_not_zero():
    """A NULL-token row (the missing-usage shape above) must not silently cost $0.00 —
    it must stay unknown, distinct from a genuinely free call."""
    assert usage.estimate_cost_usd(llm.DEFAULT_OPENAI_MODEL, None, None) is None


# ── /code-review fixes, 2026-09-24 ──────────────────────────────────────────

def test_the_recorder_never_takes_the_apps_one_pooled_connection(monkeypatch):
    """🔴 The bug the SQLite suite could not see: database.py caps the non-SQLite pool
    at one connection, which the in-flight request already holds. A recorder session
    from that same pool would wait for a connection that cannot be freed, so in
    production every call stalled and NO row was ever written — leaving the budget
    permanently unenforced. The recorder must use its own NullPool engine."""
    import database
    from sqlalchemy.pool import NullPool

    monkeypatch.setattr(database, "is_sqlite", False)
    usage._recorder_engine = None
    captured = {}

    def fake_create_engine(url, **kw):
        captured.update(kw)
        return database.engine          # harmless stand-in; we only assert the kwargs

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    try:
        usage._recorder_session().close()
    finally:
        usage._recorder_engine = None
    assert captured.get("poolclass") is NullPool, (
        "the recorder must not draw from the app's size-1 pool")


def test_a_failed_call_does_not_spend_the_daily_budget(monkeypatch):
    """A revoked key or an outage writes ok=False rows fast; spending the ceiling on
    calls that consumed no provider quota would 503 the app for nothing."""
    db = SessionLocal()
    try:
        for ok in (False, False, False):
            usage.record_call(route="r", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                              prompt_tokens=1, completion_tokens=1,
                              finish_reason="boom", ok=ok)
        monkeypatch.setenv("DAILY_CALL_BUDGET", "2")
        assert usage.calls_today(db) == 3, "failures are still recorded"
        assert usage.calls_today(db, only_ok=True) == 0
        assert usage.over_budget(db) is False, "failures must not consume the ceiling"
        usage.record_call(route="r", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                          prompt_tokens=1, completion_tokens=1, finish_reason="stop", ok=True)
        usage.record_call(route="r", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                          prompt_tokens=1, completion_tokens=1, finish_reason="stop", ok=True)
        assert usage.over_budget(db) is True, "two successful calls do reach it"
    finally:
        db.close()


def test_the_day_is_utc_not_the_servers_local_day():
    """`ts` is stamped in UTC; a local boundary would reset the budget early."""
    from datetime import datetime, timezone

    assert usage._today_start().date() == datetime.now(timezone.utc).date()


def test_a_non_positive_budget_is_treated_as_unlimited(monkeypatch):
    """0 would refuse every call all day — indistinguishable from a typo."""
    monkeypatch.setenv("DAILY_CALL_BUDGET", "0")
    assert usage.daily_budget() is None
    monkeypatch.setenv("DAILY_CALL_BUDGET", "-5")
    assert usage.daily_budget() is None


def test_over_budget_refuses_inside_chat_json_before_the_client_is_built(monkeypatch, fake_openai):
    """The middleware gate only sees the POST entry points: the quiz GET and the
    background enrichment fan-out reach chat_json without passing it again. The
    ceiling has to hold per CALL, before the provider client exists."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    usage.record_call(route="r", model=llm.DEFAULT_GEMINI_MODEL, light=False,
                      prompt_tokens=1, completion_tokens=1, finish_reason="stop", ok=True)
    monkeypatch.setenv("DAILY_CALL_BUDGET", "1")
    fake_openai.instances.clear()
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json(None, "anything")
    assert fake_openai.instances == [], "the provider client must never be constructed"

    # Positive control: without a ceiling the same call goes through.
    monkeypatch.delenv("DAILY_CALL_BUDGET", raising=False)
    llm.chat_json(None, "anything")
    assert fake_openai.instances, "unset budget must still reach the client"

"""AP41 — direct tests for ai_service's two generation entry points.

Until this module, every generation test went through HTTP routes (AP33, AP37)
and `generate_learning_path` / `adjust_difficulty` themselves had no unit tests
of their branches. These drive the functions directly with `llm.chat_json`
replaced by a scripted fake, so each branch is reached on purpose:

* cache miss -> one model call, then a hit -> no second call;
* an unknown language code is normalised to English (prompt AND cache key);
* a reply that is not JSON is retried once, and two in a row raise;
* a provider failure propagates with its own type and is NOT retried;
* adjust_difficulty's success, malformed-JSON, wrong-shape and error paths.

No network, no key: `chat_json` never reaches a provider.
"""
import json

import pytest

import ai_service
import llm
from database import SessionLocal
from models import GenerationCache
from schemas import GeneratedMilestone, GeneratedPath, PathGenerationError

ARGS = ("Learn Rust", "beginner", "5-10 hours/week")


def _path_json(n: int = 5, title: str = "Rust from zero") -> str:
    return json.dumps({
        "path_title": title,
        "path_description": "An overview.",
        "category": "Programming",
        "milestones": [
            {"title": f"M{i}", "description": "d", "estimated_hours": i + 1,
             "resources": ["The Rust Book"]}
            for i in range(n)
        ],
    })


class _Script:
    """Replaces llm.chat_json. Each call pops the next reply; a reply that is an
    Exception instance is raised instead of returned. Records (system, user)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        if not self.replies:
            raise AssertionError("chat_json called more times than the test scripted")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def script(monkeypatch):
    def install(*replies):
        s = _Script(*replies)
        monkeypatch.setattr(llm, "chat_json", s)
        return s
    # A fixed model id so the cache key does not depend on the machine's env.
    monkeypatch.setattr(llm, "resolved_model", lambda light=False: "test-model")
    return install


def _cache_rows() -> int:
    db = SessionLocal()
    try:
        return db.query(GenerationCache).count()
    finally:
        db.close()


# ── generate_learning_path ───────────────────────────────────────────────────

def test_miss_calls_the_model_once_then_a_hit_calls_it_zero_times(script):
    s = script(_path_json())
    first = ai_service.generate_learning_path(*ARGS)
    assert isinstance(first, GeneratedPath)
    assert len(s.calls) == 1
    assert _cache_rows() == 1

    second = ai_service.generate_learning_path(*ARGS)
    assert len(s.calls) == 1, "a cache hit must not reach the model"
    assert second.model_dump() == first.model_dump()


def test_different_inputs_are_a_miss(script):
    """Control for the test above: the hit is because the inputs matched, not
    because the function stopped calling the model altogether."""
    s = script(_path_json(), _path_json(title="Advanced"))
    ai_service.generate_learning_path(*ARGS)
    ai_service.generate_learning_path("Learn Rust", "advanced", "5-10 hours/week")
    assert len(s.calls) == 2
    assert _cache_rows() == 2


def test_unknown_language_is_normalised_to_english(script):
    s = script(_path_json())
    ai_service.generate_learning_path(*ARGS, language="xx")
    system, _user, _kw = s.calls[0]
    # English adds no language clause at all — byte-identical to the default.
    assert "Generate all output text values" not in system
    # ...and it shares the English cache entry: an "en" request is now a hit.
    ai_service.generate_learning_path(*ARGS, language="en")
    assert len(s.calls) == 1


def test_known_language_reaches_the_system_prompt(script):
    """Control for normalisation: a supported code is NOT flattened to English."""
    s = script(_path_json())
    ai_service.generate_learning_path(*ARGS, language="nl")
    system, _user, _kw = s.calls[0]
    assert "in Dutch" in system


def test_a_non_json_reply_is_retried_once_and_the_retry_is_used(script):
    s = script("this is not json {", _path_json(title="Second try"))
    result = ai_service.generate_learning_path(*ARGS)
    assert len(s.calls) == 2
    assert result.path_title == "Second try"


def test_two_non_json_replies_raise_and_cache_nothing(script):
    s = script("not json", "still not json")
    with pytest.raises(PathGenerationError) as exc:
        ai_service.generate_learning_path(*ARGS)
    assert isinstance(exc.value.__cause__, json.JSONDecodeError)
    assert len(s.calls) == 2
    assert _cache_rows() == 0


def test_a_provider_failure_propagates_with_its_type_and_is_not_retried(script):
    class ProviderDown(RuntimeError):
        pass

    s = script(ProviderDown("quota"))
    with pytest.raises(ProviderDown):
        ai_service.generate_learning_path(*ARGS)
    assert len(s.calls) == 1, "a provider error is not a validation error — no retry"
    assert _cache_rows() == 0


# ── adjust_difficulty ────────────────────────────────────────────────────────

def _adjust(**overrides):
    kwargs = dict(
        goal="Learn Rust", experience_level="beginner", time_commitment="5-10 hours/week",
        path_title="Rust", path_description="Overview",
        completed_milestones=[{"title": "Install"}],
        remaining_milestones=[{"title": "A"}, {"title": "B"}],
        feedback="too_easy", language="en",
    )
    kwargs.update(overrides)
    return ai_service.adjust_difficulty(**kwargs)


def test_adjust_difficulty_returns_validated_milestones(script):
    reply = json.dumps({"milestones": [
        {"title": "Harder A", "description": "d", "estimated_hours": 5, "resources": []},
        {"title": "Harder B", "description": "d", "estimated_hours": 8, "resources": []},
    ]})
    s = script(reply)
    out = _adjust()
    assert [m.title for m in out] == ["Harder A", "Harder B"]
    assert all(isinstance(m, GeneratedMilestone) for m in out)
    _system, user, _kw = s.calls[0]
    assert "MORE challenging" in user
    assert "The next 2 milestones need to be regenerated" in user


def test_adjust_difficulty_too_hard_asks_for_easier(script):
    s = script(json.dumps({"milestones": [
        {"title": "x", "description": "d", "estimated_hours": 1, "resources": []}]}))
    _adjust(feedback="too_hard", remaining_milestones=[{"title": "A"}])
    assert "EASIER" in s.calls[0][1]


def test_adjust_difficulty_unknown_language_adds_no_clause(script):
    s = script(json.dumps({"milestones": [
        {"title": "x", "description": "d", "estimated_hours": 1, "resources": []}]}))
    _adjust(language="xx", remaining_milestones=[{"title": "A"}])
    assert "Generate all output text values" not in s.calls[0][0]


@pytest.mark.parametrize("reply", [
    "not json at all",                       # json.loads fails
    json.dumps([1, 2, 3]),                   # a list has no .get -> AttributeError branch
    json.dumps({"milestones": []}),          # empty is not a valid regeneration
    json.dumps({"milestones": [{"title": "no hours"}]}),  # schema failure
])
def test_adjust_difficulty_malformed_replies_raise_path_generation_error(script, reply):
    script(reply)
    with pytest.raises(PathGenerationError):
        _adjust(remaining_milestones=[{"title": "A"}])


def test_adjust_difficulty_provider_failure_propagates_unchanged(script):
    class ProviderDown(RuntimeError):
        pass

    s = script(ProviderDown("timeout"))
    with pytest.raises(ProviderDown):
        _adjust()
    assert len(s.calls) == 1


# ── enrichment while the caller holds the pool's one connection ──────────────

def test_enrichment_completes_while_the_caller_holds_a_session(monkeypatch):
    """backfill_enrichment.main() keeps a session (and so, on Postgres, the
    app's ONE pooled connection) open while it calls enrich_milestone_resources
    per milestone. The enrichment write must not need a second connection from
    that pool — before AP41 it did, waited out the 30s pool timeout, and the
    failure was swallowed as a warning: every milestone silently not enriched.
    On SQLite (no pool cap) this passes either way; the Postgres leg is the
    one that can fail."""
    from models import LearningPath, Milestone

    monkeypatch.setattr(llm, "provider", lambda: "gemini")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: json.dumps(
        [{"title": "Docs", "url": "https://example.org", "type": "docs"}]))

    holder = SessionLocal()
    try:
        p = LearningPath(title="P", description="d", experience_level="beginner",
                         time_commitment="5h")
        holder.add(p)
        holder.flush()
        m = Milestone(learning_path_id=p.id, title="M", description="d", order=0,
                      estimated_hours=1, resources="[]")
        holder.add(m)
        holder.commit()
        m_id = m.id  # re-opens a transaction: the holder now owns the connection
        ai_service.enrich_milestone_resources(m_id, "M", "d", "goal")
    finally:
        holder.close()

    def resources(db):
        return db.query(Milestone).filter(Milestone.id == m_id).first().resources

    from conftest import peek
    assert "example.org" in peek(resources)

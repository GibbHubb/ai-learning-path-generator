"""AP46 — every AI feature goes through llm.chat_json, on Gemini's free tier.

No network: the OpenAI client is replaced with a recorder, so these pin what we SEND
(endpoint, model, response_format) and how the reply is cleaned.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import llm  # noqa: E402


class _Recorder:
    """Stands in for openai.OpenAI; records constructor args and the one create() call."""
    instances = []

    def __init__(self, reply='{"ok": true}', **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        _Recorder.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=_Recorder.reply))])


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    _Recorder.instances = []
    _Recorder.reply = '{"ok": true}'
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _Recorder(**kw))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    return _Recorder


def test_gemini_is_used_when_its_key_is_set_even_if_openai_is_too(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    assert llm.chat_json("sys", "user") == '{"ok": true}'
    client = fake_openai.instances[0]
    assert client.kwargs == {"api_key": "g-key", "base_url": llm.GEMINI_BASE_URL}
    assert client.calls[0]["model"] == llm.DEFAULT_GEMINI_MODEL


def test_openai_is_the_fallback_without_a_gemini_key(monkeypatch, fake_openai):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    llm.chat_json("sys", "user")
    client = fake_openai.instances[0]
    assert client.kwargs == {"api_key": "o-key"}  # no base_url: the real OpenAI endpoint
    assert client.calls[0]["model"] == llm.DEFAULT_OPENAI_MODEL


def test_no_key_at_all_raises_naming_the_free_key(monkeypatch, fake_openai):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.provider() is None
    with pytest.raises(llm.LLMUnavailable, match="GEMINI_API_KEY"):
        llm.chat_json("sys", "user")
    assert fake_openai.instances == []


def test_light_calls_use_the_higher_quota_model(monkeypatch, fake_openai):
    """flash allows 5 req/min on the free tier; one path creation makes 1 + (5..8)
    calls, so enrichment/quizzes must not share its quota."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("LLM_LIGHT_MODEL", raising=False)
    llm.chat_json(None, "user", light=True)
    assert fake_openai.instances[0].calls[0]["model"] == llm.DEFAULT_GEMINI_LIGHT_MODEL
    assert llm.DEFAULT_GEMINI_LIGHT_MODEL != llm.DEFAULT_GEMINI_MODEL


def test_enrichment_and_quizzes_ask_for_the_light_model(monkeypatch):
    import ai_service
    import quizzes
    seen = []
    q = {"question": "Q?", "options": ["a", "b", "c", "d"], "correct_index": 0, "explanation": ""}

    def fake_chat(system, user, **kw):
        seen.append(kw.get("light"))
        return json.dumps([q, q, q])

    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    quizzes._call_claude(SimpleNamespace(title="T", description="D"))
    ai_service.enrich_milestone_resources(987654321, "T", "D", "goal")  # no such row: no write
    assert seen == [True, True]


def test_users_never_see_raw_provider_errors():
    import httpx
    from openai import RateLimitError
    req = httpx.Request("POST", "https://generativelanguage.googleapis.com/x")
    quota = RateLimitError("quota metric generate_content_free_tier_requests",
                           response=httpx.Response(429, request=req), body=None)
    assert "busy" in llm.user_message(quota)
    assert "not configured" in llm.user_message(llm.LLMUnavailable("x"))
    raw = llm.user_message(RuntimeError("secret internal detail"))
    assert "secret internal detail" not in raw and "try again" in raw


def test_generation_keeps_the_exception_type_for_the_route(monkeypatch):
    """ai_service used to wrap every failure in a bare Exception, which would hide a
    RateLimitError from llm.user_message and show "could not" instead of "busy"."""
    import httpx
    import ai_service
    from openai import RateLimitError
    req = httpx.Request("POST", "https://x")

    def boom(*a, **k):
        raise RateLimitError("429", response=httpx.Response(429, request=req), body=None)

    monkeypatch.setattr(llm, "chat_json", boom)
    with pytest.raises(RateLimitError):
        ai_service.generate_learning_path("never-cached goal ap46", "beginner", "1h")


def test_model_override(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash-lite")
    llm.chat_json("sys", "user")
    assert fake_openai.instances[0].calls[0]["model"] == "gemini-3.5-flash-lite"


def test_object_mode_is_requested_by_default_and_omitted_for_arrays(monkeypatch, fake_openai):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    llm.chat_json("sys", "user")
    llm.chat_json(None, "user", json_object=False)
    first, second = (c.calls[0] for c in fake_openai.instances)
    assert first["response_format"] == {"type": "json_object"}
    assert "response_format" not in second
    # no system message when none is given
    assert [m["role"] for m in second["messages"]] == ["user"]
    assert [m["role"] for m in first["messages"]] == ["system", "user"]


@pytest.mark.parametrize("raw", ['```json\n[1, 2]\n```', '```\n[1, 2]\n```', '  [1, 2]  '])
def test_fences_are_stripped(monkeypatch, fake_openai, raw):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    fake_openai.reply = raw
    assert json.loads(llm.chat_json(None, "user", json_object=False)) == [1, 2]


def test_quiz_generation_goes_through_llm(monkeypatch):
    """The quiz path used a paid Anthropic key; it must now reach llm.chat_json, and a
    valid reply must survive validate_quiz."""
    import quizzes
    q = {"question": "Q?", "options": ["a", "b", "c", "d"], "correct_index": 1, "explanation": "e"}
    seen = {}

    def fake_chat(system, user, **kw):
        seen["json_object"] = kw.get("json_object")
        return json.dumps([q, q, q])

    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    out = quizzes._call_claude(SimpleNamespace(title="T", description="D" * 120))
    assert out is not None and len(out) == 3
    assert seen["json_object"] is False  # top-level array


def test_quiz_generation_without_a_key_returns_none(monkeypatch):
    import quizzes
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: called.append(1))
    assert quizzes._call_claude(SimpleNamespace(title="T", description="D")) is None
    assert called == []

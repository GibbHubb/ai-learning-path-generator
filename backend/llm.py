"""AP46 — the one place that talks to a language model.

Every AI feature (path generation, difficulty adjustment, resource enrichment, quizzes)
goes through `chat_json()`. The provider is Google Gemini on its free tier, reached through
Gemini's OpenAI-compatible endpoint, so the `openai` SDK stays the only client library.

Why Gemini: the project is free-tier only. Generation used a paid OpenAI key and
enrichment/quizzes a paid Anthropic key; neither was set in production, so all four
features were dead live. OpenAI still works as a fallback when `OPENAI_API_KEY` is set and
`GEMINI_API_KEY` is not, so a local setup that already has one keeps running.
"""
from __future__ import annotations

import inspect
import logging
import os
import re

import usage

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# gemini-2.5-* returned 404 "no longer available to new users" on 2026-09-16.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# Free-tier quotas are per model, and measured 2026-09-16: gemini-3.6-flash allows only
# 5 requests/minute (the 5th call of a test run got 429), while flash-lite took 12
# back-to-back calls without one. Creating a path makes 1 generation call PLUS one
# enrichment call per milestone (5-8), so everything on flash would 429 most enrichments.
# Generation/adjustment (what the learner reads) stays on flash; the high-volume
# enrichment and quiz calls use the light model.
DEFAULT_GEMINI_LIGHT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_OPENAI_LIGHT_MODEL = "gpt-4o-mini"

# AP36 — output ceiling + timeout, both with module-level defaults so every caller gets
# them without asking. 4,000 tokens is ~2x the observed shape of a full 8-milestone path
# (see the plan's §5); 1,024 is ample for a 2-3 item resource list or a batch of quiz
# questions. Truncation is no longer silent: since AP33 a truncated JSON reply fails
# schema validation and becomes a clean 502 after one retry, and `finish_reason` is
# recorded on every row so truncation shows up as a count, not a mystery.
DEFAULT_MAX_TOKENS = 4000
DEFAULT_LIGHT_MAX_TOKENS = 1024
# A hung provider call otherwise burns the whole Vercel function duration — independent
# of the token ceiling above.
DEFAULT_TIMEOUT_SECONDS = 30.0


class LLMUnavailable(RuntimeError):
    """No provider key is configured."""


def provider() -> str | None:
    """`"gemini"`, `"openai"`, or None. Gemini wins when both keys are set."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def _default_models(which: str | None) -> tuple[str, str]:
    """(main, light) default model ids for a provider name. `which=None` (no
    provider key configured) falls back to the Gemini defaults, same as
    `which="gemini"` — Gemini is the primary/free-tier target, and this is a
    pure lookup, not the "is a provider configured" gate (that stays in
    `_client_and_model`, which checks `provider()` itself before ever calling
    this). Kept non-raising so it also safely backs `resolved_model()`, which
    callers may need BEFORE deciding whether to call the provider at all."""
    if which == "openai":
        return (DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_LIGHT_MODEL)
    return (DEFAULT_GEMINI_MODEL, DEFAULT_GEMINI_LIGHT_MODEL)


def resolved_model(light: bool = False) -> str:
    """The model id `chat_json` would use for this call, without constructing a
    client or making a network call — and without requiring a provider key to
    already be set.

    AP37 — the generation cache needs to know the model BEFORE deciding
    whether to call the provider at all (the model id is part of the cache
    key, so a later model change never silently serves the old model's cached
    output). This never raises on a missing provider key: that failure is
    `_client_and_model`'s job (via `chat_json`), and it must fire at the SAME
    point it always has — inside `chat_json` — not earlier, from a cache-key
    lookup a test may reach with `chat_json` itself mocked and no real key set."""
    main_default, light_default = _default_models(provider())
    if light:
        return os.getenv("LLM_LIGHT_MODEL") or light_default
    return os.getenv("LLM_MODEL") or main_default


def _client_and_model(light: bool):
    from openai import OpenAI

    which = provider()
    if which is None:
        raise LLMUnavailable("No AI provider configured: set GEMINI_API_KEY (free tier).")
    main_default, light_default = _default_models(which)
    if which == "gemini":
        client = OpenAI(api_key=os.getenv("GEMINI_API_KEY"), base_url=GEMINI_BASE_URL)
    else:  # "openai" — which is only ever "gemini"/"openai"/None, and None just raised
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if light:
        return client, os.getenv("LLM_LIGHT_MODEL") or light_default
    return client, os.getenv("LLM_MODEL") or main_default


def user_message(exc: BaseException, action: str = "generate your learning path") -> str:
    """What a learner sees when an AI call fails. Never the raw exception: that is SDK or
    provider text (quota ids, URLs) — log it, show this. A 429 is expected on the free tier
    and gets its own wording so it reads as "wait", not "broken"."""
    try:
        from openai import RateLimitError
        if isinstance(exc, RateLimitError):
            return f"The AI service is busy right now. Wait a minute, then try to {action} again."
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, LLMUnavailable):
        return "AI features are not configured on this server yet."
    return f"Could not {action}. Please try again."


def strip_fences(text: str) -> str:
    """Models sometimes wrap JSON in ```json fences despite being told not to."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _caller_route() -> str:
    """Best-effort label for GET /api/admin/usage — the name of whoever called
    chat_json (generate_learning_path, adjust_difficulty, enrich_milestone_resources,
    _call_claude, ...). Never raises: an inspection failure loses the label, not the
    row (falls back to "unknown").

    Two frames up: this function's own frame, then chat_json's (its only caller),
    then chat_json's caller — the one we want."""
    try:
        frame = inspect.currentframe()
        caller = frame.f_back.f_back if frame and frame.f_back else None
        return caller.f_code.co_name if caller else "unknown"
    except Exception:  # pragma: no cover — defensive only
        return "unknown"


def chat_json(system: str | None, user: str, *, temperature: float = 0.7,
              json_object: bool = True, light: bool = False,
              max_tokens: int | None = None, timeout: float | None = None) -> str:
    """Send one system+user exchange and return the reply text, fences stripped.

    `json_object=True` asks the provider for a JSON object. Callers whose schema has a
    top-level ARRAY (enrichment, quizzes) must pass False: json_object mode forces an
    object and the array would come back wrapped or rejected.

    `light=True` uses the cheaper, higher-quota model — see DEFAULT_GEMINI_LIGHT_MODEL.

    AP36 — every call is capped (`max_tokens`, default DEFAULT_LIGHT_MAX_TOKENS or
    DEFAULT_MAX_TOKENS), timed out (`timeout`, default DEFAULT_TIMEOUT_SECONDS) and
    recorded via usage.record_call — on success AND on failure, so a burst of provider
    errors is visible rather than silent. This is the one choke point every caller goes
    through, so this is the only place any of that needs to live.
    """
    # AP36 — the ceiling is per CALL, not per request: the middleware gate cannot see
    # the quiz GET or the background enrichment fan-out. Checked before the client is
    # constructed, so an over-budget call never reaches the provider.
    import usage as _usage
    if _usage.over_budget_now():
        raise LLMUnavailable(
            "Daily model-call budget reached (DAILY_CALL_BUDGET). Try again tomorrow.")
    client, model = _client_and_model(light)
    if max_tokens is None:
        max_tokens = DEFAULT_LIGHT_MAX_TOKENS if light else DEFAULT_MAX_TOKENS
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SECONDS
    route = _caller_route()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    if json_object:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        # Recording sits OUTSIDE this try (plan §5): a failure to record must never be
        # swallowed by the provider's own except, and it must never hide the original
        # exception either — record, then re-raise unconditionally.
        usage.record_call(
            route=route, model=model, light=light,
            prompt_tokens=None, completion_tokens=None,
            finish_reason=type(exc).__name__, ok=False,
        )
        raise

    usage_obj = getattr(response, "usage", None)
    prompt_tokens = getattr(usage_obj, "prompt_tokens", None) if usage_obj is not None else None
    completion_tokens = getattr(usage_obj, "completion_tokens", None) if usage_obj is not None else None

    # /code-review, 2026-09-24: an empty `choices` list (e.g. a safety-filtered
    # response with zero candidates) used to be caught by a narrow try/except around
    # ONLY the finish_reason read, downgrading it to finish_reason=None while still
    # recording ok=True — then the very next line re-indexed choices[0] with no guard
    # and raised an uncaught IndexError anyway, so the row was already wrong (claimed
    # success) by the time the crash happened. Check once, record accordingly, and
    # raise a clean, typed error instead of leaking an IndexError from deep inside
    # this module.
    choices = getattr(response, "choices", None) or []
    if not choices:
        usage.record_call(
            route=route, model=model, light=light,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            finish_reason="empty_choices", ok=False,
        )
        raise RuntimeError(f"{model} returned a response with no choices "
                            f"(possibly safety-filtered)")

    finish_reason = getattr(choices[0], "finish_reason", None)
    usage.record_call(
        route=route, model=model, light=light,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        finish_reason=finish_reason, ok=True,
    )
    return strip_fences(choices[0].message.content)

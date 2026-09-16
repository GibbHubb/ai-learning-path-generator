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

import logging
import os
import re

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


class LLMUnavailable(RuntimeError):
    """No provider key is configured."""


def provider() -> str | None:
    """`"gemini"`, `"openai"`, or None. Gemini wins when both keys are set."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def _client_and_model(light: bool):
    from openai import OpenAI

    which = provider()
    if which == "gemini":
        client = OpenAI(api_key=os.getenv("GEMINI_API_KEY"), base_url=GEMINI_BASE_URL)
        defaults = (DEFAULT_GEMINI_MODEL, DEFAULT_GEMINI_LIGHT_MODEL)
    elif which == "openai":
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        defaults = (DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_LIGHT_MODEL)
    else:
        raise LLMUnavailable("No AI provider configured: set GEMINI_API_KEY (free tier).")
    if light:
        return client, os.getenv("LLM_LIGHT_MODEL") or defaults[1]
    return client, os.getenv("LLM_MODEL") or defaults[0]


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


def chat_json(system: str | None, user: str, *, temperature: float = 0.7,
              json_object: bool = True, light: bool = False) -> str:
    """Send one system+user exchange and return the reply text, fences stripped.

    `json_object=True` asks the provider for a JSON object. Callers whose schema has a
    top-level ARRAY (enrichment, quizzes) must pass False: json_object mode forces an
    object and the array would come back wrapped or rejected.

    `light=True` uses the cheaper, higher-quota model — see DEFAULT_GEMINI_LIGHT_MODEL.
    """
    client, model = _client_and_model(light)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    kwargs = {"model": model, "messages": messages, "temperature": temperature}
    if json_object:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return strip_fences(response.choices[0].message.content)

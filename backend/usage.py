"""AP36 — count and cap what every model call costs.

Two things changed the shape of this problem (see plans/ai_path/AP36.md §2, re-planned
2026-09-24 against AP46's `llm.py`):

  1. Dollars are no longer the scarce resource — Gemini's free tier (llm.py) bills
     nothing; what runs out is per-model request quota. So the budget this module adds
     (`DAILY_CALL_BUDGET`) counts CALLS, not dollars. `_PRICES` / `est_cost_usd` exist only
     to stay honest if the paid `gpt-4o` fallback is ever configured.
  2. Every model call goes through one choke point, `llm.chat_json`, so `record_call` is
     called from exactly one place (llm.py) rather than sprinkled across modules.

This module owns its own DB session per call (like `rate_limit._check_and_commit` in
main.py) so `llm.chat_json` — called from deep inside `ai_service.py` / `quizzes.py`,
often off the request's own session — never needs one threaded in.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database import DATABASE_URL, SessionLocal
from models import ModelCall

logger = logging.getLogger(__name__)

# Per-million-token USD rates, source-dated so a stale table is visible rather than
# confidently wrong (plan §8 risk 3). Free-tier entries are priced 0.0 — the honest
# value, not a guess. A model string NOT in this dict prices as `None` (see
# estimate_cost_usd) rather than silently reading $0.00 for a real cost — the coverage
# test in tests/test_usage_ap36.py asserts every model llm.py's defaults can reach has
# an entry here, so adding a model without a price fails the suite instead of shipping
# a wrong number.
_PRICES: dict[str, dict] = {
    # Gemini free tier: $0 while under the per-model quota (llm.py's DEFAULT_GEMINI_*).
    "gemini-3.6-flash": {"prompt": 0.0, "completion": 0.0, "date": "2026-09-16"},
    "gemini-3.5-flash-lite": {"prompt": 0.0, "completion": 0.0, "date": "2026-09-16"},
    # OpenAI paid fallback (llm.py's DEFAULT_OPENAI_*), used only when GEMINI_API_KEY is
    # unset. Published per-1M-token rates, source: openai.com/api/pricing.
    "gpt-4o": {"prompt": 2.50, "completion": 10.00, "date": "2026-09-16"},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60, "date": "2026-09-16"},
}


def price_entry(model: str) -> dict | None:
    return _PRICES.get(model)


def oldest_price_date() -> str | None:
    dates = [entry["date"] for entry in _PRICES.values()]
    return min(dates) if dates else None


def estimate_cost_usd(model: str, prompt_tokens: int | None,
                       completion_tokens: int | None) -> float | None:
    """None when the model has no price entry OR either token count is unknown — never
    a guessed number. 0.0 is a real, honest answer for a free-tier model."""
    entry = _PRICES.get(model)
    if entry is None or prompt_tokens is None or completion_tokens is None:
        return None
    return (prompt_tokens * entry["prompt"] + completion_tokens * entry["completion"]) / 1_000_000


_recorder_engine = None


def _recorder_session():
    """A session on a NullPool engine: one connection, opened and closed per row.

    🔴 NOT `SessionLocal()`. `database.py` caps the non-SQLite pool at
    `pool_size=1, max_overflow=0`, and the request handler that triggered this model
    call is still holding that one connection inside an open transaction. A second
    session from the same pool would wait for a connection that cannot be released
    until the handler returns: in production every call would stall for the pool
    timeout, the exception would be swallowed by `record_call`, and NO row would ever
    be written — leaving `calls_today()` at 0 and the budget permanently unenforced.
    The SQLite suite cannot see it (SQLite gets no pool cap), which is why the tests
    were green. Found by /code-review, 2026-09-24.

    SQLite keeps the app engine: it has no pool cap, and a second engine on the same
    file would fight the suite's per-test drop/create.
    """
    global _recorder_engine
    from database import is_sqlite
    if is_sqlite:
        return SessionLocal()
    if _recorder_engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        _recorder_engine = sessionmaker(
            bind=create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True))
    return _recorder_engine()


def record_call(*, route: str, model: str, light: bool,
                 prompt_tokens: int | None, completion_tokens: int | None,
                 finish_reason: str | None, ok: bool) -> None:
    """Write one row. Called from INSIDE llm.chat_json on both the success and the
    failure path — never inside the provider's own try/except, so a failure to record
    is never swallowed by that block (it is caught here instead, logged at ERROR, and
    does not propagate: a DB hiccup while recording must not turn a successful model
    reply into a 500, or fail a request that was already refused)."""
    db = _recorder_session()
    try:
        db.add(ModelCall(
            ts=datetime.utcnow(),
            route=route,
            model=model,
            light=light,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            est_cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
            ok=ok,
        ))
        db.commit()
    except Exception:
        logger.error(
            "usage.record_call: failed to write a model_calls row "
            "(route=%s model=%s ok=%s) — this call will look free/uncounted",
            route, model, ok, exc_info=True,
        )
        db.rollback()
    finally:
        db.close()


def _today_start() -> datetime:
    """Midnight UTC. `ts` is stamped with `utcnow()`, so a LOCAL day boundary would
    reset the budget two hours early on UTC+2 and give a 27-hour day on UTC-7, while
    .env.example promises a UTC day (/code-review, 2026-09-24)."""
    return datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())


def calls_today(db: Session, *, only_ok: bool = False) -> int:
    q = db.query(ModelCall).filter(ModelCall.ts >= _today_start())
    if only_ok:
        q = q.filter(ModelCall.ok.is_(True))
    return q.count()


def daily_budget() -> int | None:
    """`DAILY_CALL_BUDGET` unset -> None (unlimited) — Max's call, delegated 2026-09-22:
    a low default would 503 a working app on deploy.

    A malformed value (not a valid integer) is treated the SAME as unset — logged at
    ERROR, never raised. /code-review, 2026-09-24: this used to call `int(raw)`
    unguarded, and `warn_if_unbounded()` runs at MODULE IMPORT time (main.py), so a
    typo'd env value would have taken the whole app down at startup — exactly the
    failure mode the "unset means unlimited" design was meant to avoid."""
    raw = os.getenv("DAILY_CALL_BUDGET")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        logger.error(
            "usage: DAILY_CALL_BUDGET=%r is not a valid integer — ignoring it and "
            "treating today as UNLIMITED rather than failing the app.", raw)
        return None
    if value <= 0:
        # 0 would mean "refuse every model call all day", which is a foot-gun
        # indistinguishable from a typo (review, 2026-09-24).
        logger.error("usage: DAILY_CALL_BUDGET=%s is not positive — treating as "
                     "unlimited.", value)
        return None
    return value


def warn_if_unbounded() -> None:
    """One startup WARNING when there is no daily call ceiling — informational, not a
    hard stop (see daily_budget's docstring). Mirrors config.warn_unset_required()."""
    if daily_budget() is None:
        logger.warning(
            "usage: DAILY_CALL_BUDGET is unset — no daily model-call ceiling is "
            "enforced. Set it to cap spend against the provider's free-tier quota.")


def over_budget(db: Session) -> bool:
    budget = daily_budget()
    if budget is None:
        return False
    # Only calls that actually reached the provider spend the ceiling: a revoked
    # key or an outage writes ok=False rows fast, and burning the day's budget on
    # calls that consumed no quota would 503 the app for nothing. The failures are
    # still recorded — they are what makes an outage visible (review, 2026-09-24).
    return calls_today(db, only_ok=True) >= budget


def over_budget_now() -> bool:
    """`over_budget` without asking the caller for a session.

    The middleware gate only sees the POST entry points: `GET /api/milestones/{id}/quiz`
    generates a quiz through `chat_json` on a cache miss, and one `POST /api/generate`
    fans out into 1 + N background enrichment calls that never pass through it again.
    Checking here — inside the one choke point, BEFORE the provider client is built —
    is what makes the ceiling true for every call rather than every request
    (/code-review, 2026-09-24).
    """
    if daily_budget() is None:
        return False
    db = _recorder_session()
    try:
        return over_budget(db)
    except Exception:
        # A failed read must not block generation: the middleware gate still covers
        # the entry points, and refusing on an unknown count would be worse.
        logger.error("usage.over_budget_now: budget read failed — allowing the call",
                     exc_info=True)
        return False
    finally:
        db.close()


def summary_today(db: Session) -> dict:
    """Per-model call count + summed tokens for today, plus the oldest price-table
    source date — the payload for GET /api/admin/usage."""
    rows = db.query(ModelCall).filter(ModelCall.ts >= _today_start()).all()
    per_model: dict[str, dict] = {}
    for row in rows:
        bucket = per_model.setdefault(row.model, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "ok": 0, "failed": 0,
        })
        bucket["calls"] += 1
        bucket["prompt_tokens"] += row.prompt_tokens or 0
        bucket["completion_tokens"] += row.completion_tokens or 0
        bucket["ok" if row.ok else "failed"] += 1
    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "total_calls": len(rows),
        "per_model": per_model,
        "oldest_price_date": oldest_price_date(),
        "daily_call_budget": daily_budget(),
    }

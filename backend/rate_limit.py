"""AP35 — durable, per-visitor rate limiting for the routes that call a model.

Replaces the in-process `request_counts` dict main.py used to carry, which had
four independent defects (see plan §2):

  (a) it keyed on `request.client.host`, which on Vercel is the platform's own
      forwarding address, not the visitor — every visitor shared one bucket.
  (b) the dict was a module global, so each warm Vercel function instance had
      its own count.
  (c) entries were only pruned on that key's own next request, so a one-shot
      visitor's list lived forever.
  (d) it only guarded /api/generate and /api/generate/stream, missing two
      other routes that spend money on a model call.

Storage: one row per accepted hit in `rate_limit_hits` (models.RateLimitHit) —
durable across process restarts and shared across every instance, because
they all talk to the same database.

Key derivation: `request.client.host` UNLESS TRUSTED_PROXY_HEADER=1, in which
case the leftmost `X-Forwarded-For` entry is trusted instead. Off by default —
set it on Vercel, leave it unset locally — so a developer cannot accidentally
ship a limiter anyone can bypass with a forged header (see the spoofing test
in tests/test_rate_limit_ap35.py).

Concurrency: on Postgres, `check_and_record` takes a per-(key, route)
transaction-scoped advisory lock (`pg_advisory_xact_lock`) before counting and
inserting, so two requests racing on the same visitor's Nth slot cannot both
read "under limit" and both insert — the lock serialises them, and the second
transaction's count sees the first transaction's just-committed row.

Plan §5's original approach was a plain `SELECT ... FOR UPDATE` on Postgres.
That does not close this race: FOR UPDATE only locks rows that already exist,
and the race is between two transactions that are BOTH about to INSERT a
fresh row neither has written yet — there is nothing to lock. The advisory
lock keeps the "count-and-insert in one transaction" shape §5 asked for; it
just changes which statement does the serialising. Recorded here (and in the
plan file) per the execute contract: decide and document, don't silently
deviate. SQLite needs neither — `database.py` binds one shared connection and
SQLite itself serialises writers, so a plain transaction is enough there too,
exactly as §5 said.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.requests import Request

import config
from database import is_sqlite
from models import RateLimitHit

RATE_LIMIT = 5            # requests per key, per route, per window
RATE_LIMIT_WINDOW = 60    # seconds

# AP35 criterion 5 — the two routes /api/generate + /api/generate/stream
# always carried the limit; these are the two that were missing it. A module
# constant so AP36 and future tickets extend one list instead of hunting
# through main.py.
EXACT_LIMITED_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/api/generate"),
    ("POST", "/api/generate/stream"),
)
# The milestone routes carry a path parameter (`/api/milestones/{id}/...`),
# so they are matched by suffix instead of an exact string. Both suffixes are
# unique to these two routes under /api/milestones/.
_LIMITED_SUFFIXES: tuple[str, ...] = ("/feedback", "/quiz/regenerate")


def is_limited_route(method: str, path: str) -> bool:
    """True if this request should be metered against the durable limiter."""
    if method != "POST":
        return False
    if (method, path) in EXACT_LIMITED_ROUTES:
        return True
    return path.startswith("/api/milestones/") and path.endswith(_LIMITED_SUFFIXES)


def route_label_for(path: str) -> str:
    """Collapse an id-bearing path to one stable bucket per route, so
    /api/milestones/7/feedback and /api/milestones/9/feedback share a single
    per-visitor bucket — the limiter counts spend on the ROUTE, not on the
    specific id."""
    for suffix in _LIMITED_SUFFIXES:
        if path.startswith("/api/milestones/") and path.endswith(suffix):
            return f"/api/milestones/*{suffix}"
    return path


def derive_key(request: Request) -> str:
    """The visitor identity the limiter buckets on.

    Trusts `X-Forwarded-For` ONLY when TRUSTED_PROXY_HEADER=1 (set on Vercel;
    unset locally, so a developer cannot build a limiter anyone can bypass
    with a header — see the spoofing test)."""
    if config.bool_flag("TRUSTED_PROXY_HEADER", False):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            leftmost = xff.split(",", 1)[0].strip()
            if leftmost:
                return leftmost
    client = request.client
    return client.host if client else "unknown"


def prune(db: Session, *, before: datetime) -> None:
    """Delete hit rows older than `before` (criterion 7 — called on every
    write so the table never grows unbounded)."""
    db.query(RateLimitHit).filter(RateLimitHit.created_at < before).delete(
        synchronize_session=False)


def check_and_record(db: Session, key: str, route: str) -> tuple[bool, int, int]:
    """Count this key+route's hits in the current window and, if under the
    limit, record this one — atomically (see module docstring for the
    Postgres locking strategy). Caller is responsible for commit/rollback.

    Returns (allowed, remaining_after_this_request, retry_after_seconds).
    `retry_after_seconds` is only meaningful when `allowed` is False.
    """
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW)

    prune(db, before=window_start)

    if not is_sqlite:
        # Serialise concurrent count-and-insert for this exact visitor+route
        # (Postgres only — SQLite already serialises writers). Scoped to the
        # transaction: released automatically on commit/rollback.
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"{key}:{route}"},
        )

    window_hits = (
        db.query(RateLimitHit)
        .filter(
            RateLimitHit.key == key,
            RateLimitHit.route == route,
            RateLimitHit.created_at >= window_start,
        )
        .order_by(RateLimitHit.created_at.asc())
        .all()
    )
    count = len(window_hits)

    if count >= RATE_LIMIT:
        # The hit whose expiry frees a slot: with more than RATE_LIMIT hits in the
        # window (e.g. after the limit is lowered), the oldest one frees nothing.
        oldest = window_hits[count - RATE_LIMIT]
        expires_at = oldest.created_at + timedelta(seconds=RATE_LIMIT_WINDOW)
        retry_after = max(1, min(RATE_LIMIT_WINDOW, int((expires_at - now).total_seconds()) + 1))
        db.flush()
        return False, 0, retry_after

    db.add(RateLimitHit(key=key, route=route, created_at=now))
    db.flush()
    return True, max(0, RATE_LIMIT - count - 1), RATE_LIMIT_WINDOW

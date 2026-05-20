"""AP25 — minimal completion-date math for the .ics calendar export.

Plan §2: there is NO AP19 estimator to reuse in the codebase today, so
this module builds just the math AP25 needs: a tolerant parser for the
free-text `path.time_commitment` and a proportional schedule that spreads
milestones from today to a computed finish date.

Keep this small and pure (no DB / no ORM imports) so it's trivially
testable.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Iterable, List, Tuple


DEFAULT_HOURS_PER_WEEK = 5


def parse_time_commitment(s: str | None) -> int:
    """Pull the first plausible integer-hour-per-week from a free-text
    `path.time_commitment` (e.g. '10 hours/week', '5 hrs / wk', 'a few h').

    Returns DEFAULT_HOURS_PER_WEEK when the string is empty or no integer
    near an 'hour'/'hr'/'h' token can be extracted.
    """
    if not s:
        return DEFAULT_HOURS_PER_WEEK
    # Find an integer immediately followed (optionally via spaces / hyphens
    # / 'per' / '/') by an h-token. Tolerant.
    m = re.search(r"(\d+)\s*(?:to\s*\d+\s*)?[-\s/]*h", s, flags=re.IGNORECASE)
    if not m:
        return DEFAULT_HOURS_PER_WEEK
    try:
        hours = int(m.group(1))
    except (TypeError, ValueError):
        return DEFAULT_HOURS_PER_WEEK
    return hours if hours > 0 else DEFAULT_HOURS_PER_WEEK


def estimate_schedule(
    milestones: Iterable,
    time_commitment: str | None,
    start: date | None = None,
) -> Tuple[date, List[Tuple[object, date]]]:
    """Spread milestones across calendar days.

    Returns (finish_date, [(milestone, scheduled_date), ...]) in milestone
    order. Scheduling is proportional by `estimated_hours`: each milestone's
    end-cumulative-hours fraction × the total span maps it to a date.
    Linear fallback when every milestone has zero/None hours.

    - `start` defaults to today.
    - `finish_date` = start + ceil(total_hours / hours_per_week × 7),
      clamped to at least `start + 1 day` when there are milestones.
    """
    today = start or date.today()
    ms = sorted(list(milestones), key=lambda m: getattr(m, "order", 0) or 0)
    if not ms:
        return today, []

    hours_per_week = parse_time_commitment(time_commitment)
    raw_hours = [float(getattr(m, "estimated_hours", 0) or 0) for m in ms]
    total_hours = sum(raw_hours)

    if total_hours > 0:
        span_days = max(1, math.ceil((total_hours / hours_per_week) * 7))
    else:
        # No hour estimates anywhere → one day per milestone as a baseline.
        span_days = max(1, len(ms))

    finish = today + timedelta(days=span_days)

    schedule: List[Tuple[object, date]] = []
    if total_hours > 0:
        cumulative = 0.0
        for m, h in zip(ms, raw_hours):
            cumulative += h
            frac = cumulative / total_hours
            offset = max(1, math.ceil(frac * span_days))
            schedule.append((m, today + timedelta(days=offset)))
    else:
        # Linear fallback: one milestone per day.
        for idx, m in enumerate(ms, start=1):
            schedule.append((m, today + timedelta(days=idx)))

    # Guarantee monotonic non-decreasing dates (proportional math already
    # is, but math.ceil over equal fractions can repeat — fine).
    return finish, schedule

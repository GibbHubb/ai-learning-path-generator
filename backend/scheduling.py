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
from datetime import date, datetime, timedelta
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


# AP29-fu1 — a calendar block longer than a long working day is not a
# usable commitment; a "40 hours/week" path would otherwise emit a single
# 40-hour VEVENT spanning days. Cap the timed block and let the all-day
# form carry the heavier commitments.
MAX_BLOCK_HOURS = 8


def weekly_study_blocks(
    time_commitment: str | None,
    start: date | None = None,
    finish: date | None = None,
    hour: int | None = None,
    minute: int = 0,
) -> dict:
    """AP29 — parameters for a recurring weekly 'study block' calendar event.

    Pure: derives the block's cadence from the free-text `time_commitment`
    (reusing `parse_time_commitment`) and bounds it to the schedule's finish
    date. All-day / date-valued to match AP25's calendar style.

    AP29-fu1 — passing `hour` (0-23) additionally produces a *timed* block:
    a real time-boxed commitment starting at that hour, running for the
    weekly hour budget (capped at MAX_BLOCK_HOURS). The all-day keys are
    still returned, so callers that ignore the timed keys are unaffected.

    Returns:
        {
          "hours_per_week": int,   # from parse_time_commitment
          "dtstart": date,         # first occurrence (defaults to today)
          "until": date,           # RRULE UNTIL — never before dtstart
          "weekday": int,          # 0=Mon .. 6=Sun (dtstart's weekday)

          # present only when `hour` is given:
          "dtstart_dt": datetime,  # naive/floating — 18:00 in the viewer's tz
          "duration": timedelta,   # block length, <= MAX_BLOCK_HOURS
          "block_hours": int,      # duration in whole hours, for the summary
        }

    Raises:
        ValueError: if `hour` is outside 0-23 or `minute` outside 0-59.
    """
    today = start or date.today()
    until = finish or (today + timedelta(days=7))
    if until < today:
        until = today

    out = {
        "hours_per_week": parse_time_commitment(time_commitment),
        "dtstart": today,
        "until": until,
        "weekday": today.weekday(),
    }

    if hour is not None:
        if not 0 <= hour <= 23:
            raise ValueError(f"hour must be 0-23, got {hour}")
        if not 0 <= minute <= 59:
            raise ValueError(f"minute must be 0-59, got {minute}")
        block_hours = min(out["hours_per_week"], MAX_BLOCK_HOURS)
        # Naive datetime = RFC-5545 "floating" time: renders at the stated
        # wall-clock hour in whatever timezone the calendar app is in, which
        # is what someone asking for "18:00" actually wants. Avoids shipping
        # a VTIMEZONE component and a tz database dependency.
        out["dtstart_dt"] = datetime(today.year, today.month, today.day, hour, minute)
        out["duration"] = timedelta(hours=block_hours)
        out["block_hours"] = block_hours

    return out

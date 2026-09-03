"""AP11 — daily-reminder email pipeline.

Pure helpers, callable from either:
  * the optional APScheduler job (production, runs ~17:00 UTC daily), or
  * a manual `POST /jobs/run-reminders` endpoint (dev + a backup if the
    scheduler fails to start).

Send target rule:  opt-in=true ∧ has ≥1 incomplete path ∧ no activity in 3+
days ∧ already-sent dedupe (`reminder_sent_at < today`) ∧ not in 90-day
cool-off after 10 silent reminders in a row.

Activity = `learning_paths.last_active_date`; that field is updated by AP4
on every milestone completion. AP4's PATCH /milestones/{id} also resets
`User.no_activity_reminders_sent` so the cool-off only triggers on real
neglect.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import date, datetime, timedelta
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from models import LearningPath, Milestone, ReminderLog, User
import config

logger = logging.getLogger(__name__)

# Skip after this many silent reminders in a row.
SILENT_REMINDER_CAP = 10
SILENT_REMINDER_COOLOFF_DAYS = 90
# How many days of inactivity to wait before nudging.
INACTIVITY_DAYS = 3


# ---------------------------------------------------------------------------
# Signed unsubscribe tokens — HMAC over `user_id` with the auth SECRET_KEY.
# itsdangerous would do the same thing with less ceremony, but rolling our
# own keeps the dep list short (existing AP9 auth.py uses no signing libs).
# ---------------------------------------------------------------------------


def _secret() -> bytes:
    # AP38 — was a hardcoded `os.getenv("SECRET_KEY") or <public constant>`. That constant is in
    # the public repo, so an unset SECRET_KEY made every unsubscribe token forgeable.
    # require() now RAISES in production, and in development mints a per-PROCESS random
    # default — so a laptop still boots but the value is never a constant anyone can read
    # from git. (Tokens do not survive a dev restart, which is correct: they never should
    # have been verifiable with a published key.)
    return config.require("SECRET_KEY").encode("utf-8")


def make_unsubscribe_token(user_id: int) -> str:
    body = str(user_id).encode("utf-8")
    sig = hmac.new(_secret(), body, hashlib.sha256).hexdigest()
    return f"{user_id}.{sig}"


def verify_unsubscribe_token(token: str) -> int | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    if not raw.isdigit():
        return None
    expected = hmac.new(_secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return int(raw)


# ---------------------------------------------------------------------------
# Email send (Resend, with dev console-log fallback — matches AP9 pattern)
# ---------------------------------------------------------------------------


def _send_email(email: str, subject: str, html: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.info("[AP11] (no RESEND_API_KEY) would email %s — subject=%r", email, subject)
        return True
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev"),
                "to": [email], "subject": subject, "html": html,
            },
            timeout=10.0,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("[AP11] Resend send failed for %s: %s", email, exc)
        return False


def _build_reminder_html(next_title: str, path_title: str, app_url: str, unsubscribe_url: str) -> str:
    return (
        f"<p>Hi — it's been a few days since you worked on <strong>{path_title}</strong>.</p>"
        f"<p>Your next milestone is waiting: <strong>{next_title}</strong>.</p>"
        f"<p><a href=\"{app_url}\">Pick up where you left off →</a></p>"
        f"<p style=\"color:#94a3b8;font-size:12px;margin-top:24px\">"
        f"Don't want these? <a href=\"{unsubscribe_url}\">Unsubscribe</a> "
        f"in one click — no account needed."
        f"</p>"
    )


# ---------------------------------------------------------------------------
# Candidate selection + send loop
# ---------------------------------------------------------------------------


def _today_utc() -> date:
    return datetime.utcnow().date()


def _user_in_cooloff(user: User) -> bool:
    if (user.no_activity_reminders_sent or 0) < SILENT_REMINDER_CAP:
        return False
    if not user.reminder_sent_at:
        return False
    return (datetime.utcnow() - user.reminder_sent_at) < timedelta(days=SILENT_REMINDER_COOLOFF_DAYS)


def _pick_target_path(user_id: int, db: Session) -> LearningPath | None:
    """The user's most-recently-active incomplete path, oldest-active first when
    multiple stalled at the same date. Returns None if every owned path is done.
    """
    candidates = (
        db.query(LearningPath)
        .filter(LearningPath.user_id == user_id)
        .order_by(LearningPath.last_active_date.desc().nullslast(), LearningPath.id.desc())
        .all()
    )
    for p in candidates:
        total = len(p.milestones)
        if total == 0:
            continue
        completed = sum(1 for m in p.milestones if m.completed)
        if completed < total:
            return p
    return None


def _next_milestone(path: LearningPath) -> Milestone | None:
    incomplete = [m for m in path.milestones if not m.completed]
    if not incomplete:
        return None
    return sorted(incomplete, key=lambda m: m.order)[0]


def find_candidates(db: Session, today: date | None = None) -> Iterable[User]:
    """Yield users who should receive a reminder *right now*. Idempotent per
    calendar day — already-sent users today are filtered."""
    today = today or _today_utc()
    cutoff = today - timedelta(days=INACTIVITY_DAYS)
    users = (
        db.query(User)
        .filter(User.reminder_opt_in.is_(True))
        .all()
    )
    for u in users:
        # Dedupe per day
        if u.reminder_sent_at and u.reminder_sent_at.date() >= today:
            continue
        if _user_in_cooloff(u):
            continue
        path = _pick_target_path(u.id, db)
        if not path:
            continue
        last = path.last_active_date
        if last and last > cutoff:
            continue  # active enough
        yield u


def send_reminders(db: Session, today: date | None = None) -> dict:
    """Pump the queue once. Returns a small summary suitable for the manual
    `/jobs/run-reminders` endpoint and CI logs."""
    sent = 0
    failed = 0
    skipped = 0
    app_url = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")

    for user in find_candidates(db, today):
        path = _pick_target_path(user.id, db)
        if not path:
            skipped += 1
            continue
        milestone = _next_milestone(path)
        if not milestone:
            skipped += 1
            continue
        unsubscribe_url = f"{app_url}/unsubscribe?token={make_unsubscribe_token(user.id)}"
        html = _build_reminder_html(
            milestone.title, path.title, app_url=f"{app_url}/", unsubscribe_url=unsubscribe_url,
        )
        ok = _send_email(user.email, f"Your next step: {milestone.title}", html)
        status = "sent" if ok else "failed"
        if ok:
            sent += 1
            user.reminder_sent_at = datetime.utcnow()
            user.no_activity_reminders_sent = (user.no_activity_reminders_sent or 0) + 1
        else:
            failed += 1
        db.add(ReminderLog(user_id=user.id, path_id=path.id, status=status))

    db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped}


def reset_inactivity_counter(user_id: int, db: Session) -> None:
    """Called from AP4's milestone-complete handler. Restarts the user's
    cool-off countdown — proof of life clears the silent-reminder streak."""
    user = db.query(User).filter(User.id == user_id).first()
    if user and (user.no_activity_reminders_sent or 0) > 0:
        user.no_activity_reminders_sent = 0
        db.commit()

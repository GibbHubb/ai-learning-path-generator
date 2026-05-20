"""AP9 — passwordless magic-link auth + DB-backed sessions.

Design choices:
* Tokens are stored as `sha256(token)`; the raw token only travels in the
  verification URL emailed to the user.
* Session cookie is an opaque UUID hex string, HttpOnly + SameSite=Lax,
  with a server-side `sessions` table — lets us revoke instantly on logout.
* Anonymous paths are tracked via a separate `anon_id` cookie set on path
  creation, then *claimed* (assigned to user_id) on first successful verify.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DbSession

from database import get_db
from models import LearningPath, MagicLink, Session, User

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ap_session"
ANON_COOKIE = "ap_anon_id"

SESSION_TTL_DAYS = 30
MAGIC_LINK_TTL_MINUTES = 15

# Rate-limit magic-link requests: max N per email in the trailing window.
MAGIC_LINK_RATE_LIMIT = 3
MAGIC_LINK_RATE_WINDOW_SECONDS = 600  # 10 minutes
_magic_link_requests: dict[str, list[float]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def _resend_api_key() -> Optional[str]:
    return os.getenv("RESEND_API_KEY") or None


def _resend_from() -> str:
    return os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def _send_magic_link_email(email: str, link: str) -> None:
    """Send the verification email via Resend, or log to console in dev."""
    api_key = _resend_api_key()
    if not api_key:
        logger.info("[AP9] Magic link for %s (no RESEND_API_KEY): %s", email, link)
        return

    try:
        httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": _resend_from(),
                "to": [email],
                "subject": "Your sign-in link",
                "html": (
                    f"<p>Click to sign in to AI Learning Path:</p>"
                    f"<p><a href=\"{link}\">{link}</a></p>"
                    f"<p>This link expires in {MAGIC_LINK_TTL_MINUTES} minutes.</p>"
                ),
            },
            timeout=10.0,
        ).raise_for_status()
    except Exception as exc:  # pragma: no cover - external dep
        logger.warning("[AP9] Resend send failed for %s: %s", email, exc)


# ---------------------------------------------------------------------------
# Magic-link flow
# ---------------------------------------------------------------------------


def _under_rate_limit(email: str) -> bool:
    now = time.time()
    bucket = _magic_link_requests.setdefault(email.lower(), [])
    bucket[:] = [t for t in bucket if now - t < MAGIC_LINK_RATE_WINDOW_SECONDS]
    if len(bucket) >= MAGIC_LINK_RATE_LIMIT:
        return False
    bucket.append(now)
    return True


def request_magic_link(email: str, db: DbSession) -> None:
    """Generate, hash, store, and email a magic link for `email`."""
    norm_email = email.strip().lower()
    if "@" not in norm_email or len(norm_email) < 5:
        raise HTTPException(status_code=400, detail="Please provide a valid email address.")
    if not _under_rate_limit(norm_email):
        raise HTTPException(status_code=429, detail="Too many sign-in requests for this email. Try again in a few minutes.")

    token = secrets.token_urlsafe(32)
    db.add(MagicLink(
        email=norm_email,
        token_hash=_hash_token(token),
        expires_at=_utcnow() + timedelta(minutes=MAGIC_LINK_TTL_MINUTES),
    ))
    db.commit()

    link = f"{_app_base_url()}/auth/verify?token={token}"
    _send_magic_link_email(norm_email, link)


def verify_token(token: str, request: Request, response: Response, db: DbSession) -> User:
    """Verify the token, mark it used, find/create the user, start a session,
    set the session cookie, and claim any anonymous paths from this browser."""
    if not token:
        raise HTTPException(status_code=400, detail="Missing token.")

    th = _hash_token(token)
    record = (
        db.query(MagicLink)
        .filter(MagicLink.token_hash == th, MagicLink.used_at.is_(None))
        .order_by(MagicLink.id.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or already-used link.")
    if record.expires_at < _utcnow():
        raise HTTPException(status_code=400, detail="This sign-in link has expired.")

    record.used_at = _utcnow()

    user = db.query(User).filter(User.email == record.email).first()
    if not user:
        user = User(email=record.email)
        db.add(user)
        db.flush()
    user.last_login_at = _utcnow()

    # Create a session row + cookie
    session_id = uuid.uuid4().hex
    db.add(Session(
        id=session_id,
        user_id=user.id,
        expires_at=_utcnow() + timedelta(days=SESSION_TTL_DAYS),
    ))

    # Claim anonymous paths: any path with anon_session_id matching the
    # browser cookie that submitted this verify request.
    anon_id = request.cookies.get(ANON_COOKIE)
    if anon_id:
        (
            db.query(LearningPath)
            .filter(LearningPath.anon_session_id == anon_id, LearningPath.user_id.is_(None))
            .update({"user_id": user.id, "anon_session_id": None})
        )

    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        secure=os.getenv("APP_COOKIE_SECURE", "0") == "1",
    )
    if anon_id:
        # Clear the now-claimed anon cookie
        response.delete_cookie(ANON_COOKIE)
    return user


def end_session(response: Response, session_id: Optional[str], db: DbSession) -> None:
    if session_id:
        db.query(Session).filter(Session.id == session_id).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_current_user_optional(
    db: DbSession = Depends(get_db),
    ap_session: Optional[str] = Cookie(default=None),
) -> Optional[User]:
    if not ap_session:
        return None
    sess = db.query(Session).filter(Session.id == ap_session).first()
    if not sess or sess.expires_at < _utcnow():
        return None
    return db.query(User).filter(User.id == sess.user_id).first()


def require_user(current: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not current:
        raise HTTPException(status_code=401, detail="Sign-in required.")
    return current


# ---------------------------------------------------------------------------
# Anonymous-id cookie helper used by path-create paths
# ---------------------------------------------------------------------------


def ensure_anon_id(request: Request, response: Response) -> str:
    """Return the visitor's anon_id (setting one if missing)."""
    existing = request.cookies.get(ANON_COOKIE)
    if existing:
        return existing
    new_id = uuid.uuid4().hex
    response.set_cookie(
        key=ANON_COOKIE,
        value=new_id,
        httponly=True,
        samesite="lax",
        max_age=365 * 24 * 3600,
        secure=os.getenv("APP_COOKIE_SECURE", "0") == "1",
    )
    return new_id

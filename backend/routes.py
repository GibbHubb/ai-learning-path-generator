import logging
import re
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Request, Response
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime
import json

from database import get_db
from models import LearningPath, Milestone, MilestoneNote, MilestoneTask, PathRevision, User
from ai_service import generate_learning_path, stream_learning_path, enrich_milestone_resources, adjust_difficulty
from auth import (
    SESSION_COOKIE,
    end_session,
    ensure_anon_id,
    get_current_user_optional,
    request_magic_link,
    require_user,
    verify_token,
)
from notes_service import parse_difficulty_flag
from reminders import (
    reset_inactivity_counter,
    send_reminders,
    verify_unsubscribe_token,
)
from quizzes import grade_attempt, get_or_generate_quiz, PASS_THRESHOLD  # noqa: F401
from models import MilestoneQuiz, QuizAttempt
import json as _json_for_quiz
from scheduling import estimate_schedule, weekly_study_blocks  # AP25 / AP29

logger = logging.getLogger(__name__)

router = APIRouter()

# Pydantic schemas for request/response
class MilestoneCreate(BaseModel):
    title: str
    description: str
    order: int
    estimated_hours: float
    resources: List[str]

class LearningPathCreate(BaseModel):
    goal: str
    experience_level: str
    time_commitment: str
    language: Optional[str] = "en"  # AP27 — unknown values fall back to en in ai_service

class MilestoneTaskOut(BaseModel):
    id: int
    order: int
    title: str
    completed: bool
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MilestoneResponse(BaseModel):
    id: int
    title: str
    description: str
    order: int
    estimated_hours: float
    resources: List[str]
    completed: bool
    completed_at: Optional[datetime]
    tasks: List[MilestoneTaskOut] = []  # AP23

    class Config:
        from_attributes = True

class LearningPathResponse(BaseModel):
    id: int
    title: str
    description: str
    experience_level: str
    time_commitment: str
    is_public: bool
    total_xp: int
    streak_days: int
    category: Optional[str] = None  # AP6
    created_at: datetime
    milestones: List[MilestoneResponse]
    current_version: Optional[str] = None  # AP24 — e.g. "v3 — harder"
    language: Optional[str] = "en"  # AP27

    class Config:
        from_attributes = True


# AP24 — revisions listing
class RevisionOut(BaseModel):
    revision_number: int
    trigger: str
    created_at: datetime

    class Config:
        from_attributes = True

class MilestoneUpdate(BaseModel):
    completed: bool


# AP23 — sub-task payloads
class MilestoneTaskCreate(BaseModel):
    title: str


class MilestoneTaskUpdate(BaseModel):
    completed: Optional[bool] = None
    title: Optional[str] = None

class ShareUpdate(BaseModel):
    is_public: bool = True


# AP5 — difficulty feedback payload
class DifficultyFeedback(BaseModel):
    milestone_id: int
    feedback: str  # "too_easy" | "just_right" | "too_hard" (or any "ok"-like value)


# AP9 — auth payloads
class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyRequest(BaseModel):
    token: str


class UserResponse(BaseModel):
    id: int
    email: str
    created_at: datetime
    reminder_opt_in: bool = False  # AP11
    is_public_profile: bool = False  # AP30

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# AP9 — Auth routes
# ---------------------------------------------------------------------------


@router.post("/auth/request-link")
async def auth_request_link(payload: MagicLinkRequest, db: Session = Depends(get_db)):
    """Email a single-use magic-link URL to the user. 15-minute TTL.

    Returns the same shape regardless of whether the email is new — avoids
    leaking whether an account exists for that address.
    """
    request_magic_link(payload.email, db)
    return {"ok": True, "message": "If that email is valid, a sign-in link is on the way."}


@router.post("/auth/verify", response_model=UserResponse)
async def auth_verify(
    payload: VerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchange a magic-link token for a session cookie. Also claims any
    paths created during the anonymous session in this browser."""
    return verify_token(payload.token, request, response, db)


@router.get("/auth/me", response_model=Optional[UserResponse])
async def auth_me(current: Optional[User] = Depends(get_current_user_optional)):
    return current


@router.post("/auth/logout")
async def auth_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    end_session(response, request.cookies.get(SESSION_COOKIE), db)
    return {"ok": True}


def _parse_resources(raw: str) -> list:
    """Parse milestone resources — handles both string lists and JSON objects."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return [raw]
    except (json.JSONDecodeError, TypeError):
        return [raw] if raw else []


def _build_milestone_response(m: Milestone) -> MilestoneResponse:
    resources = _parse_resources(m.resources)
    # Flatten to strings for MilestoneResponse (frontend handles both)
    flat = []
    for r in resources:
        if isinstance(r, dict):
            flat.append(json.dumps(r))  # frontend will parse JSON objects
        else:
            flat.append(str(r))
    return MilestoneResponse(
        id=m.id, title=m.title, description=m.description,
        order=m.order, estimated_hours=m.estimated_hours,
        resources=flat, completed=m.completed, completed_at=m.completed_at,
        tasks=[MilestoneTaskOut.model_validate(t) for t in sorted(m.tasks, key=lambda t: t.order)],  # AP23
    )


# AP23 — single source of truth for "milestone completion + XP/streak +
# inactivity reset". Used by the manual PATCH, the quiz-pass auto-complete,
# and the sub-task last-tick auto-complete (and its reverse) so all three
# paths move XP/streak identically.
#
# Streak rule on uncomplete: matches the pre-existing PATCH behaviour —
# streak/last_active_date are NOT reversed (streak counts an active day,
# which still happened). Only completed/completed_at + total_xp change.
def complete_milestone(milestone: Milestone, completed: bool, db: Session) -> tuple[int, int]:
    milestone.completed = completed
    milestone.completed_at = datetime.utcnow() if completed else None

    path = db.query(LearningPath).filter(LearningPath.id == milestone.learning_path_id).first()
    if not path:
        return (0, 0)

    all_milestones = db.query(Milestone).filter(Milestone.learning_path_id == path.id).all()
    path.total_xp = 10 * sum(1 for m in all_milestones if m.completed)

    if completed:
        today = date.today()
        if path.last_active_date == today - timedelta(days=1):
            path.streak_days = (path.streak_days or 0) + 1
        elif path.last_active_date != today:
            path.streak_days = 1
        path.last_active_date = today
        if path.user_id:
            reset_inactivity_counter(path.user_id, db)

    return (path.total_xp, path.streak_days or 0)


def _build_path_response(path: LearningPath) -> LearningPathResponse:
    return LearningPathResponse(
        id=path.id, title=path.title, description=path.description,
        experience_level=path.experience_level, time_commitment=path.time_commitment,
        is_public=path.is_public if path.is_public is not None else False,
        total_xp=path.total_xp if path.total_xp is not None else 0,
        streak_days=path.streak_days if path.streak_days is not None else 0,
        category=getattr(path, "category", None),  # AP6
        created_at=path.created_at,
        milestones=sorted([_build_milestone_response(m) for m in path.milestones], key=lambda x: x.order),
        current_version=_current_version_label(path),  # AP24
        language=getattr(path, "language", "en") or "en",  # AP27
    )


# AP24 — version history helpers.
def _milestones_to_snapshot(path: LearningPath) -> str:
    """Serialise the path's current milestones to a JSON string suitable
    for a PathRevision row. Captures the fields we need to faithfully
    restore — including completed/completed_at so progress is preserved
    by value when an old revision is restored."""
    payload = [
        {
            "title": m.title,
            "description": m.description,
            "order": m.order,
            "estimated_hours": m.estimated_hours,
            "resources": m.resources,
            "completed": bool(m.completed),
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            "difficulty_feedback": m.difficulty_feedback,
        }
        for m in sorted(path.milestones, key=lambda x: x.order or 0)
    ]
    return json.dumps(payload)


def snapshot_revision(path: LearningPath, trigger: str, db: Session) -> PathRevision:
    """Append a new PathRevision row for the path's current milestone set."""
    last = (
        db.query(PathRevision)
        .filter(PathRevision.learning_path_id == path.id)
        .order_by(PathRevision.revision_number.desc())
        .first()
    )
    next_num = (last.revision_number + 1) if last else 1
    rev = PathRevision(
        learning_path_id=path.id,
        revision_number=next_num,
        milestones_json=_milestones_to_snapshot(path),
        trigger=trigger,
    )
    db.add(rev)
    db.flush()
    return rev


def _current_version_label(path: LearningPath) -> str:
    """Build the share-page version label. Paths created before AP24 have
    no revisions yet and show a bare 'v1'."""
    revs = sorted(path.revisions, key=lambda r: r.revision_number) if path.revisions else []
    if not revs:
        return "v1"
    cur = revs[-1]
    trig = cur.trigger or "initial"
    return f"v{cur.revision_number}" if trig == "initial" else f"v{cur.revision_number} — {trig}"


def _enqueue_enrichment(background_tasks: BackgroundTasks, milestones, goal: str, language: str = "en"):
    """Fire background enrichment for each milestone.

    AP28: `language` is threaded through so a non-English path's enriched
    resources come back in-language (captured as a plain string at enqueue
    time, before the request scope ends)."""
    for m in milestones:
        background_tasks.add_task(
            enrich_milestone_resources,
            m.id, m.title, m.description, goal, language,
        )


# API Routes
@router.post("/generate", response_model=LearningPathResponse)
async def create_learning_path_endpoint(
    path_data: LearningPathCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Generate a new learning path using AI"""
    try:
        ai_result = generate_learning_path(
            path_data.goal, path_data.experience_level, path_data.time_commitment,
            path_data.language or "en",  # AP27
        )

        # AP9 — record ownership: signed-in users get user_id; anonymous
        # visitors get an anon_session_id cookie that maps to claim-on-verify.
        owner_id = current_user.id if current_user else None
        anon_id = None if current_user else ensure_anon_id(request, response)

        db_path = LearningPath(
            title=ai_result["path_title"],
            description=ai_result["path_description"],
            experience_level=path_data.experience_level,
            time_commitment=path_data.time_commitment,
            category=ai_result.get("category", "Other"),  # AP6
            user_id=owner_id,
            anon_session_id=anon_id,
            language=path_data.language or "en",  # AP27
        )
        db.add(db_path)
        db.flush()

        created = []
        for idx, milestone_data in enumerate(ai_result["milestones"]):
            milestone = Milestone(
                learning_path_id=db_path.id,
                title=milestone_data["title"],
                description=milestone_data["description"],
                order=idx,
                estimated_hours=milestone_data["estimated_hours"],
                resources=json.dumps(milestone_data["resources"]),
                completed=False,
            )
            db.add(milestone)
            db.flush()
            created.append(milestone)

        db.commit()
        db.refresh(db_path)
        # AP24 — initial revision so future regenerate/restore have a v1 to fall back to.
        snapshot_revision(db_path, "initial", db)
        db.commit()
        db.refresh(db_path)

        # AP3 — background enrichment (AP28: in the path's language)
        _enqueue_enrichment(background_tasks, created, path_data.goal, db_path.language)

        return _build_path_response(db_path)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating learning path: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/stream")
async def generate_stream(
    path_data: LearningPathCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Stream a new learning path via Server-Sent Events."""

    # AP9 — capture ownership at request time so it's available inside the
    # generator after the request scope ends.
    owner_id = current_user.id if current_user else None
    anon_id = None if current_user else ensure_anon_id(request, response)

    lang = path_data.language or "en"  # AP27 — captured for the generator closure

    def event_generator():
        try:
            milestones_data = list(stream_learning_path(
                path_data.goal, path_data.experience_level, path_data.time_commitment, lang,
            ))

            from ai_service import generate_learning_path as _glp
            ai_result = _glp(path_data.goal, path_data.experience_level, path_data.time_commitment, lang)

            db_path = LearningPath(
                title=ai_result["path_title"],
                description=ai_result["path_description"],
                experience_level=path_data.experience_level,
                time_commitment=path_data.time_commitment,
                category=ai_result.get("category", "Other"),  # AP6
                user_id=owner_id,
                anon_session_id=anon_id,
                language=lang,  # AP27
            )
            db.add(db_path)
            db.flush()

            created_milestones = []
            for idx, milestone_data in enumerate(milestones_data):
                m = Milestone(
                    learning_path_id=db_path.id,
                    title=milestone_data["title"],
                    description=milestone_data["description"],
                    order=idx,
                    estimated_hours=milestone_data["estimated_hours"],
                    resources=json.dumps(milestone_data["resources"]),
                    completed=False,
                )
                db.add(m)
                db.flush()
                created_milestones.append(m)

                payload = {
                    "id": m.id,
                    "title": m.title,
                    "description": m.description,
                    "order": m.order,
                    "estimated_hours": m.estimated_hours,
                    "resources": milestone_data["resources"],
                    "completed": False,
                    "completed_at": None,
                }
                yield f"data: {json.dumps(payload)}\n\n"

            db.commit()
            db.refresh(db_path)
            # AP24 — initial revision (streaming generate).
            snapshot_revision(db_path, "initial", db)
            db.commit()
            db.refresh(db_path)

            # AP3 — background enrichment (AP28: in the path's language)
            _enqueue_enrichment(background_tasks, created_milestones, path_data.goal, lang)

            full_path = {
                "id": db_path.id,
                "title": db_path.title,
                "description": db_path.description,
                "experience_level": db_path.experience_level,
                "time_commitment": db_path.time_commitment,
                "is_public": False,
                "total_xp": 0,
                "streak_days": 0,
                "category": db_path.category,  # AP6
                "created_at": db_path.created_at.isoformat(),
                "milestones": [
                    {
                        "id": m.id,
                        "title": m.title,
                        "description": m.description,
                        "order": m.order,
                        "estimated_hours": m.estimated_hours,
                        "resources": json.loads(m.resources),
                        "completed": m.completed,
                        "completed_at": m.completed_at.isoformat() if m.completed_at else None,
                    }
                    for m in sorted(created_milestones, key=lambda x: x.order)
                ],
            }
            yield f"event: done\ndata: {json.dumps(full_path)}\n\n"

        except Exception as e:
            db.rollback()
            logger.error(f"Error in stream endpoint: {e}")
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/paths", response_model=List[LearningPathResponse])
async def get_all_paths(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List paths visible to the caller.

    AP9 — when signed in, returns the user's saved paths. When anonymous,
    returns paths tied to the visitor's `ap_anon_id` cookie (so a session
    that just generated a path can still see it before signing up). The
    pre-AP9 behavior of returning every path globally is gone.
    """
    q = db.query(LearningPath)
    if current_user:
        q = q.filter(LearningPath.user_id == current_user.id)
    else:
        anon_id = request.cookies.get("ap_anon_id")
        if not anon_id:
            return []
        q = q.filter(LearningPath.anon_session_id == anon_id)
    paths = q.order_by(LearningPath.created_at.desc()).all()
    return [_build_path_response(p) for p in paths]


@router.get("/paths/me", response_model=List[LearningPathResponse])
async def get_my_paths(
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """My-paths page — explicitly auth-required."""
    paths = (
        db.query(LearningPath)
        .filter(LearningPath.user_id == current.id)
        .order_by(LearningPath.created_at.desc())
        .all()
    )
    return [_build_path_response(p) for p in paths]


# AP10 — fork a public path into the caller's account.
@router.post("/paths/{path_id}/fork", response_model=LearningPathResponse)
async def fork_path(
    path_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Deep-copy a public path + its milestones into the caller's account.

    Source path stays untouched; the new copy starts with milestone progress
    reset to zero, fork_count=0, and forked_from_id pointing at the source's
    nearest ancestor (the path being forked, not its great-grandparent).
    Original author is captured once on fork — if the source is itself a
    fork, this resolves to the *origin's* original_author_id, so re-forks
    keep attribution to the true author.
    """
    src = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Path not found.")
    if not src.is_public:
        raise HTTPException(status_code=403, detail="This path is private and can't be forked.")

    # Original author: prefer the source's existing original_author_id (so
    # forks-of-forks attribute to the actual author), else the source's owner.
    original_author_id = src.original_author_id or src.user_id

    new_path = LearningPath(
        title=f"My copy of {src.title}",
        description=src.description,
        experience_level=src.experience_level,
        time_commitment=src.time_commitment,
        category=src.category,
        language=getattr(src, "language", "en") or "en",  # AP27 — fork preserves language
        is_public=False,
        total_xp=0,
        streak_days=0,
        last_active_date=None,
        user_id=current.id,
        anon_session_id=None,
        forked_from_id=src.id,
        original_author_id=original_author_id,
        fork_count=0,
    )
    db.add(new_path)
    db.flush()

    for src_m in sorted(src.milestones, key=lambda m: m.order):
        db.add(Milestone(
            learning_path_id=new_path.id,
            title=src_m.title,
            description=src_m.description,
            order=src_m.order,
            estimated_hours=src_m.estimated_hours,
            resources=src_m.resources,
            completed=False,
            completed_at=None,
            difficulty_feedback=None,
        ))

    src.fork_count = (src.fork_count or 0) + 1
    db.commit()
    db.refresh(new_path)
    # AP24 — initial revision on the forked path (its own version history starts fresh).
    snapshot_revision(new_path, "initial", db)
    db.commit()
    db.refresh(new_path)
    return _build_path_response(new_path)


@router.get("/paths/{path_id}", response_model=LearningPathResponse)
async def get_path(path_id: int, db: Session = Depends(get_db)):
    """Get a specific learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    return _build_path_response(path)


# AP25 — calendar export. Mirrors GET /paths/{id}'s open access pattern.
@router.get("/paths/{path_id}/calendar.ics")
async def export_path_calendar(
    path_id: int,
    study_blocks: bool = False,
    reminder_days: int = 1,
    study_block_hour: Optional[int] = Query(None, ge=0, le=23),
    study_block_minute: int = Query(0, ge=0, le=59),
    db: Session = Depends(get_db),
):
    """Build an .ics download of the path's milestones scheduled from
    today across `estimate_schedule`'s span. All-day VEVENTs, one per
    milestone, RFC-5545 valid.

    AP29:
    - Every milestone VEVENT carries a VALARM firing `reminder_days` (default
      1) before the due date.
    - When `study_blocks=1`, a recurring weekly all-day study-block VEVENT is
      added (RRULE FREQ=WEEKLY, UNTIL=schedule finish). Default (off) keeps
      the AP25 milestone-only calendar unchanged apart from the VALARMs.

    AP29-fu1:
    - Adding `study_block_hour` (0-23, optional `study_block_minute`) upgrades
      that block from all-day to a *timed* VEVENT starting at that hour and
      running for the weekly hour budget (capped at 8h). Omitting it keeps the
      all-day block exactly as AP29 shipped it, so existing links are
      unaffected. Ignored unless `study_blocks=1`.
    """
    from icalendar import Calendar, Event, Alarm  # local import — keeps cold-start light
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    lead = reminder_days if reminder_days and reminder_days > 0 else 1
    finish, schedule = estimate_schedule(path.milestones, path.time_commitment)

    cal = Calendar()
    cal.add("prodid", "-//ai-learning-path-generator//AP25//EN")
    cal.add("version", "2.0")
    for m, d in schedule:
        ev = Event()
        ev.add("uid", f"path-{path.id}-milestone-{m.id}@ai-learning-path")
        ev.add("summary", (m.title or "Milestone").strip())
        if m.description:
            ev.add("description", m.description.strip())
        ev.add("dtstart", d)  # date (all-day)
        ev.add("dtend", d + timedelta(days=1))  # exclusive end for all-day
        # AP29 — reminder alarm, relative trigger (well-supported for all-day).
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"Milestone due soon: {(m.title or 'Milestone').strip()}")
        alarm.add("trigger", timedelta(days=-lead))
        ev.add_component(alarm)
        cal.add_component(ev)

    # AP29 — optional recurring weekly study block.
    if study_blocks:
        sb = weekly_study_blocks(path.time_commitment, date.today(), finish,
                                 hour=study_block_hour, minute=study_block_minute)
        block = Event()
        block.add("uid", f"path-{path.id}-studyblock@ai-learning-path")
        block.add("description", "Recurring weekly study session for your learning path.")
        if "dtstart_dt" in sb:
            # AP29-fu1 — real time-boxed commitment. Floating (naive) times, so
            # the block lands at the requested wall-clock hour in the viewer's
            # own timezone rather than being pinned to the server's.
            block.add("summary", f"📚 Study block ({sb['block_hours']}h)")
            block.add("dtstart", sb["dtstart_dt"])
            block.add("dtend", sb["dtstart_dt"] + sb["duration"])
        else:
            block.add("summary", f"📚 Study block (~{sb['hours_per_week']}h/week)")
            block.add("dtstart", sb["dtstart"])  # date (all-day)
            block.add("dtend", sb["dtstart"] + timedelta(days=1))
        block.add("rrule", {"freq": "weekly", "until": sb["until"]})
        cal.add_component(block)

    body = cal.to_ical()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", (path.title or "learning-path"))[:60].strip("-") or "learning-path"
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'},
    )


# AP2 — public read-only endpoint (no auth)
@router.get("/paths/{path_id}/public", response_model=LearningPathResponse)
async def get_public_path(path_id: int, db: Session = Depends(get_db)):
    """Get a public learning path — no authentication required."""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path or not path.is_public:
        raise HTTPException(status_code=404, detail="Path not found or not public")
    return _build_path_response(path)


# AP2 — toggle public sharing
@router.patch("/paths/{path_id}/share", response_model=LearningPathResponse)
async def share_path(path_id: int, body: ShareUpdate, db: Session = Depends(get_db)):
    """Set a path's public sharing status."""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    path.is_public = body.is_public
    db.commit()
    db.refresh(path)
    return _build_path_response(path)


# AP4 — milestone completion with XP + streak (delegates to complete_milestone)
@router.patch("/milestones/{milestone_id}")
async def update_milestone(milestone_id: int, update: MilestoneUpdate, db: Session = Depends(get_db)):
    """Update milestone completion status + recompute XP and streak.

    AP23: when sub-tasks exist, manual (un)complete cascades to every task
    so task-state and milestone-state stay coherent.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")

    total_xp, streak_days = complete_milestone(milestone, update.completed, db)

    # AP23 (Q2 = yes): manual complete also ticks all tasks; uncomplete unticks.
    if milestone.tasks:
        now = datetime.utcnow() if update.completed else None
        for t in milestone.tasks:
            t.completed = update.completed
            t.completed_at = now

    db.commit()

    return {
        "success": True,
        "milestone_id": milestone_id,
        "completed": milestone.completed,
        "total_xp": total_xp,
        "streak_days": streak_days,
    }


# AP23 — sub-task CRUD. Owner-scoped: the task's milestone must belong to a
# path owned by the requester. Toggling the *last* incomplete task auto-
# completes the milestone via complete_milestone(); un-ticking from
# all-done reverts. Creating a task on an already-completed milestone does
# NOT auto-uncomplete (user intent preserved).
def _load_owned_milestone(milestone_id: int, current: User, db: Session) -> Milestone:
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    path = db.query(LearningPath).filter(LearningPath.id == milestone.learning_path_id).first()
    if not path or path.user_id != current.id:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return milestone


def _load_owned_task(task_id: int, current: User, db: Session) -> tuple["MilestoneTask", Milestone]:
    task = db.query(MilestoneTask).filter(MilestoneTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    milestone = _load_owned_milestone(task.milestone_id, current, db)
    return task, milestone


@router.get("/milestones/{milestone_id}/tasks", response_model=List[MilestoneTaskOut])
async def list_milestone_tasks(
    milestone_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    milestone = _load_owned_milestone(milestone_id, current, db)
    return [MilestoneTaskOut.model_validate(t) for t in sorted(milestone.tasks, key=lambda t: t.order)]


@router.post("/milestones/{milestone_id}/tasks", response_model=MilestoneTaskOut)
async def create_milestone_task(
    milestone_id: int,
    payload: MilestoneTaskCreate,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    milestone = _load_owned_milestone(milestone_id, current, db)
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Task title is required")
    next_order = (max((t.order for t in milestone.tasks), default=-1) + 1)
    task = MilestoneTask(milestone_id=milestone.id, order=next_order, title=title)
    db.add(task)
    db.commit()
    db.refresh(task)
    return MilestoneTaskOut.model_validate(task)


@router.patch("/tasks/{task_id}")
async def update_milestone_task(
    task_id: int,
    payload: MilestoneTaskUpdate,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    task, milestone = _load_owned_task(task_id, current, db)

    if payload.title is not None:
        new_title = payload.title.strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="Task title cannot be empty")
        task.title = new_title

    state_changed = False
    if payload.completed is not None and payload.completed != task.completed:
        task.completed = payload.completed
        task.completed_at = datetime.utcnow() if payload.completed else None
        state_changed = True

    response = {
        "success": True,
        "task": None,  # filled below
        "milestone_auto_completed": False,
        "milestone_auto_uncompleted": False,
        "total_xp": None,
        "streak_days": None,
    }

    # Auto-derive milestone state from tasks ONLY when a task's completed
    # state was actually toggled in this request.
    if state_changed:
        sibling_tasks = milestone.tasks  # includes the in-session change
        all_done = all(t.completed for t in sibling_tasks) and len(sibling_tasks) > 0
        if all_done and not milestone.completed:
            total_xp, streak_days = complete_milestone(milestone, True, db)
            response["milestone_auto_completed"] = True
            response["total_xp"] = total_xp
            response["streak_days"] = streak_days
        elif not all_done and milestone.completed:
            total_xp, streak_days = complete_milestone(milestone, False, db)
            response["milestone_auto_uncompleted"] = True
            response["total_xp"] = total_xp
            response["streak_days"] = streak_days

    db.commit()
    db.refresh(task)
    response["task"] = MilestoneTaskOut.model_validate(task)
    return response


@router.delete("/tasks/{task_id}")
async def delete_milestone_task(
    task_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    task, milestone = _load_owned_task(task_id, current, db)
    db.delete(task)
    db.flush()  # so milestone.tasks reflects the removal
    # If removing this task makes the remaining set all-complete, auto-complete.
    remaining = [t for t in milestone.tasks if t.id != task.id]
    response = {"success": True, "milestone_auto_completed": False}
    if remaining and all(t.completed for t in remaining) and not milestone.completed:
        complete_milestone(milestone, True, db)
        response["milestone_auto_completed"] = True
    db.commit()
    return response


# ---------------------------------------------------------------------------
# AP26 — Achievement badges
# ---------------------------------------------------------------------------

# Declarative badge catalogue: single source of truth, served from the API
# so the frontend never re-defines thresholds (no drift). A badge is earned
# when `stats[metric] >= threshold`.
BADGES = [
    # Bronze
    {"id": "first_path",  "tier": "bronze", "label": "First path completed", "metric": "completed_paths", "threshold": 1},
    {"id": "xp_100",      "tier": "bronze", "label": "100 XP",               "metric": "total_xp",        "threshold": 100},
    {"id": "xp_500",      "tier": "bronze", "label": "500 XP",               "metric": "total_xp",        "threshold": 500},
    {"id": "xp_1000",     "tier": "bronze", "label": "First 1,000 XP",       "metric": "total_xp",        "threshold": 1000},
    # Silver
    {"id": "streak_5",    "tier": "silver", "label": "5-day streak",         "metric": "best_streak",     "threshold": 5},
    {"id": "paths_5",     "tier": "silver", "label": "5 paths completed",    "metric": "completed_paths", "threshold": 5},
    {"id": "xp_2500",     "tier": "silver", "label": "2,500 XP",             "metric": "total_xp",        "threshold": 2500},
    # Gold
    {"id": "streak_10",   "tier": "gold",   "label": "10-day streak",        "metric": "best_streak",     "threshold": 10},
    {"id": "paths_10",    "tier": "gold",   "label": "10 paths completed",   "metric": "completed_paths", "threshold": 10},
    {"id": "xp_5000",     "tier": "gold",   "label": "5,000 XP",             "metric": "total_xp",        "threshold": 5000},
]


def _compute_user_stats(user: User, db: Session) -> dict:
    """Aggregate a user's XP/streak/path-completion + derived earned badges.

    Shared by the owner view (`/me/stats`) and the public view
    (`/u/{id}/stats`) so the two can't drift. Contains NO email/PII — only
    XP, streak, path counts, and badge ids — so it is safe to expose publicly.
    """
    user_paths = (
        db.query(LearningPath)
        .filter(LearningPath.user_id == user.id)
        .all()
    )
    total_xp = sum((p.total_xp or 0) for p in user_paths)
    best_streak = max((p.streak_days or 0 for p in user_paths), default=0)
    # "Path completed" = every milestone completed AND ≥1 milestone exists.
    completed_paths = sum(
        1 for p in user_paths
        if p.milestones and all(m.completed for m in p.milestones)
    )
    stats = {
        "total_xp": total_xp,
        "best_streak": best_streak,
        "completed_paths": completed_paths,
        "total_paths": len(user_paths),
    }
    earned_ids = [b["id"] for b in BADGES if stats[b["metric"]] >= b["threshold"]]
    return {**stats, "earned_badges": earned_ids, "badges": BADGES}


@router.get("/me/stats")
async def me_stats(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Aggregate XP/streak/path-completion + derived earned badges.
    Badges are a pure projection of the stats — no badge table."""
    return _compute_user_stats(current, db)


# ---------------------------------------------------------------------------
# AP30 — opt-in public profile
# ---------------------------------------------------------------------------

class ProfileVisibilityUpdate(BaseModel):
    is_public_profile: bool


@router.patch("/me/profile/visibility")
async def set_profile_visibility(
    body: ProfileVisibilityUpdate,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Owner-only toggle for public profile visibility."""
    current.is_public_profile = body.is_public_profile
    db.commit()
    db.refresh(current)
    return {"is_public_profile": current.is_public_profile}


@router.get("/u/{user_id}/stats")
async def public_user_stats(user_id: int, db: Session = Depends(get_db)):
    """PUBLIC (no auth) profile stats for an opted-in user.

    Returns the same PII-free shape as `/me/stats`. Returns 404 both when the
    user doesn't exist AND when their profile is private — the two are
    indistinguishable so a private profile never leaks its existence."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_public_profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _compute_user_stats(user, db)


@router.delete("/paths/{path_id}")
async def delete_path(path_id: int, db: Session = Depends(get_db)):
    """Delete a learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    db.delete(path)
    db.commit()
    return {"success": True, "message": "Learning path deleted"}


# AP5 — record difficulty feedback; optionally regenerate remaining milestones
@router.post("/milestones/{milestone_id}/feedback", response_model=LearningPathResponse)
async def milestone_feedback(
    milestone_id: int,
    body: DifficultyFeedback,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Record user's difficulty feedback on a milestone.

    - Always stores `milestone.difficulty_feedback = feedback`.
    - If feedback is "too_easy" or "too_hard", regenerates remaining incomplete
      milestones (order > anchor.order) at adjusted difficulty via OpenAI,
      deletes the old remaining rows and inserts the new ones preserving
      the order sequence.
    - Otherwise just records the feedback and returns the path unchanged.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")

    path = db.query(LearningPath).filter(LearningPath.id == milestone.learning_path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    milestone.difficulty_feedback = body.feedback

    if body.feedback not in ("too_easy", "too_hard"):
        db.commit()
        db.refresh(path)
        return _build_path_response(path)

    # Split milestones into completed vs. remaining (ahead of this one, incomplete)
    all_milestones = (
        db.query(Milestone)
        .filter(Milestone.learning_path_id == path.id)
        .order_by(Milestone.order)
        .all()
    )
    completed_milestones = [m for m in all_milestones if m.completed]
    to_replace = [
        m for m in all_milestones
        if (not m.completed) and m.order > milestone.order
    ]

    if not to_replace:
        # Nothing to regenerate — just save the feedback
        db.commit()
        db.refresh(path)
        return _build_path_response(path)

    completed_payload = [
        {"title": m.title, "description": m.description} for m in completed_milestones
    ]
    remaining_payload = [
        {"title": m.title, "description": m.description} for m in to_replace
    ]
    # Preserve the exact order sequence of the replaced rows
    orders_to_reuse = [m.order for m in to_replace]

    # Capture primitives before mutation for the AI call
    goal = path.title
    experience_level = path.experience_level
    time_commitment = path.time_commitment
    path_title = path.title
    path_description = path.description
    feedback = body.feedback
    path_language = getattr(path, "language", "en") or "en"  # AP27

    # Regenerate remaining milestones via OpenAI
    try:
        new_milestones = adjust_difficulty(
            goal=goal,
            experience_level=experience_level,
            time_commitment=time_commitment,
            path_title=path_title,
            path_description=path_description,
            completed_milestones=completed_payload,
            remaining_milestones=remaining_payload,
            feedback=feedback,
            language=path_language,  # AP27 — keep regenerated milestones in same language
        )
    except Exception as e:
        db.rollback()
        logger.error(f"adjust_difficulty failed for path {path.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to adjust difficulty: {e}")

    # AP24 — snapshot the pre-change milestone set BEFORE the destructive
    # delete so the user can revert. trigger derived from the feedback.
    snapshot_revision(path, "harder" if body.feedback == "too_easy" else "easier", db)

    # Delete the old remaining rows, insert the new ones reusing the order sequence
    for m in to_replace:
        db.delete(m)
    db.flush()

    created = []
    for idx, md in enumerate(new_milestones):
        # If the AI returned fewer than expected, keep ascending orders past the last reused one
        if idx < len(orders_to_reuse):
            new_order = orders_to_reuse[idx]
        else:
            new_order = orders_to_reuse[-1] + (idx - len(orders_to_reuse) + 1) if orders_to_reuse else milestone.order + idx + 1
        new_m = Milestone(
            learning_path_id=path.id,
            title=md.get("title", f"Milestone {new_order + 1}"),
            description=md.get("description", ""),
            order=new_order,
            estimated_hours=md.get("estimated_hours", 0) or 0,
            resources=json.dumps(md.get("resources", [])),
            completed=False,
        )
        db.add(new_m)
        db.flush()
        created.append(new_m)

    db.commit()
    db.refresh(path)

    # AP3 — enrich the regenerated milestones in background
    # (AP28: keep them in the path's language)
    for m in created:
        background_tasks.add_task(
            enrich_milestone_resources,
            m.id, m.title, m.description, goal, path_language,
        )

    return _build_path_response(path)


# AP24 — list a path's revision history (owner only).
def _load_owned_path(path_id: int, current: User, db: Session) -> LearningPath:
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path or path.user_id != current.id:
        raise HTTPException(status_code=404, detail="Learning path not found")
    return path


@router.get("/paths/{path_id}/revisions", response_model=List[RevisionOut])
async def list_path_revisions(
    path_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    path = _load_owned_path(path_id, current, db)
    revs = sorted(path.revisions, key=lambda r: r.revision_number)
    return [RevisionOut.model_validate(r) for r in revs]


# AP24 — restore a revision: replace current milestones with the snapshot,
# then append a new "restore" revision so history is append-only.
@router.post("/paths/{path_id}/revisions/{revision_number}/restore", response_model=LearningPathResponse)
async def restore_path_revision(
    path_id: int,
    revision_number: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    path = _load_owned_path(path_id, current, db)
    target = next(
        (r for r in path.revisions if r.revision_number == revision_number),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Revision not found")

    try:
        snapshot = json.loads(target.milestones_json)
        if not isinstance(snapshot, list):
            raise ValueError("snapshot is not a list")
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Stored revision is unreadable")

    # Wipe current milestones (their MilestoneTasks/MilestoneNotes cascade-
    # delete by the model relationships) and recreate from the snapshot.
    for m in list(path.milestones):
        db.delete(m)
    db.flush()

    for idx, entry in enumerate(snapshot):
        completed_at = entry.get("completed_at")
        if completed_at:
            try:
                completed_at = datetime.fromisoformat(completed_at)
            except (TypeError, ValueError):
                completed_at = None
        db.add(Milestone(
            learning_path_id=path.id,
            title=entry.get("title", ""),
            description=entry.get("description", ""),
            order=entry.get("order", idx),
            estimated_hours=entry.get("estimated_hours", 0),
            resources=entry.get("resources", "[]"),
            completed=bool(entry.get("completed", False)),
            completed_at=completed_at,
            difficulty_feedback=entry.get("difficulty_feedback"),
        ))
    db.flush()
    db.refresh(path)

    # Recompute total_xp from the restored set (streak/last_active_date
    # left as-is; restoring shouldn't fake activity).
    path.total_xp = 10 * sum(1 for m in path.milestones if m.completed)

    # Append the restore as its own revision so history is append-only.
    snapshot_revision(path, "restore", db)
    db.commit()
    db.refresh(path)
    return _build_path_response(path)


# AP6 — discovery / explore public paths grouped by category
@router.get("/explore")
async def explore_public_paths(db: Session = Depends(get_db)):
    """Return all public learning paths grouped by category.

    Shape: {"Programming": [path1, path2], "Design": [...], ...}
    """
    public_paths = (
        db.query(LearningPath)
        .filter(LearningPath.is_public == True)  # noqa: E712
        .order_by(LearningPath.created_at.desc())
        .all()
    )

    grouped: dict = {}
    for p in public_paths:
        cat = (getattr(p, "category", None) or "Other").strip() or "Other"
        milestones = sorted(p.milestones, key=lambda m: m.order)
        total_hours = sum((m.estimated_hours or 0) for m in milestones)
        card = {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "experience_level": p.experience_level,
            "time_commitment": p.time_commitment,
            "category": cat,
            "total_xp": p.total_xp or 0,
            "milestone_count": len(milestones),
            "total_hours": total_hours,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        grouped.setdefault(cat, []).append(card)

    return grouped


# ---------------------------------------------------------------------------
# AP12 — Milestone notes / reflections
# ---------------------------------------------------------------------------


class NoteUpsert(BaseModel):
    content: str
    is_private: bool = False


class NoteResponse(BaseModel):
    id: int
    milestone_id: int
    content: str
    is_private: bool
    difficulty_flag: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _note_to_response(n: MilestoneNote) -> NoteResponse:
    return NoteResponse(
        id=n.id, milestone_id=n.milestone_id, content=n.content,
        is_private=n.is_private, difficulty_flag=n.difficulty_flag,
        created_at=n.created_at, updated_at=n.updated_at,
    )


@router.get("/milestones/{milestone_id}/note", response_model=Optional[NoteResponse])
async def get_my_note(
    milestone_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Return the calling user's note on this milestone, or null."""
    note = (
        db.query(MilestoneNote)
        .filter(MilestoneNote.milestone_id == milestone_id, MilestoneNote.user_id == current.id)
        .first()
    )
    return _note_to_response(note) if note else None


@router.put("/milestones/{milestone_id}/note", response_model=Optional[NoteResponse])
async def upsert_my_note(
    milestone_id: int,
    payload: NoteUpsert,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Create or update the caller's note on this milestone.

    Empty / whitespace-only `content` deletes the note (saves nothing).
    The keyword-based `difficulty_flag` is recomputed on every save.
    """
    if not db.query(Milestone).filter(Milestone.id == milestone_id).first():
        raise HTTPException(status_code=404, detail="Milestone not found.")

    body = (payload.content or "").strip()
    note = (
        db.query(MilestoneNote)
        .filter(MilestoneNote.milestone_id == milestone_id, MilestoneNote.user_id == current.id)
        .first()
    )

    # Empty submission → wipe any existing note (no-op if none) and return null.
    if not body:
        if note:
            db.delete(note)
            db.commit()
        return None

    flag = parse_difficulty_flag(body)
    if note:
        note.content = body
        note.is_private = payload.is_private
        note.difficulty_flag = flag
    else:
        note = MilestoneNote(
            milestone_id=milestone_id,
            user_id=current.id,
            content=body,
            is_private=payload.is_private,
            difficulty_flag=flag,
        )
        db.add(note)
    db.commit()
    db.refresh(note)
    return _note_to_response(note)


@router.delete("/milestones/{milestone_id}/note", status_code=204)
async def delete_my_note(
    milestone_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    note = (
        db.query(MilestoneNote)
        .filter(MilestoneNote.milestone_id == milestone_id, MilestoneNote.user_id == current.id)
        .first()
    )
    if note:
        db.delete(note)
        db.commit()


@router.get("/paths/{path_id}/notes/public")
async def get_public_notes_for_path(
    path_id: int,
    db: Session = Depends(get_db),
):
    """Public, non-auth endpoint used by SharePathPage.

    Returns `{milestone_id: [{author_email_masked, content, updated_at}, ...]}`
    only for non-private notes on milestones belonging to a *public* path.
    Email is masked (e.g. `m**@example.com`) so the share view has light
    attribution without leaking inboxes.
    """
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path or not path.is_public:
        raise HTTPException(status_code=404, detail="Path not found or not public.")

    rows = (
        db.query(MilestoneNote, User.email)
        .join(Milestone, Milestone.id == MilestoneNote.milestone_id)
        .join(User, User.id == MilestoneNote.user_id)
        .filter(
            Milestone.learning_path_id == path_id,
            MilestoneNote.is_private.is_(False),
        )
        .order_by(MilestoneNote.updated_at.desc())
        .all()
    )

    def _mask(email: str) -> str:
        local, _, domain = email.partition("@")
        if not domain:
            return "anon"
        if len(local) <= 1:
            return f"{local}*@{domain}"
        return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"

    grouped: dict[int, list] = {}
    for note, email in rows:
        grouped.setdefault(note.milestone_id, []).append({
            "content": note.content,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            "author": _mask(email),
        })
    return grouped


# ---------------------------------------------------------------------------
# AP11 — Daily reminder emails: toggle + unsubscribe + manual trigger
# ---------------------------------------------------------------------------


class ReminderToggle(BaseModel):
    reminder_opt_in: bool


@router.patch("/auth/me/reminders", response_model=UserResponse)
async def update_reminder_pref(
    payload: ReminderToggle,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Toggle the daily-reminder opt-in. Defaults are off, so this is the
    only way to start receiving them."""
    current.reminder_opt_in = bool(payload.reminder_opt_in)
    # Toggling fresh-on resets the silent-reminder streak so the user gets
    # at least one nudge before the 90-day cool-off can possibly kick in.
    if payload.reminder_opt_in:
        current.no_activity_reminders_sent = 0
    db.commit()
    db.refresh(current)
    return current


@router.get("/unsubscribe")
async def unsubscribe(token: str, db: Session = Depends(get_db)):
    """One-click unsubscribe link from inside the reminder email. Auth-free
    by design — the signed token IS the auth."""
    user_id = verify_unsubscribe_token(token)
    if not user_id:
        raise HTTPException(status_code=400, detail="This unsubscribe link is invalid.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    user.reminder_opt_in = False
    db.commit()
    return {"ok": True, "message": f"Reminders for {user.email} are turned off."}


@router.post("/jobs/run-reminders")
async def manual_run_reminders(db: Session = Depends(get_db)):
    """Manual trigger — used by the optional APScheduler daily job and as a
    fallback for hosts where the scheduler isn't running. Idempotent per day."""
    summary = send_reminders(db)
    return summary


# ---------------------------------------------------------------------------
# AP8 — Milestone quizzes
# ---------------------------------------------------------------------------


_QUIZ_ERROR_TO_HTTP = {
    "milestone-not-found":         (404, "Milestone not found."),
    "milestone-content-too-short": (422, "Milestone description is too short to generate a meaningful quiz."),
    "regenerate-rate-limited":     (429, "You can regenerate this quiz again tomorrow."),
    "generation-failed":           (502, "Quiz generation failed — please try again in a moment."),
}


def _quiz_to_client_view(quiz: MilestoneQuiz) -> dict:
    """Plan §5: cached JSON travels to the client INCLUDING `correct_index`
    for instant client-side grading. Server still re-grades on submit."""
    return {
        "milestone_id": quiz.milestone_id,
        "questions": _json_for_quiz.loads(quiz.questions_json),
        "generated_at": quiz.updated_at.isoformat() if quiz.updated_at else None,
    }


@router.get("/milestones/{milestone_id}/quiz")
async def get_milestone_quiz(
    milestone_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Cached quiz; generates via Claude on miss. Generation rate-limited 1/hr."""
    quiz, err = get_or_generate_quiz(milestone_id, db, force=False)
    if err:
        status, msg = _QUIZ_ERROR_TO_HTTP.get(err, (500, err))
        raise HTTPException(status_code=status, detail=msg)
    return _quiz_to_client_view(quiz)


@router.post("/milestones/{milestone_id}/quiz/regenerate")
async def regenerate_milestone_quiz(
    milestone_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Force a fresh Claude generation. Gated to once per day per milestone."""
    quiz, err = get_or_generate_quiz(milestone_id, db, force=True)
    if err:
        status, msg = _QUIZ_ERROR_TO_HTTP.get(err, (500, err))
        raise HTTPException(status_code=status, detail=msg)
    return _quiz_to_client_view(quiz)


class QuizSubmission(BaseModel):
    answers: list[int]


@router.post("/milestones/{milestone_id}/quiz/attempt")
async def submit_quiz_attempt(
    milestone_id: int,
    payload: QuizSubmission,
    db: Session = Depends(get_db),
    current: User = Depends(require_user),
):
    """Server-authoritative grade. On pass (≥ PASS_THRESHOLD), marks the
    milestone complete and fires the AP4 XP/streak update — same logic as
    the direct PATCH /milestones/{id} path so quiz-gated and direct flows
    agree on totals."""
    quiz = (
        db.query(MilestoneQuiz)
        .filter(MilestoneQuiz.milestone_id == milestone_id)
        .first()
    )
    if not quiz:
        raise HTTPException(status_code=404, detail="No quiz exists for this milestone — generate one first.")

    grade = grade_attempt(quiz, payload.answers)

    db.add(QuizAttempt(
        user_id=current.id,
        milestone_id=milestone_id,
        score=grade["score"],
        passed=grade["passed"],
    ))

    response: dict = {**grade, "milestone_completed": False, "total_xp": None, "streak_days": None}

    if grade["passed"]:
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if milestone and not milestone.completed:
            total_xp, streak_days = complete_milestone(milestone, True, db)
            # AP23: keep tasks coherent (same rule as the manual PATCH path).
            if milestone.tasks:
                now = datetime.utcnow()
                for t in milestone.tasks:
                    t.completed = True
                    t.completed_at = now
            response["total_xp"] = total_xp
            response["streak_days"] = streak_days
            response["milestone_completed"] = True

    db.commit()
    return response

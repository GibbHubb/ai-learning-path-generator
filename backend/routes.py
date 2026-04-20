import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

from database import get_db
from models import LearningPath, Milestone
from ai_service import generate_learning_path, stream_learning_path, enrich_milestone_resources

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

class MilestoneResponse(BaseModel):
    id: int
    title: str
    description: str
    order: int
    estimated_hours: float
    resources: List[str]
    completed: bool
    completed_at: Optional[datetime]

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
    created_at: datetime
    milestones: List[MilestoneResponse]

    class Config:
        from_attributes = True

class MilestoneUpdate(BaseModel):
    completed: bool

class ShareUpdate(BaseModel):
    is_public: bool = True


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
    )


def _build_path_response(path: LearningPath) -> LearningPathResponse:
    return LearningPathResponse(
        id=path.id, title=path.title, description=path.description,
        experience_level=path.experience_level, time_commitment=path.time_commitment,
        is_public=path.is_public if path.is_public is not None else False,
        total_xp=path.total_xp if path.total_xp is not None else 0,
        streak_days=path.streak_days if path.streak_days is not None else 0,
        created_at=path.created_at,
        milestones=sorted([_build_milestone_response(m) for m in path.milestones], key=lambda x: x.order),
    )


def _enqueue_enrichment(background_tasks: BackgroundTasks, milestones, goal: str):
    """Fire background enrichment for each milestone."""
    for m in milestones:
        background_tasks.add_task(
            enrich_milestone_resources,
            m.id, m.title, m.description, goal,
        )


# API Routes
@router.post("/generate", response_model=LearningPathResponse)
async def create_learning_path_endpoint(
    path_data: LearningPathCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Generate a new learning path using AI"""
    try:
        ai_result = generate_learning_path(
            path_data.goal, path_data.experience_level, path_data.time_commitment
        )

        db_path = LearningPath(
            title=ai_result["path_title"],
            description=ai_result["path_description"],
            experience_level=path_data.experience_level,
            time_commitment=path_data.time_commitment,
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

        # AP3 — background enrichment
        _enqueue_enrichment(background_tasks, created, path_data.goal)

        return _build_path_response(db_path)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating learning path: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/stream")
async def generate_stream(
    path_data: LearningPathCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Stream a new learning path via Server-Sent Events."""

    def event_generator():
        try:
            milestones_data = list(stream_learning_path(
                path_data.goal, path_data.experience_level, path_data.time_commitment,
            ))

            from ai_service import generate_learning_path as _glp
            ai_result = _glp(path_data.goal, path_data.experience_level, path_data.time_commitment)

            db_path = LearningPath(
                title=ai_result["path_title"],
                description=ai_result["path_description"],
                experience_level=path_data.experience_level,
                time_commitment=path_data.time_commitment,
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

            # AP3 — background enrichment
            _enqueue_enrichment(background_tasks, created_milestones, path_data.goal)

            full_path = {
                "id": db_path.id,
                "title": db_path.title,
                "description": db_path.description,
                "experience_level": db_path.experience_level,
                "time_commitment": db_path.time_commitment,
                "is_public": False,
                "total_xp": 0,
                "streak_days": 0,
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
async def get_all_paths(db: Session = Depends(get_db)):
    """Get all learning paths"""
    paths = db.query(LearningPath).order_by(LearningPath.created_at.desc()).all()
    return [_build_path_response(p) for p in paths]


@router.get("/paths/{path_id}", response_model=LearningPathResponse)
async def get_path(path_id: int, db: Session = Depends(get_db)):
    """Get a specific learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    return _build_path_response(path)


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


# AP4 — milestone completion with XP + streak
@router.patch("/milestones/{milestone_id}")
async def update_milestone(milestone_id: int, update: MilestoneUpdate, db: Session = Depends(get_db)):
    """Update milestone completion status + recompute XP and streak."""
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")

    milestone.completed = update.completed
    milestone.completed_at = datetime.utcnow() if update.completed else None

    # Recompute XP and streak on the parent path
    path = db.query(LearningPath).filter(LearningPath.id == milestone.learning_path_id).first()
    if path:
        all_milestones = db.query(Milestone).filter(Milestone.learning_path_id == path.id).all()
        path.total_xp = 10 * sum(1 for m in all_milestones if m.completed or (m.id == milestone_id and update.completed))
        # Adjust for the current milestone being toggled (it hasn't been committed yet)
        # Actually the milestone.completed is already set above, so just count from current state
        path.total_xp = 10 * sum(1 for m in all_milestones if m.completed)

        if update.completed:
            today = date.today()
            if path.last_active_date == today - timedelta(days=1):
                path.streak_days = (path.streak_days or 0) + 1
            elif path.last_active_date != today:
                path.streak_days = 1
            path.last_active_date = today

    db.commit()

    return {
        "success": True,
        "milestone_id": milestone_id,
        "completed": milestone.completed,
        "total_xp": path.total_xp if path else 0,
        "streak_days": path.streak_days if path else 0,
    }


@router.delete("/paths/{path_id}")
async def delete_path(path_id: int, db: Session = Depends(get_db)):
    """Delete a learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    db.delete(path)
    db.commit()
    return {"success": True, "message": "Learning path deleted"}

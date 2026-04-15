import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

from database import get_db
from models import LearningPath, Milestone
from ai_service import generate_learning_path, stream_learning_path

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
    created_at: datetime
    milestones: List[MilestoneResponse]
    
    class Config:
        from_attributes = True

class MilestoneUpdate(BaseModel):
    completed: bool

# API Routes
@router.post("/generate", response_model=LearningPathResponse)
async def create_learning_path(path_data: LearningPathCreate, db: Session = Depends(get_db)):
    """Generate a new learning path using AI"""
    try:
        # Generate learning path using AI
        ai_result = generate_learning_path(
            path_data.goal,
            path_data.experience_level,
            path_data.time_commitment
        )
        
        # Create learning path in database
        db_path = LearningPath(
            title=ai_result["path_title"],
            description=ai_result["path_description"],
            experience_level=path_data.experience_level,
            time_commitment=path_data.time_commitment
        )
        db.add(db_path)
        db.flush()
        
        # Create milestones
        for idx, milestone_data in enumerate(ai_result["milestones"]):
            milestone = Milestone(
                learning_path_id=db_path.id,
                title=milestone_data["title"],
                description=milestone_data["description"],
                order=idx,
                estimated_hours=milestone_data["estimated_hours"],
                resources=json.dumps(milestone_data["resources"]),
                completed=False
            )
            db.add(milestone)
        
        db.commit()
        db.refresh(db_path)
        
        # Format response
        response_milestones = []
        for m in db_path.milestones:
            response_milestones.append(MilestoneResponse(
                id=m.id,
                title=m.title,
                description=m.description,
                order=m.order,
                estimated_hours=m.estimated_hours,
                resources=json.loads(m.resources),
                completed=m.completed,
                completed_at=m.completed_at
            ))
        
        return LearningPathResponse(
            id=db_path.id,
            title=db_path.title,
            description=db_path.description,
            experience_level=db_path.experience_level,
            time_commitment=db_path.time_commitment,
            created_at=db_path.created_at,
            milestones=sorted(response_milestones, key=lambda x: x.order)
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating learning path: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/stream")
async def generate_stream(path_data: LearningPathCreate, db: Session = Depends(get_db)):
    """
    Stream a new learning path via Server-Sent Events.

    Milestones are persisted to the database as they are yielded so the
    path is immediately resumable.  The final SSE event is ``event: done``
    carrying the full ``LearningPathResponse`` payload so the client can
    set its local state without an extra round-trip.
    """

    def event_generator():
        try:
            # Collect milestones from the generator (reuses cache if available)
            milestones_data = list(stream_learning_path(
                path_data.goal,
                path_data.experience_level,
                path_data.time_commitment,
            ))

            # We need the path title/description which come from generate_learning_path.
            # stream_learning_path already called it internally so the cache is warm;
            # call it again (instant, cached) to get the envelope fields.
            from ai_service import generate_learning_path as _glp
            ai_result = _glp(path_data.goal, path_data.experience_level, path_data.time_commitment)

            # Persist the LearningPath row
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

                # Yield each persisted milestone (with real DB id) as an SSE event
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

            # Final event: full path so frontend can update its state atomically
            full_path = {
                "id": db_path.id,
                "title": db_path.title,
                "description": db_path.description,
                "experience_level": db_path.experience_level,
                "time_commitment": db_path.time_commitment,
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
    
    result = []
    for path in paths:
        milestones = []
        for m in path.milestones:
            milestones.append(MilestoneResponse(
                id=m.id,
                title=m.title,
                description=m.description,
                order=m.order,
                estimated_hours=m.estimated_hours,
                resources=json.loads(m.resources),
                completed=m.completed,
                completed_at=m.completed_at
            ))
        
        result.append(LearningPathResponse(
            id=path.id,
            title=path.title,
            description=path.description,
            experience_level=path.experience_level,
            time_commitment=path.time_commitment,
            created_at=path.created_at,
            milestones=sorted(milestones, key=lambda x: x.order)
        ))
    
    return result

@router.get("/paths/{path_id}", response_model=LearningPathResponse)
async def get_path(path_id: int, db: Session = Depends(get_db)):
    """Get a specific learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    
    milestones = []
    for m in path.milestones:
        milestones.append(MilestoneResponse(
            id=m.id,
            title=m.title,
            description=m.description,
            order=m.order,
            estimated_hours=m.estimated_hours,
            resources=json.loads(m.resources),
            completed=m.completed,
            completed_at=m.completed_at
        ))
    
    return LearningPathResponse(
        id=path.id,
        title=path.title,
        description=path.description,
        experience_level=path.experience_level,
        time_commitment=path.time_commitment,
        created_at=path.created_at,
        milestones=sorted(milestones, key=lambda x: x.order)
    )

@router.patch("/milestones/{milestone_id}")
async def update_milestone(milestone_id: int, update: MilestoneUpdate, db: Session = Depends(get_db)):
    """Update milestone completion status"""
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    
    milestone.completed = update.completed
    milestone.completed_at = datetime.utcnow() if update.completed else None
    
    db.commit()
    
    return {"success": True, "milestone_id": milestone_id, "completed": milestone.completed}

@router.delete("/paths/{path_id}")
async def delete_path(path_id: int, db: Session = Depends(get_db)):
    """Delete a learning path"""
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")
    
    db.delete(path)
    db.commit()
    
    return {"success": True, "message": "Learning path deleted"}

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class LearningPath(Base):
    __tablename__ = "learning_paths"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    experience_level = Column(String)  # beginner, intermediate, advanced
    time_commitment = Column(String)  # e.g., "10 hours/week"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    milestones = relationship("Milestone", back_populates="learning_path", cascade="all, delete-orphan")

class Milestone(Base):
    __tablename__ = "milestones"
    
    id = Column(Integer, primary_key=True, index=True)
    learning_path_id = Column(Integer, ForeignKey("learning_paths.id"))
    title = Column(String)
    description = Column(Text)
    order = Column(Integer)  # sequence in the learning path
    estimated_hours = Column(Float)
    resources = Column(Text)  # JSON string of recommended resources
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    
    learning_path = relationship("LearningPath", back_populates="milestones")

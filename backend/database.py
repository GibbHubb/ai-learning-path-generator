from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./learning_paths.db")

# AP31 — `check_same_thread` is a SQLite-only argument. Passing it to psycopg2
# raises on connect, so the app could not talk to Postgres at all while this was
# unconditional. Local dev keeps SQLite (and needs the flag, because FastAPI
# serves requests from a threadpool); a deployed instance gets DATABASE_URL.
is_sqlite = DATABASE_URL.startswith("sqlite")
_is_sqlite = is_sqlite  # kept for readability below
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# On serverless the process is recycled constantly and each instance holds its
# own pool, so a default-sized pool per instance multiplies into connection
# exhaustion against one Postgres. The Supabase pooler is the real broker.
_engine_kwargs = {} if _is_sqlite else {"pool_size": 1, "max_overflow": 0, "pool_pre_ping": True}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

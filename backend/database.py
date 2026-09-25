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


_isolated_engine = None


def isolated_session():
    """A session on its own NullPool engine: one connection, opened and closed
    per use, instead of drawing from the app's shared pool.

    🔴 On a non-SQLite deployment `engine` above is capped at `pool_size=1,
    max_overflow=0`. A caller running INSIDE a request handler that already
    holds that one connection (an open `Depends(get_db)` transaction) would
    deadlock waiting for a second connection from the same pool — the wait
    times out silently in production and the caller's write never happens.
    usage.py hit exactly this (see its `_recorder_session`, AP36); this is
    the same fix, extracted so a second caller (ai_service.py's generation
    cache, AP37) does not have to reinvent it.

    SQLite gets no pool cap, so this returns a plain `SessionLocal()` there —
    a second engine bound to the same file would fight the test suite's
    per-test drop/create.
    """
    global _isolated_engine
    if is_sqlite:
        return SessionLocal()
    if _isolated_engine is None:
        # Local imports (mirroring usage.py's `_recorder_session`, on purpose):
        # keeps this engine's construction easy to intercept in a test via
        # `monkeypatch.setattr("sqlalchemy.create_engine", ...)`, the same
        # technique test_usage_ap36.py already uses for the identical pattern.
        from sqlalchemy import create_engine as _create_engine
        from sqlalchemy.orm import sessionmaker as _sessionmaker
        from sqlalchemy.pool import NullPool
        _isolated_engine = _sessionmaker(
            bind=_create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True))
    return _isolated_engine()

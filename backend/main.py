import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import router
import uvicorn

# Configure structured logging before anything else
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# Create database tables
Base.metadata.create_all(bind=engine)

# Migrate existing SQLite tables (add columns that don't exist yet)
from sqlalchemy import inspect, text
with engine.connect() as conn:
    cols = {c["name"] for c in inspect(engine).get_columns("learning_paths")}
    if "is_public" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT 0"))
    if "total_xp" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN total_xp INTEGER NOT NULL DEFAULT 0"))
    if "streak_days" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN streak_days INTEGER NOT NULL DEFAULT 0"))
    if "last_active_date" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN last_active_date DATE"))
    # AP6 — category column on learning_paths
    if "category" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN category VARCHAR"))
    # AP9 — ownership columns on learning_paths
    if "user_id" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN user_id INTEGER"))
    if "anon_session_id" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN anon_session_id VARCHAR"))
    # AP10 — fork lineage
    if "forked_from_id" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN forked_from_id INTEGER"))
    if "original_author_id" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN original_author_id INTEGER"))
    if "fork_count" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN fork_count INTEGER NOT NULL DEFAULT 0"))
    # AP27 — content language (en default for legacy rows)
    if "language" not in cols:
        conn.execute(text("ALTER TABLE learning_paths ADD COLUMN language VARCHAR NOT NULL DEFAULT 'en'"))

    # AP11 — reminder opt-in + bookkeeping on users (the table exists since AP9)
    if inspect(engine).has_table("users"):
        user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
        if "reminder_opt_in" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN reminder_opt_in BOOLEAN NOT NULL DEFAULT 0"))
        if "reminder_sent_at" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN reminder_sent_at DATETIME"))
        if "no_activity_reminders_sent" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN no_activity_reminders_sent INTEGER NOT NULL DEFAULT 0"))
        # AP30 — opt-in public profile flag
        if "is_public_profile" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_public_profile BOOLEAN NOT NULL DEFAULT 0"))

    milestone_cols = {c["name"] for c in inspect(engine).get_columns("milestones")}
    # AP5 — difficulty_feedback column on milestones
    if "difficulty_feedback" not in milestone_cols:
        conn.execute(text("ALTER TABLE milestones ADD COLUMN difficulty_feedback VARCHAR"))
    conn.commit()

# Initialize FastAPI app
app = FastAPI(
    title="AI Learning Path Generator",
    description="Generate personalized learning paths using AI",
    version="1.0.0"
)

# Configure CORS
import os as _os
_cors_origins = _os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,  # AP9 — required so the session cookie crosses origins
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api", tags=["learning-paths"])

# ---------------------------------------------------------------------------
# AP30-fu1 — per-user Open Graph unfurl for public profiles
#
# These are mounted on `app` at the TOP level (not under /api) so the path
# mirrors the SPA's own /u/:id route. A crawler hitting this origin reads
# per-user meta; a human is bounced straight to the SPA. The API router stays
# under /api, so there is no path clash with /, /health or /api/u/{id}/stats.
# ---------------------------------------------------------------------------
# NB: Request/HTTPException/Depends are imported explicitly here rather than
# relied on from the top of the file — the module only imports `FastAPI` up
# there, and the rate-limiting block that pulls in Request/HTTPException sits
# BELOW this point. A signature annotation is evaluated at def time, so
# leaning on the later import would raise NameError at startup.
from fastapi import (  # noqa: E402
    Depends as _Depends,
    HTTPException as _HTTPException,
    Request as _Request,
)
from fastapi.responses import HTMLResponse, Response as _Response  # noqa: E402
from sqlalchemy.orm import Session as _Session  # noqa: E402

from og import (  # noqa: E402
    build_profile_meta_html,
    public_display_name,
    render_profile_card_png,
)
from routes import _compute_user_stats  # noqa: E402
from database import get_db  # noqa: E402
from models import User  # noqa: E402

# Absolute URLs are mandatory for og:image — crawlers reject relative ones.
# Falls back to the request's own base_url so local dev works with no config.
_PUBLIC_BACKEND_URL = _os.getenv("PUBLIC_BACKEND_URL", "").rstrip("/")
_FRONTEND_ORIGIN = _os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")


def _backend_base(request: _Request) -> str:
    return _PUBLIC_BACKEND_URL or str(request.base_url).rstrip("/")


def _public_user_or_404(user_id: int, db: _Session) -> User:
    """Load an opted-in public user, else 404.

    Missing and private are deliberately indistinguishable — same rule as
    AP30's /api/u/{id}/stats, so a private profile never leaks its existence.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_public_profile:
        raise _HTTPException(status_code=404, detail="Profile not found")
    return user


@app.get("/u/{user_id}", response_class=HTMLResponse, include_in_schema=False)
async def public_profile_unfurl(user_id: int, request: _Request,
                                db: _Session = _Depends(get_db)):
    """Crawler-facing HTML shell with per-user OG meta; humans get redirected."""
    user = _public_user_or_404(user_id, db)
    stats = _compute_user_stats(user, db)
    base = _backend_base(request)
    return HTMLResponse(build_profile_meta_html(
        display_name=public_display_name(user.id),
        stats=stats,
        card_url=f"{base}/u/{user.id}/card.png",
        spa_url=f"{_FRONTEND_ORIGIN}/u/{user.id}",
        canonical_url=f"{base}/u/{user.id}",
    ))


@app.get("/u/{user_id}/card.png", include_in_schema=False)
async def public_profile_card(user_id: int, db: _Session = _Depends(get_db)):
    """The 1200x630 OG image, generated per request (cached by the CDN/UA)."""
    user = _public_user_or_404(user_id, db)
    stats = _compute_user_stats(user, db)
    png = render_profile_card_png(public_display_name(user.id), stats)
    return _Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )

# Rate Limiting Middleware
from fastapi import Request, HTTPException
import time

RATE_LIMIT = 5  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds
request_counts = {}

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only limit generation endpoints (sync and streaming)
    if request.url.path in ("/api/generate", "/api/generate/stream") and request.method == "POST":
        client_ip = request.client.host
        current_time = time.time()
        
        # Clean up old entries
        if client_ip in request_counts:
            requests = [t for t in request_counts[client_ip] if current_time - t < RATE_LIMIT_WINDOW]
            request_counts[client_ip] = requests
        else:
            request_counts[client_ip] = []
            
        if len(request_counts[client_ip]) >= RATE_LIMIT:
            # Return JSON response for 429
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."}
            )
            
        request_counts[client_ip].append(current_time)
        
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return {
        "message": "AI Learning Path Generator API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

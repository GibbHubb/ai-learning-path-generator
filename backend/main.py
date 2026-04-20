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
    conn.commit()

# Initialize FastAPI app
app = FastAPI(
    title="AI Learning Path Generator",
    description="Generate personalized learning paths using AI",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api", tags=["learning-paths"])

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

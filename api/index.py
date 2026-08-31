"""AP31 — Vercel serverless entry point for the FastAPI backend.

Vercel's Python runtime looks for a module-level ASGI/WSGI callable named
`app`, so this re-exports the one `backend/main.py` already builds. Nothing is
constructed here: importing `main` is what runs `create_all` and wires the
routes, exactly as `uvicorn main:app` does locally.

`backend/` is put on the path because that package imports its siblings by bare
name (`from models import ...`, `from database import ...`), which only
resolves when `backend` is itself a source root. Rewriting those imports would
touch every file in the app for no gain.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from main import app  # noqa: E402  — the path insert above has to happen first

# Vercel looks for this name.
__all__ = ["app"]

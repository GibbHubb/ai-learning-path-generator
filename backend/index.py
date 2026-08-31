"""AP31 — Vercel serverless entry point.

Deliberately inside `backend/` rather than in a top-level `api/`. Vercel's
Python builder imports the entry point as a module from its OWN directory, so
sitting here is what makes `main`'s sibling imports (`from models import …`,
`from database import …`) resolve — the same way `uvicorn main:app` resolves
them locally.

The first attempt put this in `api/index.py` and inserted `../backend` onto
`sys.path` at runtime. The build went green and every request returned
FUNCTION_INVOCATION_FAILED: a path manipulation that happens at runtime is
invisible to the dependency tracer, so the bundle shipped the entry point and
none of the application it imports.

Importing `main` is what runs `create_all` and wires the routes; nothing is
constructed here.
"""
from main import app

__all__ = ["app"]

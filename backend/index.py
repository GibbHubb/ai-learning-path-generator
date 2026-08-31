"""AP31 — Vercel serverless entry point.

Deliberately inside `backend/` rather than in a top-level `api/`. Vercel's
Python builder imports the entry point as a module from its OWN directory, so
sitting here is what makes `main`'s sibling imports (`from models import …`,
`from database import …`) resolve — the same way `uvicorn main:app` resolves
them locally. An earlier attempt lived in `api/` and pushed `../backend` onto
`sys.path` at runtime; that is invisible to the dependency tracer.

⚠️ Two things about the shape of this file are load-bearing, both learned the
hard way:

1. **`app` must be assigned at the TOP LEVEL.** The builder looks for the
   symbol *statically*, before anything runs — a version of this file that
   defined `app` only inside a `try:` failed the BUILD outright with
   `Could not find a top-level "app", "application", or "handler"`. So the
   fallback is defined first, unconditionally, and the real app replaces it.

2. **The import is guarded**, because a module-level exception on a serverless
   function surfaces as `FUNCTION_INVOCATION_FAILED` with no detail, and this
   project has no runtime log retention to look it up in. The guard turns an
   opaque platform error into a 500 that says what happened. It hides nothing:
   every route still fails, loudly, with the traceback attached.
"""
import os
import sys
import traceback

# The function runs with cwd=/var/task while this file sits in
# /var/task/backend, so `main` is NOT importable by default — the observed
# failure was a plain `ModuleNotFoundError: No module named 'main'`. Vercel
# imports the entry point by path rather than as a package, so put its own
# directory on the path explicitly. `includeFiles: backend/**` in vercel.json
# is the other half: the tracer cannot follow this, so without it the siblings
# are never uploaded at all.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_IMPORT_ERROR: str | None = None


async def app(scope, receive, send):  # replaced below on a successful import
    """Minimal ASGI app that reports why the real one could not load."""
    if scope["type"] != "http":
        return
    body = ("ai-path failed to start.\n\n" + (_IMPORT_ERROR or "unknown")).encode()
    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})


try:
    from main import app  # type: ignore[assignment]  # noqa: F811 — the real one
except Exception:  # noqa: BLE001 — anything at all, we need to see it
    _IMPORT_ERROR = traceback.format_exc()

__all__ = ["app"]

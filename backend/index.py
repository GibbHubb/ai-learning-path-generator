"""AP31 — Vercel serverless entry point.

Deliberately inside `backend/` rather than in a top-level `api/`. Vercel's
Python builder imports the entry point as a module from its OWN directory, so
sitting here is what makes `main`'s sibling imports (`from models import …`,
`from database import …`) resolve — the same way `uvicorn main:app` resolves
them locally.

An earlier attempt put this in `api/index.py` and inserted `../backend` onto
`sys.path` at runtime. The build went green and every request returned
FUNCTION_INVOCATION_FAILED, because a path manipulation performed at runtime is
invisible to the dependency tracer.

Importing `main` is what runs `create_all` and wires the routes; nothing is
constructed here.

⚠️ The import is guarded. A module-level exception on a serverless function
surfaces as `FUNCTION_INVOCATION_FAILED` with no detail, and this project has
no runtime log retention to look it up in — so a cold-start failure is
completely opaque from the outside. The guard turns that into a 500 that SAYS
what happened. It is not a fallback that hides a problem: every route still
fails, loudly, with the reason attached.
"""
import traceback

try:
    from main import app  # noqa: F401 — re-exported for Vercel
    _IMPORT_ERROR = None
except Exception:  # noqa: BLE001 — anything at all, we need to see it
    _IMPORT_ERROR = traceback.format_exc()

    async def app(scope, receive, send):  # type: ignore[misc]
        """Minimal ASGI app that reports why the real one could not load."""
        if scope["type"] != "http":
            return
        body = (
            "ai-path failed to start.\n\n" + (_IMPORT_ERROR or "unknown")
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": body})

__all__ = ["app"]

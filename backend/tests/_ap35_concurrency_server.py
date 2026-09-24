"""AP35 concurrency test harness.

Launched as a SEPARATE PROCESS by test_rate_limit_concurrency_ap35.py — real
uvicorn, real OS-level concurrency, not TestClient's synchronous single
transport. Stubs the model call (`routes.generate_learning_path`) so
`/api/generate` returns 200 with no real GEMINI key and no network — this
stays inside the free-tier / no-live-dependency rule the rest of the suite
follows. Everything downstream of that stub (the rate limiter itself) is
real: the actual `rate_limit.check_and_record`, the actual SQLite file named
on the command line, the actual middleware wiring in main.py.

Usage: python _ap35_concurrency_server.py <port> <sqlite-db-path> [trusted]
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

port = int(sys.argv[1])
db_path = sys.argv[2]
trusted = len(sys.argv) > 3 and sys.argv[3] == "trusted"

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ.setdefault("GEMINI_API_KEY", "test-not-a-real-key")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.pop("RESEND_API_KEY", None)
if trusted:
    os.environ["TRUSTED_PROXY_HEADER"] = "1"
else:
    os.environ.pop("TRUSTED_PROXY_HEADER", None)

import routes  # noqa: E402
from schemas import GeneratedPath  # noqa: E402


def _fake_generate(goal, experience_level, time_commitment, language="en"):
    # AP33 — real generate_learning_path now returns a validated GeneratedPath (and
    # requires >=1 milestone), so the stub must too, or routes.py's attribute access
    # (ai_result.path_title, etc.) below would blow up on a raw dict.
    return GeneratedPath(
        path_title="Concurrency Test",
        path_description="stubbed for AP35's concurrency test — no real AI call",
        category="Other",
        milestones=[
            {"title": "m1", "description": "stub milestone", "estimated_hours": 1.0, "resources": []},
        ],
    )


routes.generate_learning_path = _fake_generate

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # forwarded_allow_ips="" — no XFF header is sent by this test anyway, but
    # explicit is cheaper than debugging uvicorn's own proxy-trust default
    # later if that ever changes.
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="warning",
                forwarded_allow_ips="")

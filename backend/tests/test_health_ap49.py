"""AP49 — /health's 503 says the database is down, and nothing else.

It used to return `str(exc)[:200]` in the body: on a real outage that is the
psycopg2 message, e.g. 'connection to server at "db.<ref>.supabase.co"
(1.2.3.4), port 6543 failed: ...' — the database host, port and driver, on a
public, unauthenticated route. The detail belongs in the server log.
"""
import logging
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from main import app

# A realistic driver failure, carrying exactly what must not leak.
_DRIVER_ERROR = OperationalError(
    "SELECT 1", {},
    Exception('connection to server at "db.secretref.supabase.co" (10.20.30.40), '
              'port 6543 failed: FATAL: password authentication failed for user "postgres"'),
)
_LEAKS = ("supabase", "secretref", "10.20.30.40", "6543", "psycopg", "OperationalError",
          "password", "postgres", "connection to server")


def _dead_health():
    with patch("main.engine.connect", side_effect=_DRIVER_ERROR):
        return TestClient(app).get("/health")


def test_503_body_is_exactly_the_generic_shape():
    r = _dead_health()
    assert r.status_code == 503
    assert r.json() == {"status": "unhealthy", "db": "down"}


def test_503_body_carries_no_host_port_or_driver_text():
    body = _dead_health().text
    leaked = [s for s in _LEAKS if s.lower() in body.lower()]
    assert leaked == [], f"/health leaked {leaked}: {body}"


def test_the_driver_error_is_logged_server_side(caplog):
    with caplog.at_level(logging.ERROR):
        _dead_health()
    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "db.secretref.supabase.co" in logged, "the detail must still reach the server log"


def test_healthy_body_unchanged():
    """Control: the up path is untouched."""
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "db": "up"}

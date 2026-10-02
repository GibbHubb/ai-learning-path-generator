"""AP-testfu1 — shared test bootstrap.

Why this file exists
--------------------
Each test module used to set ``os.environ["DATABASE_URL"]`` at import time to
its own SQLite file (test_ap8.db, test_ap9.db, ...). But ``database.engine`` is
created exactly once, at the first import of ``database`` — so whichever test
module pytest imported *first* silently won, and every later module's fixtures
reset a database its TestClient was never bound to.

The visible symptom was ``test_auth.py::test_anonymous_path_claimed_on_verify``
failing in a full-suite run while passing in isolation: its ``reset_db``
fixture truncated test_ap9.db, but the app under test was still talking to
whichever DB had been bound first.

pytest imports ``conftest.py`` before collecting any test module, so pinning
the env var here happens strictly before ``database`` is first imported by
anyone. One engine, one database, module order irrelevant.

Modules keep their own ``reset_db`` fixtures where they do extra work (e.g.
clearing the auth rate-limit bucket); the autouse fixture below is what covers
the modules that never had one (test_api.py, test_enrichment_lang.py).
"""
import os
import sys

# Make the backend package importable regardless of pytest's rootdir/cwd.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# One database for the whole suite. Must be set before `database` is imported.
TEST_DB_PATH = os.path.join(BACKEND_DIR, "test_suite.db")

# AP41 — the suite can run against Postgres, the engine production runs.
#
# It reads TEST_DATABASE_URL, deliberately NOT DATABASE_URL: the autouse
# fixture below drops every table before every test, so a suite that picked up
# an exported DATABASE_URL (the production Supabase URL, say) would wipe it.
# A separate variable means only an explicit choice reaches a server — and the
# host guard means even an explicit choice can only reach this machine.
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _assert_local(url: str) -> None:
    from urllib.parse import urlsplit

    from urllib.parse import parse_qs

    parts = urlsplit(url)
    host = parts.hostname or ""
    # libpq lets the query string override the host (`?host=`, `?hostaddr=`) or
    # pull it from a service file (`?service=`): `postgresql://localhost/x?host=
    # db.supabase.co` would pass a hostname-only check and then be wiped.
    overrides = {"host", "hostaddr", "service"} & {k.lower() for k in parse_qs(parts.query)}
    if overrides:
        raise RuntimeError(
            f"TEST_DATABASE_URL sets {sorted(overrides)} in its query string, which can "
            "point the connection anywhere. Put the host in the URL itself."
        )
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"TEST_DATABASE_URL points at {host!r}. The suite drops every table "
            "before every test, so it only runs against a local database "
            f"({', '.join(sorted(_LOCAL_HOSTS))})."
        )


if _TEST_DB_URL:
    _assert_local(_TEST_DB_URL)
    os.environ["DATABASE_URL"] = _TEST_DB_URL
else:
    os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.replace(os.sep, '/')}"

# No test may reach the real email provider — force the console-log fallback.
os.environ.pop("RESEND_API_KEY", None)

import pytest  # noqa: E402
from database import Base, engine  # noqa: E402


def peek(fn):
    """Run `fn(session)` on a short-lived session and CLOSE it.

    AP41 — tests used to write `SessionLocal().query(...)` inline. On SQLite the
    leaked session is harmless; on Postgres the app's pool is ONE connection
    (database.py, the production setting), so the leaked session held it and
    the next request the test made waited out the 30s pool timeout.
    """
    from database import SessionLocal

    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_schema():
    """Drop + recreate every table before each test.

    Autouse and suite-wide, so a module without its own reset fixture can no
    longer inherit another module's rows. Modules that additionally define
    ``reset_db`` still run theirs — the operations are idempotent.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _reset_process_state():
    """Clear the in-process buckets that outlive a single test.

    * AP35 — the /api/generate rate limiter used to be an in-process dict
      keyed by ``request.client.host`` (always the literal "testclient" under
      TestClient, so every test module shared one bucket and had to be reset
      here). It is now ``rate_limit_hits``, a database table, so the
      ``_clean_schema`` fixture above (drop + recreate every table before
      each test) already covers it — nothing to clear here any more.

    * ``auth._magic_link_requests`` — the magic-link per-email bucket. Most
      modules already cleared this themselves; doing it here covers the rest.
    """
    import auth as auth_module

    auth_module._magic_link_requests.clear()
    yield


def pytest_sessionfinish(session, exitstatus):
    """Remove the shared SQLite file so a run never leaves state behind."""
    engine.dispose()
    try:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
    except OSError:
        pass  # Windows may still hold the handle; harmless, it gets reused.

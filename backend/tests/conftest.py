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
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.replace(os.sep, '/')}"

# No test may reach the real email provider — force the console-log fallback.
os.environ.pop("RESEND_API_KEY", None)

import pytest  # noqa: E402
from database import Base, engine  # noqa: E402


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

    These are module-level dicts in the app, not database rows, so the schema
    reset above does not touch them:

    * ``main.request_counts`` — the /api/generate rate limiter, keyed by
      ``request.client.host``. Under TestClient that key is always the literal
      "testclient", so *every* test module shares one 5-requests-per-60-seconds
      bucket. ``test_api.py::test_rate_limiting`` deliberately exhausts it to
      assert the 429, and the whole suite finishes well inside the 60s window —
      so any later module that POSTs /api/generate inherited the exhausted
      bucket and got a 429. That is what made
      ``test_auth.py::test_anonymous_path_claimed_on_verify`` fail in a
      full-suite run while passing in isolation.

    * ``auth._magic_link_requests`` — the magic-link per-email bucket. Most
      modules already cleared this themselves; doing it here covers the rest.
    """
    import main as main_module
    import auth as auth_module

    main_module.request_counts.clear()
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

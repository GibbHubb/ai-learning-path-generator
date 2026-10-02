"""AP41 — main.py's startup block, driven for real.

main.py runs `create_all` and then a ladder of hand-written SQLite-dialect
`ALTER TABLE ... ADD COLUMN` statements at IMPORT time, gated on
`database.is_sqlite`. Its own comment names the landmine: on Postgres, the day
a model grows a column the ladder would fire SQLite DDL and take the app down
on startup. Nothing tested that guard.

Each test imports `main` in a FRESH interpreter (the guard runs once, at
import, so an in-process import would only ever see the suite's own engine)
with a cursor listener that records every statement the engine executes.

* Postgres: an OLD schema (a column the ladder knows about is dropped first —
  the exact "model grew a column" situation) must import cleanly with ZERO
  `ALTER TABLE` statements executed.
* SQLite, same old schema: the ladder MUST fire. This is the positive control
  that proves the listener can see an ALTER at all — without it, "zero ALTERs
  on Postgres" could just mean the listener was never attached.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The probe: build the schema, drop one column the ladder back-patches, then
# import main with a listener on and report every statement it executed.
_PROBE = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, {backend!r})
    os.environ["DATABASE_URL"] = {url!r}
    os.environ.setdefault("ENVIRONMENT", "development")
    from sqlalchemy import event, text
    import database
    import models  # noqa: F401 — registers every table on Base
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE learning_paths DROP COLUMN language"))
    seen = []
    @event.listens_for(database.engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)
    import main  # noqa: F401 — the startup block runs here
    print("AP41-PROBE " + json.dumps({{"is_sqlite": database.is_sqlite,
                                       "statements": seen}}))
""")


def _run_probe(url: str) -> dict:
    code = _PROBE.format(backend=BACKEND_DIR, url=url)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"importing main failed:\n{proc.stdout}\n{proc.stderr}"
    line = next(l for l in proc.stdout.splitlines() if l.startswith("AP41-PROBE "))
    return json.loads(line[len("AP41-PROBE "):])


def _alters(statements):
    return [s for s in statements if s.lstrip().upper().startswith("ALTER TABLE")]


_PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@pytest.mark.skipif(not _PG_URL.startswith("postgres"),
                    reason="needs a Postgres TEST_DATABASE_URL — runs in CI's postgres leg")
def test_postgres_startup_executes_no_alter_even_when_a_column_is_missing():
    result = _run_probe(_PG_URL)
    assert result["is_sqlite"] is False
    assert _alters(result["statements"]) == [], \
        "the SQLite ALTER ladder ran against Postgres"
    # create_all did run (it inspected the existing tables) — the import was
    # not a no-op that trivially executed nothing.
    assert result["statements"], "no statements at all — the listener saw nothing"


def test_sqlite_startup_backpatches_the_missing_column(tmp_path):
    """Positive control: on SQLite the ladder fires for the same old schema."""
    db = tmp_path / "ap41_startup.db"
    result = _run_probe(f"sqlite:///{db.as_posix()}")
    assert result["is_sqlite"] is True
    alters = _alters(result["statements"])
    assert any("ADD COLUMN language" in a for a in alters), alters


# ── the conftest guard that keeps the suite off real databases ───────────────

@pytest.mark.parametrize("url", [
    "postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres",
    "postgresql://u:p@localhost:5432/ci?host=db.abcdefgh.supabase.co",
    "postgresql://u:p@127.0.0.1/ci?hostaddr=10.0.0.5",
    "postgresql://u:p@localhost/ci?service=prod",
])
def test_the_test_db_guard_refuses_anything_but_a_local_host(url):
    import conftest

    with pytest.raises(RuntimeError):
        conftest._assert_local(url)


def test_the_test_db_guard_accepts_a_local_url():
    """Control: the guard is not simply refusing everything."""
    import conftest

    conftest._assert_local("postgresql://postgres:ci@localhost:5432/ci")
    conftest._assert_local("postgresql://postgres:ci@127.0.0.1:55441/ap41")

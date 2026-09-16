"""AP38 — the hardcoded signing-secret fallback, the non-Secure cookie, and the
.env.example drift that hid both.

The drift test is the one that makes this stick: a corrected .env.example decays, but a
test that derives the variable list from the source cannot. This is the
"enforce it, don't remember it" rule applied to configuration.
"""
import logging
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

import config  # noqa: E402
from database import Base, engine  # noqa: E402
import auth as auth_module  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    auth_module._magic_link_requests.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def captured_tokens(monkeypatch):
    tokens = []
    real = auth_module.secrets.token_urlsafe

    def fake(n=32):
        t = real(n)
        tokens.append(t)
        return t

    monkeypatch.setattr(auth_module.secrets, "token_urlsafe", fake)
    return tokens


# ── (a) the signing key no longer falls back to a public constant ──────────────
def test_secret_raises_in_production_when_unset(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    import reminders
    with pytest.raises(config.ConfigError):
        reminders._secret()


def test_secret_dev_default_is_random_not_the_public_constant(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    import reminders
    # It still runs on a laptop — but the dev fallback must NOT be the published
    # constant that made tokens forgeable. A per-process random value is stable within
    # the process (so a token minted and verified in one run matches) and long.
    val = reminders._secret()
    assert val != b"dev-secret-change-me"
    assert len(val) >= 32
    assert reminders._secret() == val  # stable within the process


def test_the_literal_dev_secret_is_gone_from_backend():
    """No file under backend/ may embed the fallback string any more — grepping the
    tree, not trusting the one file we edited."""
    hits = []
    for root, _dirs, files in os.walk(BACKEND):
        if "__pycache__" in root or "/tests" in root.replace(os.sep, "/"):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            text = open(path, encoding="utf-8", errors="replace").read()
            # the assignment/fallback, not a comment describing it
            for line in text.splitlines():
                if "dev-secret-change-me" in line and not line.lstrip().startswith("#"):
                    hits.append(f"{path}: {line.strip()}")
    assert hits == [], "the public fallback constant is still live:\n" + "\n".join(hits)


# ── (a2) startup logs ONE warning naming every unset required variable ─────────
def test_startup_warning_names_every_unset_required_variable(caplog, monkeypatch):
    """config.warn_unset_required() is what main.py calls at import time
    (main.py:91) — the single up-front line a deployer actually reads. Remove
    SECRET_KEY and the warning must name it."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    with caplog.at_level(logging.WARNING, logger="config"):
        config.warn_unset_required()
    warnings = [r.getMessage() for r in caplog.records if r.name == "config"]
    assert len(warnings) == 1, warnings  # a SINGLE warning, not one per variable
    assert "SECRET_KEY" in warnings[0], warnings[0]


def test_startup_warning_says_nothing_when_everything_is_set(caplog, monkeypatch):
    """Positive control for the test above: with every REQUIRED_IN_PRODUCTION
    variable set, warn_unset_required() must log nothing — otherwise the prior
    test's green could just mean it warns unconditionally, proving nothing about
    SECRET_KEY specifically."""
    for name in config.REQUIRED_IN_PRODUCTION:
        monkeypatch.setenv(name, "x")
    with caplog.at_level(logging.WARNING, logger="config"):
        config.warn_unset_required()
    assert [r.getMessage() for r in caplog.records if r.name == "config"] == []


# ── (b) the session cookie is Secure by default ────────────────────────────────
def _sign_in_headers(client, captured_tokens):
    client.post("/api/auth/request-link", json={"email": "cookie@example.com"})
    token = captured_tokens[-1]
    res = client.post("/api/auth/verify", json={"token": token})
    assert res.status_code == 200, res.text
    return res.headers.get_list("set-cookie")


def test_session_cookie_is_secure_in_production_by_default(client, captured_tokens, monkeypatch):
    # No cookie var set: in production the cookie MUST be Secure (the fix). In
    # development it must NOT be (so local http + the test client work) — the two
    # assertions together are what makes the default correct rather than blanket.
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    cookies = _sign_in_headers(client, captured_tokens)
    session = [c for c in cookies if c.startswith(auth_module.SESSION_COOKIE + "=")]
    assert session, "no session cookie was set"
    assert "Secure" in session[0], session[0]


def test_session_cookie_is_not_secure_in_development_by_default(client, captured_tokens, monkeypatch):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    cookies = _sign_in_headers(client, captured_tokens)
    session = [c for c in cookies if c.startswith(auth_module.SESSION_COOKIE + "=")]
    assert session
    assert "Secure" not in session[0], session[0]


def test_session_cookie_secure_can_be_disabled_for_local_http(client, captured_tokens, monkeypatch):
    monkeypatch.setenv("APP_COOKIE_SECURE", "0")
    cookies = _sign_in_headers(client, captured_tokens)
    session = [c for c in cookies if c.startswith(auth_module.SESSION_COOKIE + "=")]
    assert session
    assert "Secure" not in session[0], session[0]


# ── config.require / bool_flag behaviour ───────────────────────────────────────
def test_require_raises_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ZZ_MISSING", raising=False)
    with pytest.raises(config.ConfigError):
        config.require("ZZ_MISSING", dev_default="x")


def test_bool_flag_default_and_overrides(monkeypatch):
    monkeypatch.delenv("ZZ_FLAG", raising=False)
    assert config.bool_flag("ZZ_FLAG", default=True) is True
    assert config.bool_flag("ZZ_FLAG", default=False) is False
    for off in ("0", "false", "no", "off", "FALSE"):
        monkeypatch.setenv("ZZ_FLAG", off)
        assert config.bool_flag("ZZ_FLAG", default=True) is False
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("ZZ_FLAG", on)
        assert config.bool_flag("ZZ_FLAG", default=False) is True


# ── (c) the drift test — .env.example must document what the code reads ─────────
def _env_example_names():
    text = open(os.path.join(REPO, ".env.example"), encoding="utf-8").read()
    return {m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]+)=", text, re.M)}


def _backend_env_reads():
    names = set()
    pat = re.compile(
        r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"'
        r'|os\.environ\.get\(\s*"([A-Z][A-Z0-9_]+)"'
        r'|os\.environ\[\s*"([A-Z][A-Z0-9_]+)"\]'
        r'|config\.(?:require|optional|bool_flag)\(\s*"([A-Z][A-Z0-9_]+)"')
    for root, _dirs, files in os.walk(BACKEND):
        if "__pycache__" in root or "/tests" in root.replace(os.sep, "/"):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            for m in pat.finditer(text):
                names.add(next(g for g in m.groups() if g))
    return names


def _frontend_vite_reads():
    names = set()
    src = os.path.join(REPO, "frontend", "src")
    if not os.path.isdir(src):
        return names
    for root, _dirs, files in os.walk(src):
        for f in files:
            if not f.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            for m in re.finditer(r"import\.meta\.env\.(VITE_[A-Z0-9_]+)", text):
                names.add(m.group(1))
    return names


def test_env_example_covers_every_variable_the_code_reads():
    documented = _env_example_names()
    read = _backend_env_reads() | _frontend_vite_reads()
    missing = sorted(read - documented)
    assert not missing, (
        ".env.example does not document variable(s) the code reads: "
        + ", ".join(missing))


def test_the_drift_test_actually_sees_the_reads():
    """Positive control: the enumerator must find the variables we KNOW are read, or a
    green on the test above would prove nothing (it would pass on an empty set)."""
    read = _backend_env_reads()
    for expected in ("SECRET_KEY", "OPENAI_API_KEY", "CORS_ORIGINS", "APP_COOKIE_SECURE"):
        assert expected in read, f"the enumerator failed to find {expected}"
    assert "VITE_API_BASE" in _frontend_vite_reads()


def test_the_dead_vite_url_name_is_gone():
    """VITE_API_URL was documented and read by nothing; VITE_API_BASE is what the code
    uses. The dead name must not be back."""
    assert "VITE_API_URL" not in _env_example_names()
    assert "VITE_API_BASE" in _env_example_names()


# ── (e) CORS_ORIGINS required in production — criterion 6 ──────────────────────
def test_cors_dev_default_is_localhost_and_therefore_dev_only():
    """The permissive localhost fallback (main.py:94) is passed as an explicit
    dev_default=, never returned once ENVIRONMENT=production — proven by the
    refuses-to-start test below, which is the half that actually matters."""
    val = config.require(
        "CORS_ORIGINS", dev_default="http://localhost:5173,http://localhost:3000")
    assert "localhost" in val


def test_main_refuses_to_start_in_production_without_cors_origins(monkeypatch):
    """main.py:92 calls config.require("CORS_ORIGINS", ...) at IMPORT time, before
    app.add_middleware — not lazily on first request — so with allow_credentials=
    True (main.py:99) a misconfigured production deploy must fail to LOAD, not
    silently allow the localhost dev origins against a live session cookie.

    Forces a real re-import of main (not just calling a function) so this proves
    the actual module-load path, matching how a fresh Vercel cold start behaves."""
    import importlib

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    # Review fix: popping `main` without restoring it left later tests (and conftest's
    # autouse `import main`) on a FRESH module whose rate-limit dict is not the one the
    # shared `app` uses — the full-suite-only 429 flake conftest exists to prevent.
    # monkeypatch.delitem restores the ORIGINAL module object at teardown.
    monkeypatch.delitem(sys.modules, "main", raising=False)
    with pytest.raises(config.ConfigError, match="CORS_ORIGINS"):
        importlib.import_module("main")


def test_main_starts_in_production_with_cors_origins_set(monkeypatch):
    """Positive control for the test above: the SAME reimport path must succeed
    once CORS_ORIGINS is set, or the previous test would only be proving that
    main.py is broken in general, not that the guard is what's firing."""
    import importlib

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    # Anything else main.py requires in production must also be present, or this
    # would fail for the WRONG reason (SECRET_KEY, OPENAI_API_KEY — see
    # config.REQUIRED_IN_PRODUCTION) and look like a false confirmation.
    monkeypatch.setenv("SECRET_KEY", "test-only-not-a-real-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    # Same review fix as above: restore the ORIGINAL `main` module at teardown instead of
    # leaving the fresh copy (with its own rate-limit dict) in sys.modules.
    monkeypatch.delitem(sys.modules, "main", raising=False)
    fresh_main = importlib.import_module("main")
    assert fresh_main.app is not None
    monkeypatch.delitem(sys.modules, "main", raising=False)

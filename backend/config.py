"""AP38 — one place that decides what a required variable means, and when its
absence is fatal.

Three reads in this backend were dangerous in production and silent by construction:

  * `reminders._secret()` fell back to the literal a hardcoded string
    that is in the public repo — so every unsubscribe token was forgeable if `SECRET_KEY`
    was unset (and it is absent from `.env.example`, so it probably was).
  * the session cookie was `Secure` only if the undocumented `APP_COOKIE_SECURE=1` was
    set — off by default, on an HTTPS-only site.
  * `CORS_ORIGINS` fell back to localhost with `allow_credentials=True`.

The fix is not to sprinkle `raise` at import time: a module-level exception on a
serverless function surfaces as an opaque `FUNCTION_INVOCATION_FAILED` with no detail,
which is the exact failure `index.py` was written to turn into a readable 500 (see its
docstring). So `require()` raises at CALL time, and the app still boots.

`ENVIRONMENT` is the switch. In development, a required-but-unset variable falls back to
a clearly-marked dev default and logs a warning. In anything else, it raises.
"""
from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger(__name__)


def environment() -> str:
    """`development` (the default) vs anything else. Read live, not cached, so tests can
    flip it with `monkeypatch.setenv` without re-importing the module."""
    return os.getenv("ENVIRONMENT", "development").strip().lower()


def is_production() -> bool:
    return environment() != "development"


class ConfigError(RuntimeError):
    """A required variable is unset in a non-development environment. Raised at call
    time, never at import — see the module docstring."""


# Sentinel: `require(name)` with no dev_default mints a random one (for secrets),
# rather than raising like an explicit `None` does.
_GENERATE = object()

# The generated dev defaults, cached per variable name. A secret must be STABLE within a
# process — a token minted and later verified in the same dev session has to match — so
# mint once and reuse, not once per call.
_dev_secrets: dict[str, str] = {}


def require(name: str, dev_default=_GENERATE) -> str:
    """Return the variable's value.

    In production it must be set — otherwise `ConfigError`, loudly, naming the variable.
    In development an unset value falls back to `dev_default` (if given) with a warning,
    so a laptop still runs; if there is no dev default even development raises, because a
    variable with no safe default is not safe to invent one for.
    """
    val = os.getenv(name)
    if val:
        return val
    if is_production():
        raise ConfigError(
            f"{name} is required in ENVIRONMENT={environment()!r} and is not set. "
            f"This value has no safe default in production; set it in the deployment "
            f"environment. (See .env.example.)")
    if dev_default is _GENERATE:
        # No caller-supplied default: mint a per-process random one, cached by name so
        # repeated calls return the SAME value within the process. Used for secrets,
        # where the dev fallback must not be a value anyone can read from the repo (the
        # class of defect AP38 fixed).
        dev_default = _dev_secrets.setdefault(name, secrets.token_urlsafe(32))
    elif dev_default is None:
        raise ConfigError(f"{name} is not set and has no development default.")
    logger.warning("config: %s is unset — using a development-only default. "
                   "This MUST be set in production.", name)
    return dev_default


def optional(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def bool_flag(name: str, default: bool) -> bool:
    """A boolean env var. Unset -> `default`. `"0"`/`"false"`/`"no"`/`"off"` -> False,
    anything else that is set -> True. Used for `APP_COOKIE_SECURE`, whose default flips
    to True under AP38: a session cookie on an HTTPS site is Secure unless a human turns
    it off for local HTTP dev."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# The single source of truth for "what does this backend read from the environment".
# The drift test in tests/test_config_ap38.py derives from the code, not from here, so
# this list is documentation; the ENFORCEMENT is the test.
REQUIRED_IN_PRODUCTION = ["SECRET_KEY", "CORS_ORIGINS", "OPENAI_API_KEY"]


def warn_unset_required() -> None:
    """Log ONE warning at startup naming every production-required variable that is
    unset. Called from main.py. In development this is informational; in production the
    individual `require()` calls are the hard stop, but a single up-front list is what a
    deployer actually reads."""
    missing = [n for n in REQUIRED_IN_PRODUCTION if not os.getenv(n)]
    if missing:
        logger.warning("config: required variable(s) unset: %s (ENVIRONMENT=%s)",
                       ", ".join(missing), environment())

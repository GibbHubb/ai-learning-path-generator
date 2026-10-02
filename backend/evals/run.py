"""AP39 — score the learning-path generator against the gold set.

    python backend/evals/run.py                 # --offline: the recorded fixtures, no network
    python backend/evals/run.py --live          # real Gemini calls (asks first)
    python backend/evals/run.py --record        # --live, then overwrite the fixtures
    python backend/evals/run.py --score-stdin   # score API responses piped in as JSON
    python backend/evals/run.py --write-baseline

What OFFLINE measures, honestly: each fixture is fed in as the model's reply and
pushed through the REAL app — POST /api/generate, prompt build, AP33 schema
validation, category coercion, the database write and the response model — and
the checks score the HTTP response. So a change to the pipeline (routes.py,
ai_service.py, schemas.py) moves the score even though the fixtures do not.
What it does NOT measure: whether the live model still answers like the
fixtures. Only --live does that, and the fixtures' recording date and source
are printed on every run so their age is on the same screen as the score.

Every check is deterministic (see scoring.py); no model is ever used to score.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVALS_DIR.parent
GOLDSET = EVALS_DIR / "goldset.json"
FIXTURES = EVALS_DIR / "fixtures"
BASELINE = EVALS_DIR / "baseline.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scoring import CHECKS, aggregate, score_case  # noqa: E402  (evals/ is on sys.path as the script dir)


def load_goldset() -> list[dict]:
    return json.loads(GOLDSET.read_text(encoding="utf-8"))["cases"]


def fixture_path(case_id: str) -> Path:
    return FIXTURES / f"{case_id}.json"


def load_fixture(case_id: str) -> dict:
    return json.loads(fixture_path(case_id).read_text(encoding="utf-8"))


# ── the app, isolated ───────────────────────────────────────────────────────

@contextmanager
def app_client():
    """A TestClient on the real app, bound to a THROWAWAY SQLite file.

    DATABASE_URL is overridden before the app is imported, so an eval run can
    never touch a configured database (the generation cache, the paths and the
    usage rows all land in the temp file, deleted afterwards). The per-route
    rate limit is lifted: 15 cases from one client would otherwise hit AP35's
    5-per-minute cap, and rate limiting is not what is being scored.
    """
    tmp = tempfile.mkdtemp(prefix="ap39-evals-")
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp, 'evals.db').as_posix()}"
    os.environ.setdefault("ENVIRONMENT", "development")
    import logging
    logging.disable(logging.WARNING)  # the app logs every request at INFO; the table is the output

    from fastapi.testclient import TestClient
    import database
    import main
    import rate_limit
    import routes

    rate_limit.RATE_LIMIT = 10**6
    # Enrichment is a separate model call per milestone (light model) and out of
    # scope (plan §4); never let an eval spend quota on it.
    routes.enrich_milestone_resources = lambda *a, **k: None
    try:
        with TestClient(main.app) as client:
            yield client
    finally:
        database.engine.dispose()
        try:
            for f in Path(tmp).iterdir():
                f.unlink()
            Path(tmp).rmdir()
        except OSError:
            pass


@contextmanager
def scripted_model(reply_text: str | None):
    """Replace llm.chat_json with one that returns `reply_text` — or, when it
    is None, wrap the real one so its raw reply is captured."""
    import llm

    real = llm.chat_json
    captured: list[str] = []

    def fake(system, user, **kwargs):
        captured.append(reply_text)
        return reply_text

    def recording(system, user, **kwargs):
        out = real(system, user, **kwargs)
        captured.append(out)
        return out

    llm.chat_json = fake if reply_text is not None else recording
    try:
        yield captured
    finally:
        llm.chat_json = real


def generate_via_api(client, case: dict) -> tuple[int, dict]:
    body = {k: case[k] for k in ("goal", "experience_level", "time_commitment", "language")}
    r = client.post("/api/generate", json=body)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def score_one(case: dict, raw_obj, status: int, path: dict) -> dict:
    if status != 200:
        # The app refused the reply (e.g. AP33's 502): nothing reached the user.
        row = {c: 0.0 for c in CHECKS}
        row["total"] = 0.0
    else:
        row = score_case(case, raw_obj, path)
    row["case"] = case["id"]
    row["status"] = status
    return row


# ── modes ────────────────────────────────────────────────────────────────────

def run_offline(cases: list[dict]) -> list[dict]:
    rows = []
    with app_client() as client:
        for case in cases:
            fx = load_fixture(case["id"])
            raw_obj = fx["response"]
            with scripted_model(json.dumps(raw_obj, ensure_ascii=False)):
                status, path = generate_via_api(client, case)
            rows.append(score_one(case, raw_obj, status, path))
    return rows


def run_live(cases: list[dict], record: bool) -> list[dict]:
    import llm

    rows = []
    model = llm.resolved_model(light=False)
    with app_client() as client:
        for i, case in enumerate(cases):
            with scripted_model(None) as captured:
                status, path = generate_via_api(client, case)
            raw_text = captured[-1] if captured else ""
            try:
                raw_obj = json.loads(llm.strip_fences(raw_text))
            except (ValueError, TypeError):
                raw_obj = None
            rows.append(score_one(case, raw_obj, status, path))
            if record and raw_obj is not None and status == 200:
                fixture_path(case["id"]).write_text(json.dumps({
                    "case_id": case["id"],
                    "source": "recorded",
                    "model": model,
                    "recorded_at": time.strftime("%Y-%m-%d"),
                    "response": raw_obj,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if i < len(cases) - 1:
                time.sleep(13)  # stay under the free tier's per-minute request cap
    return rows


def run_score_stdin(cases: list[dict]) -> list[dict]:
    """Score responses captured from a RUNNING app.

    stdin: a JSON list of {"case_id": ..., "status": 200, "path": <the
    /api/generate response body>, "raw": <optional model JSON>}. Without "raw",
    the schema check is scored on that case's fixture and the output says so.
    """
    by_id = {c["id"]: c for c in cases}
    rows = []
    for item in json.load(sys.stdin):
        case = by_id[item["case_id"]]
        raw_obj = item.get("raw")
        if raw_obj is None:
            raw_obj = load_fixture(case["id"])["response"]
            print(f"  ({case['id']}: no raw reply supplied — schema check uses the fixture)")
        rows.append(score_one(case, raw_obj, item.get("status", 200), item["path"]))
    return rows


# ── output ───────────────────────────────────────────────────────────────────

def fixture_provenance(cases: list[dict]) -> str:
    sources, dates = set(), set()
    for c in cases:
        try:
            fx = load_fixture(c["id"])
        except FileNotFoundError:
            sources.add("MISSING")
            continue
        sources.add(fx.get("source", "?"))
        dates.add(fx.get("recorded_at", "?"))
    return (f"fixtures: source={'/'.join(sorted(sources))}  "
            f"dated={min(dates) if dates else '?'}..{max(dates) if dates else '?'}")


def print_table(rows: list[dict], header: str) -> dict:
    print(header)
    cols = ("schema", "category", "milestone_count", "keyword_recall", "monotonic_hours", "total")
    short = ("schema", "categ", "count", "kw_recall", "mono_hrs", "TOTAL")
    print(f"{'case':<12} {'http':>4}  " + "  ".join(f"{s:>9}" for s in short))
    for r in rows:
        print(f"{r['case']:<12} {r['status']:>4}  " + "  ".join(f"{r[c]:>9.2f}" for c in cols))
    agg = aggregate(rows)
    print(f"{'AGGREGATE':<12} {'':>4}  " + "  ".join(f"{agg[c]:>9.3f}" for c in cols))
    return agg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="score the fixtures (default)")
    mode.add_argument("--live", action="store_true", help="call the real model (asks first)")
    mode.add_argument("--record", action="store_true", help="--live, and overwrite the fixtures")
    mode.add_argument("--score-stdin", action="store_true", help="score API responses from stdin")
    ap.add_argument("--yes", action="store_true", help="skip the --live confirmation")
    ap.add_argument("--write-baseline", action="store_true", help="write baseline.json from this run")
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--fixtures", metavar="DIR", help="read fixtures from DIR instead of evals/fixtures")
    ap.add_argument("--json", metavar="PATH", help="also write the per-case rows + aggregate as JSON")
    args = ap.parse_args(argv)

    if args.fixtures:
        global FIXTURES
        FIXTURES = Path(args.fixtures)
    cases = load_goldset()
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    started = time.time()
    if args.live or args.record:
        if not os.getenv("GEMINI_API_KEY"):
            print("refusing: --live needs GEMINI_API_KEY (the app runs on Gemini's free tier, AP46).",
                  file=sys.stderr)
            return 2
        import llm
        import usage
        model = llm.resolved_model(light=False)
        price = usage._PRICES.get(model)
        cost = "unknown — model not in usage._PRICES" if price is None else (
            "$0.00 (free tier)" if price["prompt"] == 0 and price["completion"] == 0
            else f"~${len(cases) * 0.01:.2f}")
        print(f"--live: {len(cases)} call(s) to {model}; estimated cost {cost}; "
              f"~13s between calls to stay under the per-minute quota.")
        if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
            print("aborted — no call made.")
            return 1
        rows = run_live(cases, record=args.record)
        header = f"LIVE run against {model} on {time.strftime('%Y-%m-%d')}"
    elif args.score_stdin:
        rows = run_score_stdin(cases)
        header = "scored from stdin (a running app's responses)"
    else:
        rows = run_offline(cases)
        header = "OFFLINE run — " + fixture_provenance(cases)

    agg = print_table(rows, header)
    print(f"({len(rows)} case(s) in {time.time() - started:.1f}s)")

    if args.json:
        Path(args.json).write_text(json.dumps({"aggregate": agg, "rows": rows}, indent=2),
                                   encoding="utf-8")

    if args.write_baseline:
        BASELINE.write_text(json.dumps({
            "_about": "AP39 offline scores. test_evals_ap39.py fails if a check drops below these.",
            "aggregate": {k: round(v, 4) for k, v in agg.items()},
            "cases": {r["case"]: {c: round(r[c], 4) for c in (*CHECKS, "total")} for r in rows},
        }, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

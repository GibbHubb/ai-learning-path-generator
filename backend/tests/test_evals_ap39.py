"""AP39 — the eval harness is wired into the suite, not a script nobody runs.

Two halves:

* unit tests of the five checks, each with a payload that must PASS and one
  that must FAIL — a check that has never gone red is not a check;
* the regression gate: `run.py --offline` (in a subprocess, so its app import,
  throwaway database and lifted rate limit cannot leak into this test session)
  must score at or above `baseline.json` on every check and every case.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
EVALS_DIR = BACKEND_DIR / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import scoring  # noqa: E402


def _path(n=6, hours=None, category="Programming", text="python pandas"):
    hours = hours or list(range(1, n + 1))
    return {
        "title": "T", "description": text, "category": category,
        "milestones": [{"title": f"M{i}", "description": "d", "estimated_hours": h,
                        "resources": []} for i, h in enumerate(hours)],
    }


def _raw(n=6):
    return {"path_title": "T", "path_description": "D", "category": "Programming",
            "milestones": [{"title": "m", "description": "d", "estimated_hours": 1,
                            "resources": []}] * n}


# ── the five checks, each seen passing AND failing ──────────────────────────

def test_schema_check():
    assert scoring.check_schema(_raw()) == 1.0
    assert scoring.check_schema({"path_title": "T"}) == 0.0          # missing fields
    bad = _raw()
    bad["milestones"][0] = {"title": "m", "description": "d", "estimated_hours": -1}
    assert scoring.check_schema(bad) == 0.0                           # out of range


def test_category_check():
    assert scoring.check_category(_path(category="Design"), "Design") == 1.0
    assert scoring.check_category(_path(category="Other"), "Design") == 0.0


def test_milestone_count_check():
    assert scoring.check_milestone_count(_path(n=5)) == 1.0
    assert scoring.check_milestone_count(_path(n=8)) == 1.0
    assert scoring.check_milestone_count(_path(n=4)) == 0.0
    assert scoring.check_milestone_count(_path(n=9)) == 0.0


def test_keyword_recall_check():
    p = _path(text="We use Pandas and build a Visualisation in Jupyter")
    kws = ["pandas", "jupyter", "visualization|visualisation", "numpy"]
    assert scoring.check_keyword_recall(p, kws) == 0.75
    assert scoring.check_keyword_recall(_path(text="knitting"), kws) == 0.0


def test_keyword_recall_folds_accents_and_reads_resources():
    p = _path(text="")
    p["milestones"][0]["description"] = "Les données et le modèle"
    p["milestones"][1]["resources"] = [{"title": "Exposição", "url": "u"}]
    assert scoring.check_keyword_recall(p, ["donnees", "modele", "exposicao"]) == 1.0


def test_monotonic_hours_check():
    assert scoring.check_monotonic_hours(_path(hours=[2, 2, 5, 8, 8])) == 1.0
    assert scoring.check_monotonic_hours(_path(hours=[2, 5, 3, 8, 9])) == 0.0
    assert scoring.check_monotonic_hours(_path(n=1)) == 0.0


def test_score_case_total_is_the_mean_of_the_five():
    case = {"expected_category": "Programming", "expected_keywords": ["python", "rust"]}
    s = scoring.score_case(case, _raw(), _path(text="python"))
    assert s["keyword_recall"] == 0.5
    assert s["total"] == pytest.approx((1 + 1 + 1 + 0.5 + 1) / 5)


# ── the gold set itself ─────────────────────────────────────────────────────

def test_goldset_shape():
    cases = json.loads((EVALS_DIR / "goldset.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 15
    assert len({c["id"] for c in cases}) == 15
    assert len({c["expected_category"] for c in cases}) >= 5
    assert len({c["language"] for c in cases} - {"en"}) >= 2
    for c in cases:
        assert 3 <= len(c["expected_keywords"]) <= 6, c["id"]
        assert (EVALS_DIR / "fixtures" / f"{c['id']}.json").is_file(), c["id"]


# ── the regression gate ─────────────────────────────────────────────────────

def _offline(tmp_path, extra_env=None):
    out = tmp_path / "evals.json"
    env = {**os.environ, **(extra_env or {})}
    proc = subprocess.run([sys.executable, str(EVALS_DIR / "run.py"), "--offline",
                           "--json", str(out)],
                          cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
                          timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text(encoding="utf-8")), proc.stdout


def test_offline_run_does_not_regress_below_the_baseline(tmp_path):
    result, stdout = _offline(tmp_path)
    baseline = json.loads((EVALS_DIR / "baseline.json").read_text(encoding="utf-8"))

    # The provenance line is on screen with the score (plan §8).
    assert "fixtures: source=" in stdout and "dated=" in stdout

    assert len(result["rows"]) == len(baseline["cases"]) == 15
    regressions = []
    for row in result["rows"]:
        for check, floor in baseline["cases"][row["case"]].items():
            if row[check] + 1e-9 < floor:
                regressions.append(f"{row['case']}.{check}: {row[check]:.3f} < baseline {floor:.3f}")
    assert regressions == [], "\n".join(regressions)


def test_the_gate_goes_red_when_a_fixture_regresses(tmp_path):
    """Negative control for the gate above: break one fixture (wrong category,
    3 milestones) in a COPY of evals/, run the copy, and the regression check
    must fire. Without this, a gate that compares nothing would look the same."""
    import shutil

    copy = tmp_path / "fixtures"
    shutil.copytree(EVALS_DIR / "fixtures", copy)
    fx_path = copy / "figma.json"
    fx = json.loads(fx_path.read_text(encoding="utf-8"))
    fx["response"]["category"] = "Science"
    fx["response"]["milestones"] = fx["response"]["milestones"][:3]
    fx_path.write_text(json.dumps(fx), encoding="utf-8")

    out = tmp_path / "broken.json"
    proc = subprocess.run([sys.executable, str(EVALS_DIR / "run.py"), "--offline",
                           "--fixtures", str(copy), "--json", str(out)],
                          cwd=BACKEND_DIR, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = {r["case"]: r for r in json.loads(out.read_text(encoding="utf-8"))["rows"]}
    baseline = json.loads((EVALS_DIR / "baseline.json").read_text(encoding="utf-8"))
    dropped = [c for c, floor in baseline["cases"]["figma"].items() if rows["figma"][c] + 1e-9 < floor]
    assert {"category", "milestone_count", "total"} <= set(dropped), dropped

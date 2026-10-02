"""AP39 — the five deterministic checks the generator is scored on.

Pure functions: string, set and schema operations only. No model call, no
network, no randomness — the same response always gets the same score, which
is the whole point of a number you compare across prompt edits.

A check returns a float in [0, 1]. Four are binary (0.0 or 1.0); keyword
recall is a fraction.

What these checks are NOT: a quality score. They are a regression signal — they
catch a prompt edit that breaks the structure (wrong category, wrong length,
milestones in a strange order, a path that stopped talking about the goal).
Two paths can both score 1.0 and be very different in usefulness.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from pydantic import ValidationError

from schemas import GeneratedPath

CHECKS = ("schema", "category", "milestone_count", "keyword_recall", "monotonic_hours")

MIN_MILESTONES, MAX_MILESTONES = 5, 8  # ai_service.py's prompt: "5-8 major milestones"


def fold(text: str) -> str:
    """Lowercase and strip accents, so `données` matches `donnees` and
    `Exposição` matches `exposicao` — the gold keywords are written unaccented."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _resource_text(r: Any) -> str:
    if isinstance(r, dict):
        return " ".join(str(v) for v in r.values())
    return str(r)


def path_text(path: dict) -> str:
    """Everything a learner reads: titles, descriptions and resources."""
    parts = [path.get("title", ""), path.get("description", "")]
    for m in path.get("milestones", []) or []:
        parts.append(m.get("title", ""))
        parts.append(m.get("description", ""))
        parts.extend(_resource_text(r) for r in (m.get("resources") or []))
    return fold(" ".join(str(p) for p in parts if p))


# ── the five checks ─────────────────────────────────────────────────────────

def check_schema(raw_model_output: Any) -> float:
    """1. The model's raw JSON validates against AP33's GeneratedPath."""
    try:
        GeneratedPath.model_validate(raw_model_output)
    except ValidationError:
        return 0.0
    return 1.0


def check_category(path: dict, expected: str) -> float:
    """2. The category the app stored equals the gold category."""
    return 1.0 if path.get("category") == expected else 0.0


def check_milestone_count(path: dict) -> float:
    """3. 5-8 milestones, as the prompt demands."""
    n = len(path.get("milestones") or [])
    return 1.0 if MIN_MILESTONES <= n <= MAX_MILESTONES else 0.0


def keyword_hits(path: dict, keywords: list[str]) -> list[bool]:
    text = path_text(path)
    return [any(fold(alt) in text for alt in kw.split("|")) for kw in keywords]


def check_keyword_recall(path: dict, keywords: list[str]) -> float:
    """4. Fraction of the gold keywords the path mentions anywhere."""
    if not keywords:
        return 0.0
    hits = keyword_hits(path, keywords)
    return sum(hits) / len(hits)


def check_monotonic_hours(path: dict) -> float:
    """5. estimated_hours never decreases from one milestone to the next — the
    difficulty proxy for "each milestone should build on previous ones"
    (ai_service.py's prompt). Crude on purpose: a final capstone that is
    shorter than the milestone before it fails this, and that is a real
    signal about ordering, not a bug in the check."""
    hours = [m.get("estimated_hours") for m in (path.get("milestones") or [])]
    if len(hours) < 2 or any(not isinstance(h, (int, float)) for h in hours):
        return 0.0
    return 1.0 if all(a <= b for a, b in zip(hours, hours[1:])) else 0.0


def score_case(case: dict, raw_model_output: Any, path: dict) -> dict:
    """Score one gold case.

    `raw_model_output` is the model's parsed JSON (what check 1 validates).
    `path` is what the APP returned for it — the /api/generate response body
    (title, description, category, milestones[...]) — so checks 2-5 score what
    a user actually receives, after every transformation the pipeline makes.
    """
    scores = {
        "schema": check_schema(raw_model_output),
        "category": check_category(path, case["expected_category"]),
        "milestone_count": check_milestone_count(path),
        "keyword_recall": check_keyword_recall(path, case["expected_keywords"]),
        "monotonic_hours": check_monotonic_hours(path),
    }
    scores["total"] = sum(scores[c] for c in CHECKS) / len(CHECKS)
    return scores


def aggregate(rows: list[dict]) -> dict:
    """Mean of each check across cases, plus the overall mean."""
    if not rows:
        return {c: 0.0 for c in (*CHECKS, "total")}
    return {c: sum(r[c] for r in rows) / len(rows) for c in (*CHECKS, "total")}

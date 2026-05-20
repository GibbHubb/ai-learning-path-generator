"""AP8 — milestone quiz generation + grading.

Generation:
  * Claude Haiku via the existing `anthropic` SDK already on requirements.txt
  * Strict JSON-only system message; the model returns a list of 3-5 MCQs
    with `question`, `options[]` (exactly 4), `correct_index` (0-3), and a
    short `explanation`.
  * `validate_quiz()` rejects anything not matching that shape so a flaky
    response never gets persisted as a corrupt row.

Caching:
  * `MilestoneQuiz` is a single row per milestone — same questions across
    every learner so retries see identical questions (plan §3).
  * Generation rate-limit: refuse to re-generate if the last generation was
    less than 1 hour ago (plan AC).

Grading:
  * Server-authoritative — the cached row keeps `correct_index`, the
    `/quiz/attempt` endpoint re-scores answers regardless of what the
    client thinks. Plan said client-side for speed; we still echo a
    breakdown to the client for the result screen.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from models import Milestone, MilestoneQuiz

logger = logging.getLogger(__name__)

PASS_THRESHOLD = 0.70                  # 70% — user-confirmed default
GENERATION_RATE_LIMIT_SECONDS = 3600   # 1 hour
REGENERATE_RATE_LIMIT_SECONDS = 24 * 3600  # 1 day
QUESTION_COUNT_MIN = 3
QUESTION_COUNT_MAX = 5
MIN_MILESTONE_BODY_CHARS = 100         # below this we refuse to generate


@dataclass
class QuizQuestion:
    question: str
    options: list[str]
    correct_index: int
    explanation: str


def validate_quiz(raw: Any) -> list[QuizQuestion] | None:
    """Returns the parsed question list, or None if the shape is wrong.
    Conservative: every field strictly typed, length-bounded."""
    if not isinstance(raw, list) or not (QUESTION_COUNT_MIN <= len(raw) <= QUESTION_COUNT_MAX):
        return None
    out: list[QuizQuestion] = []
    for q in raw:
        if not isinstance(q, dict):
            return None
        question = q.get("question")
        options = q.get("options")
        correct_index = q.get("correct_index")
        explanation = q.get("explanation", "")
        if not isinstance(question, str) or not question.strip():
            return None
        if not isinstance(options, list) or len(options) != 4:
            return None
        if not all(isinstance(o, str) and o.strip() for o in options):
            return None
        if not isinstance(correct_index, int) or not (0 <= correct_index < 4):
            return None
        if not isinstance(explanation, str):
            return None
        out.append(QuizQuestion(
            question=question.strip(),
            options=[o.strip() for o in options],
            correct_index=correct_index,
            explanation=explanation.strip(),
        ))
    return out


def _quiz_prompt(title: str, description: str) -> str:
    return f"""You are writing a short comprehension quiz for someone who just finished this learning milestone.

Milestone title: {title}

Milestone description / scope:
{description}

Write exactly {QUESTION_COUNT_MIN}-{QUESTION_COUNT_MAX} multiple-choice questions that genuinely test understanding (not trivia, not phrasing). Each question must have **exactly 4 options**, one correct, with a short explanation that makes the answer click for a beginner.

Return ONLY valid JSON (no markdown, no prose, no code fences). The top-level value MUST be a JSON array of objects matching this schema:

[
  {{
    "question":      "<the question, ≤200 chars>",
    "options":       ["A", "B", "C", "D"],
    "correct_index": <integer 0..3>,
    "explanation":   "<1-2 sentences, ≤300 chars>"
  }}
]"""


def _call_claude(milestone: Milestone) -> list[QuizQuestion] | None:
    """Best-effort Claude call. Returns None on any failure (network, parse,
    validation) so the caller can short-circuit to a 502."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[AP8] ANTHROPIC_API_KEY missing — cannot generate quiz")
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("[AP8] anthropic SDK not installed")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": _quiz_prompt(milestone.title or "Untitled", milestone.description or ""),
            }],
        )
        raw_text = msg.content[0].text.strip() if msg.content else ""
        # Strip code fences if Claude rebels
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()
        parsed = json.loads(raw_text)
    except Exception as exc:
        logger.warning("[AP8] Claude quiz call failed: %s", exc)
        return None

    return validate_quiz(parsed)


def _serialise(questions: list[QuizQuestion]) -> str:
    return json.dumps([
        {
            "question": q.question,
            "options": q.options,
            "correct_index": q.correct_index,
            "explanation": q.explanation,
        }
        for q in questions
    ])


def get_or_generate_quiz(milestone_id: int, db: Session, *, force: bool = False) -> tuple[MilestoneQuiz | None, str | None]:
    """Returns `(quiz_row, error)`.

    * cache hit → returns the existing row, error=None
    * cache miss → generates via Claude, persists, returns the new row
    * any failure → returns (None, error_string) so the caller can map
      it to a clean HTTP response
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        return None, "milestone-not-found"
    if not (milestone.description or "").strip() or len((milestone.description or "")) < MIN_MILESTONE_BODY_CHARS:
        return None, "milestone-content-too-short"

    existing = (
        db.query(MilestoneQuiz)
        .filter(MilestoneQuiz.milestone_id == milestone_id)
        .first()
    )

    if existing and not force:
        return existing, None

    # Rate-limit generation (1/hour for any caller; 1/day for force)
    if existing:
        gap = datetime.utcnow() - existing.updated_at
        if force and gap < timedelta(seconds=REGENERATE_RATE_LIMIT_SECONDS):
            return None, "regenerate-rate-limited"
        if not force and gap < timedelta(seconds=GENERATION_RATE_LIMIT_SECONDS):
            return existing, None  # within 1h cache window — return cached

    questions = _call_claude(milestone)
    if not questions:
        return None, "generation-failed"

    payload = _serialise(questions)
    if existing:
        existing.questions_json = payload
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing, None

    row = MilestoneQuiz(milestone_id=milestone_id, questions_json=payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, None


def grade_attempt(quiz: MilestoneQuiz, answers: list[int]) -> dict:
    """Score a submission. Returns:
        {
          score: 0.0..1.0,
          passed: bool,
          results: [{question_index, your_answer, correct_index, correct, explanation}, ...]
        }
    """
    questions = json.loads(quiz.questions_json)
    if len(answers) != len(questions):
        # Pad with -1 for missing answers (count as wrong); ignore extras.
        answers = list(answers)[:len(questions)]
        while len(answers) < len(questions):
            answers.append(-1)

    correct_count = 0
    results = []
    for i, (q, ans) in enumerate(zip(questions, answers)):
        is_correct = isinstance(ans, int) and ans == q["correct_index"]
        if is_correct:
            correct_count += 1
        results.append({
            "question_index": i,
            "your_answer": ans if isinstance(ans, int) else -1,
            "correct_index": q["correct_index"],
            "correct": is_correct,
            "explanation": q.get("explanation", ""),
        })

    score = correct_count / len(questions) if questions else 0.0
    return {
        "score": round(score, 4),
        "passed": score >= PASS_THRESHOLD,
        "results": results,
        "threshold": PASS_THRESHOLD,
    }

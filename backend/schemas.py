"""AP33 — the schema a model's generated-path JSON must satisfy before it reaches
routes.py or the database.

`generate_learning_path`/`adjust_difficulty` in `ai_service.py` used to do
`json.loads(...)` and hand the raw dict straight back. `routes.py` then subscripted it
directly (`ai_result["path_title"]`, `milestone_data["estimated_hours"]`, ...), so any
missing/malformed key was a bare `KeyError` surfacing as a 500 with the literal Python
key name in the response body — and on the streaming endpoint, after milestone rows had
already been flushed to the database.

Mirrors the existing pattern in `quizzes.py::validate_quiz` (reject anything malformed,
never persist a half-formed structure) but as Pydantic models, since AP33 additionally
wants type coercion (`estimated_hours` as a bounded float) and one deliberate exception:
`category` is *coerced* to `"Other"` rather than rejected — see `GeneratedPath` below.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# The exact seven values the generation prompt names (ai_service.py) — the ONLY
# allowed values for `category`. `GeneratedPath.category` below hardcodes the same
# seven as a `Literal` (see the comment there for why not derived from this tuple);
# a test asserts the two never drift apart.
CATEGORIES = ("Programming", "Design", "Business", "Science", "Creative", "Language", "Other")


class GeneratedMilestone(BaseModel):
    """One milestone inside a generated (or regenerated) learning path."""
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # Sanity bound, not a business rule — guards against a model returning something
    # like 100000, not a claim that 500 hours is a meaningful ceiling for a milestone.
    # `ge=0`, not `gt=0`: a model answering 0 was rendered fine before AP33, and
    # failing the whole path over it would be a regression, not a catch.
    estimated_hours: float = Field(ge=0, le=500)
    # 🔴 A resource is a string OR an object: `routes._parse_resources` documents both
    # and `_build_milestone_response` re-serialises the dict form. Typing this
    # `list[str]` made a model's object-shaped resources fail validation twice and
    # 502 a path that used to generate and render (review, 2026-09-24).
    resources: list[str | dict] = Field(default_factory=list)


class GeneratedPath(BaseModel):
    """The full shape of a `generate_learning_path` / model JSON response."""
    path_title: str = Field(min_length=1)
    path_description: str = Field(min_length=1)
    # Hardcoded, not `Literal[*CATEGORIES]` (PEP 646 star-unpacking): that syntax is a
    # SyntaxError on Python < 3.11, and this project's README states "Python 3.8+" as
    # its own minimum, deployed via an unpinned Vercel Python builder — a code-review
    # pass on AP34's identical pattern in routes.py caught this exact break before it
    # shipped there (see routes.py's LearningPathCreate for the full note). The drift
    # risk of "these two lists could disagree" is covered by a test instead:
    # test_schema_conformance_ap33.py::test_category_literal_matches_categories_tuple.
    category: Literal["Programming", "Design", "Business", "Science", "Creative", "Language", "Other"] = "Other"
    milestones: list[GeneratedMilestone] = Field(min_length=1)

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_unknown_category(cls, v):
        """A model returning e.g. "programming" (wrong casing/value) lands as "Other"
        instead of failing the whole generation — losing a good title + description +
        5-8 good milestones over one miscategorised field would be a worse failure than
        the mis-grouping it fixes. See plans/ai_path/AP33.md §8 for why this is
        deliberately NOT one of the payload shapes that 502s."""
        if v not in CATEGORIES:
            return "Other"
        return v


class PathGenerationError(RuntimeError):
    """Raised when a model's JSON response fails schema validation twice in a row
    (the one retry `ai_service.generate_learning_path` attempts). `routes.py` maps
    this to a clean 502 — never the raw validation error text, which would carry
    Python type names."""

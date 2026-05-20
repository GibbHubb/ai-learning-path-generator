"""AP12 — milestone note helpers.

Heuristic difficulty parser: scans the note body for substring cues and
returns one of {-1, 0, +1, +2}. AP5 may roll these up across users to
detect milestones that consistently confuse people.
"""
from __future__ import annotations

import re

# Order matters: more-specific cues first so "too easy" doesn't
# match the broader "easy" patterns later (we don't have any here, but
# preserved for future extension).
_CUES: list[tuple[int, list[str]]] = [
    (+2, ["confused", "didn't get", "didnt get", "lost me", "no idea", "totally lost", "stuck"]),
    (+1, ["too hard", "too difficult", "challenging", "struggled", "struggle", "tough"]),
    (-1, ["too easy", "trivial", "way too simple", "boring"]),
]


def parse_difficulty_flag(content: str) -> int:
    """Return -1/0/+1/+2 based on the strongest cue present in `content`.
    Conservative: defaults to 0 unless a cue clearly fires.
    """
    if not content:
        return 0
    body = content.lower()
    # Strip simple "not …" negation windows so "not confused" doesn't fire.
    body = re.sub(r"\bnot\s+\w+", " ", body)
    for flag, cues in _CUES:
        for cue in cues:
            if cue in body:
                return flag
    return 0

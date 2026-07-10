"""AP28 — localized resource enrichment.

Asserts the enrichment prompt assembly localises for a known non-English
language, stays byte-identical to English for `en`, and normalises unknown
codes to English. Pure-function tests — no API calls.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_service import build_enrichment_prompt, language_instruction  # noqa: E402


def test_non_english_includes_localisation_line():
    prompt = build_enrichment_prompt("Vectors", "Learn linear algebra", "master ML", "nl")
    assert language_instruction("nl") in prompt
    assert "in Dutch" in prompt


def test_english_omits_localisation_line():
    prompt = build_enrichment_prompt("Vectors", "Learn linear algebra", "master ML", "en")
    # language_instruction("en") returns "" — nothing appended.
    assert "in Dutch" not in prompt
    assert "change language" not in prompt.replace(
        "only the free-text `title` values may change language.", ""
    )


def test_english_prompt_is_byte_identical_to_no_language():
    # The default (no language arg) and explicit "en" must be identical.
    assert (
        build_enrichment_prompt("T", "D", "G")
        == build_enrichment_prompt("T", "D", "G", "en")
    )


def test_unknown_language_falls_back_to_english():
    unknown = build_enrichment_prompt("T", "D", "G", "zz-not-a-lang")
    english = build_enrichment_prompt("T", "D", "G", "en")
    assert unknown == english


def test_type_enum_pinned_english_for_localised_prompt():
    # The `type` machine value must stay English even when localising titles.
    prompt = build_enrichment_prompt("T", "D", "G", "es")
    assert "video|docs|article" in prompt
    assert language_instruction("es") in prompt

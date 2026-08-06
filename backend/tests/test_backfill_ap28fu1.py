"""AP28-fu1 — selection + guard behaviour for the re-enrichment backfill.

No Anthropic calls are made: enrich_milestone_resources is monkeypatched, so
these tests exercise which paths get picked and how many milestone calls fire.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_enrichment as bf  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import LearningPath, Milestone  # noqa: E402


def _make_path(db, *, language, title, created_at=None, milestones=2):
    p = LearningPath(
        title=title,
        description="x",
        experience_level="beginner",
        time_commitment="5 hours/week",
        language=language,
    )
    if created_at is not None:
        p.created_at = created_at
    db.add(p)
    db.flush()
    for i in range(milestones):
        db.add(Milestone(
            learning_path_id=p.id, title=f"M{i}", description="d",
            order=i, estimated_hours=1.0, resources="[]", completed=False,
        ))
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seeded(db):
    return {
        "en": _make_path(db, language="en", title="English path",
                         created_at=datetime(2026, 1, 1)),
        "nl": _make_path(db, language="nl", title="Nederlands pad",
                         created_at=datetime(2026, 1, 1)),
        "fr": _make_path(db, language="fr", title="Parcours",
                         created_at=datetime(2026, 12, 1)),
    }


class TestSelectPaths:
    def test_default_selects_only_non_english(self, db, seeded):
        got = {p.language for p in bf.select_paths(db)}
        assert got == {"nl", "fr"}

    def test_all_languages_includes_english(self, db, seeded):
        got = {p.language for p in bf.select_paths(db, all_languages=True)}
        assert got == {"en", "nl", "fr"}

    def test_language_filter_narrows_to_one(self, db, seeded):
        got = bf.select_paths(db, language="nl")
        assert [p.id for p in got] == [seeded["nl"].id]

    def test_path_id_wins_over_the_non_english_default(self, db, seeded):
        # An explicit id must be honoured even though the path is English.
        got = bf.select_paths(db, path_id=seeded["en"].id)
        assert [p.id for p in got] == [seeded["en"].id]

    def test_created_before_excludes_newer_paths(self, db, seeded):
        got = bf.select_paths(db, created_before=datetime(2026, 6, 1))
        assert [p.id for p in got] == [seeded["nl"].id]

    def test_limit_caps_the_result(self, db, seeded):
        assert len(bf.select_paths(db, limit=1)) == 1

    def test_returns_empty_when_nothing_matches(self, db, seeded):
        assert bf.select_paths(db, language="de") == []


class TestMain:
    def test_refuses_to_run_without_an_api_key(self, monkeypatch, capsys, seeded):
        # Without the key, enrichment silently no-ops — a run would report
        # success having done nothing. It must fail loudly instead.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert bf.main([]) == 2
        assert "ANTHROPIC_API_KEY" in capsys.readouterr().err

    def test_dry_run_needs_no_key_and_writes_nothing(self, monkeypatch, capsys, seeded):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        calls = []
        monkeypatch.setattr(bf, "enrich_milestone_resources",
                            lambda *a, **kw: calls.append(a))
        assert bf.main(["--dry-run"]) == 0
        assert calls == []
        assert "DRY RUN" in capsys.readouterr().out

    def test_enriches_every_milestone_of_each_selected_path(self, monkeypatch, seeded):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        calls = []
        monkeypatch.setattr(bf, "enrich_milestone_resources",
                            lambda *a, **kw: calls.append(a))
        assert bf.main([]) == 0
        # 2 non-English paths x 2 milestones each
        assert len(calls) == 4
        # the path's own language is threaded through, not a default
        assert {c[4] for c in calls} == {"nl", "fr"}

    def test_one_failing_milestone_does_not_abort_the_run(self, monkeypatch, seeded):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        calls = []

        def flaky(mid, *a):
            calls.append(mid)
            if len(calls) == 1:
                raise RuntimeError("boom")

        monkeypatch.setattr(bf, "enrich_milestone_resources", flaky)
        rc = bf.main([])
        assert len(calls) == 4      # kept going past the failure
        assert rc == 1              # but reported a non-zero exit

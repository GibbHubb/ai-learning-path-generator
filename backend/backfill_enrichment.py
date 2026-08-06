"""AP28-fu1 — re-enrich milestone resources for already-generated paths.

AP28 localised resource enrichment at create/adjust time only. Any path
generated before AP28 landed — or generated in a non-English language before
the language was threaded through — still carries an English resource list
even though the rest of the path is in the user's language.

This is the one-off admin pass that fixes those. It re-runs the same
`enrich_milestone_resources` the live endpoints use, with the path's own
`language`, so there is no second code path to keep in sync.

Usage
-----
    # See what would be touched — no API calls, no writes.
    python backfill_enrichment.py --dry-run

    # Re-enrich every non-English path (the default selection).
    python backfill_enrichment.py

    # Narrow it down.
    python backfill_enrichment.py --language nl
    python backfill_enrichment.py --path-id 42
    python backfill_enrichment.py --created-before 2026-07-10
    python backfill_enrichment.py --limit 25

    # Include English paths too (rarely wanted — they were never wrong).
    python backfill_enrichment.py --all-languages

Notes
-----
* Requires ANTHROPIC_API_KEY unless --dry-run; enrichment silently no-ops
  without it, which would look like a successful run that did nothing.
* Each milestone is one Haiku call. --limit and --created-before exist so a
  large backfill can be run in cost-controlled batches.
* Safe to re-run: enrichment overwrites a milestone's resources column
  wholesale, so a partial run just picks up where it left off.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_service import enrich_milestone_resources  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import LearningPath  # noqa: E402


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {s!r}")


def select_paths(db, *, language=None, path_id=None, created_before=None,
                 all_languages=False, limit=None):
    """Build the target path list. Default selection is every non-English path."""
    q = db.query(LearningPath)
    if path_id is not None:
        q = q.filter(LearningPath.id == path_id)
    elif language:
        q = q.filter(LearningPath.language == language)
    elif not all_languages:
        # The paths AP28 could not have localised at creation time.
        q = q.filter(LearningPath.language != "en")
    if created_before is not None:
        q = q.filter(LearningPath.created_at < created_before)
    q = q.order_by(LearningPath.id)
    if limit:
        q = q.limit(limit)
    return q.all()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be re-enriched; no API calls, no writes")
    ap.add_argument("--language", help="only paths in this language code (e.g. nl)")
    ap.add_argument("--path-id", type=int, help="only this path id")
    ap.add_argument("--created-before", type=_parse_date, metavar="YYYY-MM-DD",
                    help="only paths created before this date")
    ap.add_argument("--all-languages", action="store_true",
                    help="include English paths as well (default: non-English only)")
    ap.add_argument("--limit", type=int, help="cap the number of paths processed")
    args = ap.parse_args(argv)

    if not args.dry_run and not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set. Enrichment would silently "
              "no-op and report success. Set the key, or use --dry-run.",
              file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        paths = select_paths(
            db,
            language=args.language,
            path_id=args.path_id,
            created_before=args.created_before,
            all_languages=args.all_languages,
            limit=args.limit,
        )
        if not paths:
            print("No matching paths — nothing to do.")
            return 0

        total_milestones = sum(len(p.milestones) for p in paths)
        print(f"{len(paths)} path(s), {total_milestones} milestone(s) to re-enrich"
              f"{' [DRY RUN]' if args.dry_run else ''}\n")

        enriched = failed = 0
        for p in paths:
            ms = sorted(p.milestones, key=lambda m: m.order)
            print(f"  path {p.id} [{p.language}] {p.title!r} — {len(ms)} milestone(s)")
            if args.dry_run:
                continue
            for m in ms:
                try:
                    # Same call the live create/adjust endpoints make. The
                    # original free-text `goal` is never persisted, so the
                    # path title stands in for it — the same substitution
                    # adjust_difficulty already makes (routes.py: goal = path.title).
                    enrich_milestone_resources(m.id, m.title, m.description,
                                               p.title or "", p.language)
                    enriched += 1
                except Exception as exc:  # keep going; one bad path is not fatal
                    failed += 1
                    print(f"      ! milestone {m.id} failed: {exc}", file=sys.stderr)

        if args.dry_run:
            print("\nDry run — nothing written.")
        else:
            print(f"\nDone. {enriched} milestone(s) re-enriched, {failed} failed.")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

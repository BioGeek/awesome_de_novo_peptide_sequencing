#!/usr/bin/env python3
"""Keep the counts quoted in CLAUDE.md and WATCHLIST.md honest.

Both files argue from numbers ("Coverage is 240/293", "every one of the 256
`algorithm` rows"), and every one of them goes stale the moment a paper is
added. An audit in September 2026 found 9 of 19 such numbers already wrong,
including "Fifteen tables" which had been wrong since `publication_version`
landed. Nobody notices, because a stale number still reads as authoritative.

WHY THIS IS NOT A SCHEDULED WORKFLOW, unlike the four build_*.py refreshers:
their numbers come from external APIs that move on their own, so they need a
clock. These come from denovo.db in the same commit, so they need a trigger,
and .githooks/pre-commit already has exactly the right one. It fires when
denovo.db is staged and already folds derived output (denovo.sql) into the same
commit. Running --fix there keeps prose and data consistent in every single
commit, which a nightly job cannot: it would push prose-only commits after the
fact and join the push race that .github/actions/commit-refreshed-db exists to
solve. The hook is opt-in per clone (`git config core.hooksPath .githooks`), so
CI runs --check as the backstop for anyone who has not enabled it.

WHAT IS DELIBERATELY NOT REGISTERED HERE: numbers that record a past
observation rather than describe the present. "alters none of the other 240"
and "6 of 7 carry the print date" are findings from a verification run, and
"that list was empty as of 2026-08-31" is dated on purpose. Rewriting those to
today's value would silently falsify the record, which is worse than letting it
age. That is why this is a curated registry and not a regex that hunts for
digits.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "denovo.db"

HAS_ID = ("COALESCE(orcid,'')<>'' OR COALESCE(openalex_id,'')<>'' "
          "OR COALESCE(scholar_id,'')<>'' OR COALESCE(sciprofiles_id,'')<>''")

# (file, label, regex with ONE capture group around the number, SQL returning it)
CLAIMS: list[tuple[str, str, str, str]] = [
    ("CLAUDE.md", "tables",
     r"\*\*(\d+) tables and one view\.\*\*",
     "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
     "AND name NOT LIKE 'sqlite_%'"),
    ("CLAUDE.md", "authors with an external id",
     r"; (\d+) of \d+ authors have at least one",
     f"SELECT COUNT(*) FROM author WHERE {HAS_ID}"),
    ("CLAUDE.md", "authors total",
     r"; \d+ of (\d+) authors have at least one",
     "SELECT COUNT(*) FROM author"),
    ("CLAUDE.md", "abstracts present",
     r"Coverage is (\d+)/\d+", 
     "SELECT COUNT(*) FROM publication WHERE COALESCE(abstract,'')<>''"),
    ("CLAUDE.md", "publications total (abstract coverage)",
     r"Coverage is \d+/(\d+)", "SELECT COUNT(*) FROM publication"),
    ("CLAUDE.md", "abstracts missing",
     r"The (\d+) without one are mostly theses",
     "SELECT COUNT(*) FROM publication WHERE COALESCE(abstract,'')=''"),
    ("CLAUDE.md", "subdomain values in use",
     r"rows \((\d+) values in use",
     "SELECT COUNT(DISTINCT subdomain) FROM algorithm "
     "WHERE COALESCE(subdomain,'')<>''"),
    ("CLAUDE.md", "rows dated day 01",
     r"(\d+) rows use day",
     "SELECT COUNT(*) FROM publication WHERE publication_date LIKE '%-01'"),
    ("CLAUDE.md", "day-01 rows in non-January months",
     r"and (\d+) of those are in non-January months",
     "SELECT COUNT(*) FROM publication WHERE publication_date LIKE '%-01' "
     "AND substr(publication_date,6,2)<>'01'"),
    ("WATCHLIST.md", "algorithm rows",
     r"Every one of the (\d+) `algorithm` rows",
     "SELECT COUNT(*) FROM algorithm"),
    ("WATCHLIST.md", "review entries",
     r"All (\d+) existing review entries",
     "SELECT COUNT(*) FROM algorithm WHERE kind='review'"),
]

# publication_type is one sentence listing every type with its count.
CLAIMS += [
    ("CLAUDE.md", f"publication_type {t!r}",
     r"`'" + re.escape(t) + r"'` \((\d+)",
     f"SELECT COUNT(*) FROM publication WHERE publication_type='{t}'")
    for t in ("peer-reviewed", "preprint", "thesis", "ML conference",
              "resource", "postprint", "commentary")
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true",
                        help="rewrite stale numbers in place (default: report only)")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing when everything already agrees")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"error: {DB_PATH} not found", file=sys.stderr)
        return 2
    db = sqlite3.connect(DB_PATH)

    texts: dict[str, str] = {}
    stale: list[tuple[str, str, str, str]] = []
    missing: list[tuple[str, str]] = []

    for fname, label, pattern, query in CLAIMS:
        path = Path(__file__).parent / fname
        if fname not in texts:
            texts[fname] = path.read_text(encoding="utf-8")
        found = list(re.finditer(pattern, texts[fname]))
        if len(found) != 1:
            # A claim that no longer matches is a failure, not a pass: the prose
            # was reworded and this registry entry now silently checks nothing.
            missing.append((f"{fname}: {label}",
                            f"pattern matched {len(found)} times, expected 1"))
            continue
        m = found[0]
        actual = str(db.execute(query).fetchone()[0])
        if m.group(1) != actual:
            stale.append((fname, label, m.group(1), actual))
            if args.fix:
                s = texts[fname]
                texts[fname] = (s[:m.start(1)] + actual + s[m.end(1):])

    for name, why in missing:
        print(f"  UNMATCHED  {name}: {why}")
    for fname, label, was, now in stale:
        verb = "updated" if args.fix else "STALE  "
        print(f"  {verb}    {fname}: {label}: {was} -> {now}")

    if args.fix and stale:
        for fname in {f for f, _, _, _ in stale}:
            (Path(__file__).parent / fname).write_text(texts[fname], encoding="utf-8")
        print(f"fixed {len(stale)} of {len(CLAIMS)} counts in "
              f"{len({f for f, _, _, _ in stale})} file(s)")
        return 1 if missing else 0

    if not stale and not missing:
        if not args.quiet:
            print(f"all {len(CLAIMS)} documented counts agree with denovo.db")
        return 0

    if not args.fix:
        print(f"\n{len(stale)} stale, {len(missing)} unmatched, of "
              f"{len(CLAIMS)} documented counts.")
        print("Run `uv run python check_counts.py --fix` to update them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

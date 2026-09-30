#!/usr/bin/env python3
"""Keep the counts quoted in the docs, config and source comments honest.

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

NOT JUST MARKDOWN. Comments in _quarto.yml, publish.yml, build_pages.py,
build_candidates.py and slugs.py argue from counts too, and rot the same way:
an audit in September 2026 found "~1969 entity pages" in three files when the
generator was emitting 2406, and "512 affiliation rows collapse to 335
institutions" when it was 618 and 393. A number nothing checks is a number
nothing maintains, wherever it lives. Any file in the repo can carry a claim.

A claim's value can be a SQL string or a callable taking the connection, for
the cases where re-deriving the number in SQL would duplicate logic that
already exists: the generated-page total comes from slugs.all_slugs, the same
function build_pages.py and index.qmd use, so the three can never disagree.
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

def _generated_pages(db) -> int:
    """One page per slug, from the module build_pages.py itself uses."""
    from slugs import all_slugs
    tables = all_slugs(db)
    tables.pop("__fallbacks__", None)
    return sum(len(v) for v in tables.values())


# (file, label, regex with ONE capture group around the number,
#  SQL string OR a callable(db) returning it)
CLAIMS: list[tuple[str, str, str, object]] = [
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
    # Counts quoted in config and source comments, not just the two docs.
    (".github/workflows/publish.yml", "entity pages (render time)",
     r"it builds ~(\d+) entity pages", _generated_pages),
    (".github/workflows/publish.yml", "entity pages (Quarto pin)",
     r"~(\d+) pages at once", _generated_pages),
    ("_quarto.yml", "entity pages (navbar)",
     r"the ~(\d+) generated entity pages", _generated_pages),
    ("build_pages.py", "entity pages (search.json)",
     r"keeps ~(\d+) thin pages out of", _generated_pages),
    ("build_pages.py", "algorithms with exactly one paper",
     r"(\d+) of \d+ algorithms have exactly",
     "SELECT COUNT(*) FROM (SELECT algorithm_id FROM publication_algorithm "
     "GROUP BY algorithm_id HAVING COUNT(*)=1)"),
    ("build_pages.py", "algorithms total",
     r"\d+ of (\d+) algorithms have exactly", "SELECT COUNT(*) FROM algorithm"),
    ("slugs.py", "affiliation rows",
     r"(\d+) affiliation rows", "SELECT COUNT(*) FROM affiliation"),
    ("slugs.py", "distinct institutions",
     r"collapse to (\d+) institutions",
     "SELECT COUNT(DISTINCT name) FROM affiliation"),
    ("build_candidates.py", "publication_citation rows",
     r"0 of its (\d+) rows point outside",
     "SELECT COUNT(*) FROM publication_citation"),
    ("build_candidates.py", "publications scored against",
     r"our (\d+) publications link", "SELECT COUNT(*) FROM publication"),
    ("CLAUDE.md", "publication_algorithm 'uses' links",
     r"(\d+) of the 408 are `'uses'`",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("CLAUDE.md", "publication_algorithm links total",
     r"\d+ of the (\d+) are `'uses'`",
     "SELECT COUNT(*) FROM publication_algorithm"),
    ("CLAUDE.md", "PEAKS papers that only use it",
     r"(\d+) of PEAKS's 21 papers",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses' "
     "AND algorithm_id=(SELECT id FROM algorithm WHERE name='PEAKS')"),
    ("CLAUDE.md", "PEAKS papers in total",
     r"\d+ of PEAKS's (\d+) papers",
     "SELECT COUNT(*) FROM publication_algorithm "
     "WHERE algorithm_id=(SELECT id FROM algorithm WHERE name='PEAKS')"),
    ("index.qmd", "publication_algorithm 'uses' links",
     r"(\d+) of 408 publication_algorithm links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("index.qmd", "publication_algorithm links total",
     r"\d+ of (\d+) publication_algorithm links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm"),
    ("build_pages.py", "publication_algorithm 'uses' links",
     r"(\d+) of 408 links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("build_pages.py", "publication_algorithm links total",
     r"\d+ of (\d+) links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm"),
    # Public benchmarks. These move whenever the upstream repository adds a
    # tool or a dataset, which is exactly why they are registered.
    ("CLAUDE.md", "benchmark tools",
     r"which runs (\d+) tools in their own containers",
     "SELECT COUNT(*) FROM benchmark_tool"),
    ("CLAUDE.md", "benchmark datasets",
     r"containers over (\d+) public datasets",
     "SELECT COUNT(*) FROM benchmark_dataset"),
    ("CLAUDE.md", "benchmark_dataset rows",
     r"`benchmark_dataset` \((\d+) rows\)",
     "SELECT COUNT(*) FROM benchmark_dataset"),
    ("CLAUDE.md", "benchmark_tool rows",
     r"`benchmark_tool` \((\d+)\)",
     "SELECT COUNT(*) FROM benchmark_tool"),
    ("CLAUDE.md", "benchmark_result rows",
     r"`benchmark_result`\n\((\d+) = one per tool",
     "SELECT COUNT(*) FROM benchmark_result"),
    ("CLAUDE.md", "benchmark_curve rows",
     r"`benchmark_curve` \((\d+) = a",
     "SELECT COUNT(*) FROM benchmark_curve"),
    ("CLAUDE.md", "benchmark rows on the latest version",
     r"disagree on \d+ of (\d+) \(tool, dataset\) pairs",
     "SELECT COUNT(*) FROM benchmark_result WHERE is_latest = 1"),
    ("CLAUDE.md", "proteobench submissions",
     r"`proteobench_submission`\n\((\d+) rows\)",
     "SELECT COUNT(*) FROM proteobench_submission"),
    ("CLAUDE.md", "proteobench metric rows",
     r"`proteobench_metric` \((\d+) = one per submission",
     "SELECT COUNT(*) FROM proteobench_metric"),
    ("CLAUDE.md", "proteobench runs (prose)",
     r"peptide-level precision of the (\w+) runs",
     lambda db: ("one two three four five six seven eight nine ten".split()
                 [db.execute("SELECT COUNT(*) FROM proteobench_submission")
                    .fetchone()[0] - 1])),
    ("CLAUDE.md", "methods with a benchmark section",
     r"The (\d+) methods with benchmark results carry",
     "SELECT COUNT(DISTINCT algorithm_id) FROM benchmark_tool "
     "WHERE algorithm_id IS NOT NULL AND n_datasets = "
     "(SELECT COUNT(*) FROM benchmark_dataset)"),
    ("CLAUDE.md", "methods with a proteobench submission",
     r"precision for the (\d+) that have a submission",
     "SELECT COUNT(DISTINCT algorithm_id) FROM proteobench_submission "
     "WHERE algorithm_id IS NOT NULL"),
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
              "resource", "postprint", "commentary", "abstract")
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true",
                        help="rewrite stale numbers in place (default: report only)")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing when everything already agrees")
    parser.add_argument("--list-files", action="store_true",
                        help="print every file the registry can rewrite, one per "
                             "line, so the pre-commit hook can re-stage them")
    args = parser.parse_args()

    if args.list_files:
        for fname in dict.fromkeys(c[0] for c in CLAIMS):
            print(fname)
        return 0

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
        actual = str(query(db) if callable(query) else db.execute(query).fetchone()[0])
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

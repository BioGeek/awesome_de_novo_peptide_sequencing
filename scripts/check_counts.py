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

DB_PATH = Path(__file__).resolve().parent.parent / "denovo.db"

HAS_ID = ("COALESCE(orcid,'')<>'' OR COALESCE(openalex_id,'')<>'' "
          "OR COALESCE(scholar_id,'')<>'' OR COALESCE(sciprofiles_id,'')<>''")

def _instanovo_median_ap(db) -> str:
    """Median of InstaNovo's per-dataset peptide AP, to three decimals.

    A real median rather than an index into the sorted list: with an even
    number of datasets it is the mean of the two middle values, and the dataset
    count grows.
    """
    aps = sorted(r[0] for r in db.execute(
        "SELECT r.ap_peptide FROM benchmark_result r "
        "JOIN benchmark_tool t ON t.tool = r.tool "
        "JOIN algorithm a ON a.id = t.algorithm_id "
        "WHERE r.is_latest = 1 AND a.name = 'InstaNovo' "
        "  AND r.ap_peptide IS NOT NULL"))
    if not aps:
        return "n/a"
    mid = len(aps) // 2
    med = aps[mid] if len(aps) % 2 else (aps[mid - 1] + aps[mid]) / 2
    return f"{med:.3f}"


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
     r"\*\*(\d+) tables and (?:one|two|three) views?\.\*\*",
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
    ("CLAUDE.md", "rows dated day 01",
     r"(\d+) rows use day",
     "SELECT COUNT(*) FROM publication WHERE publication_date LIKE '%-01'"),
    ("CLAUDE.md", "day-01 rows in non-January months",
     r"and (\d+) of those are in non-January months",
     "SELECT COUNT(*) FROM publication WHERE publication_date LIKE '%-01' "
     "AND substr(publication_date,6,2)<>'01'"),
    ("CLAUDE.md", "datasets",
     r"\*\*(\d+) datasets, \d+ versions", "SELECT COUNT(*) FROM dataset"),
    ("CLAUDE.md", "dataset versions",
     r"\*\*\d+ datasets, (\d+) versions", "SELECT COUNT(*) FROM dataset_version"),
    ("CLAUDE.md", "dataset addresses",
     r"versions, (\d+) addresses", "SELECT COUNT(*) FROM dataset_address"),
    ("CLAUDE.md", "publication_dataset rows",
     r"addresses, (\d+) publication links", "SELECT COUNT(*) FROM publication_dataset"),
    ("CLAUDE.md", "publications with a dataset",
     r"publication links over (\d+) papers",
     "SELECT COUNT(DISTINCT publication_id) FROM publication_dataset"),
    ("CLAUDE.md", "versions with no address",
     r"one\.\*\* (\d+) of \d+ versions have none",
     "SELECT COUNT(*) FROM dataset_version v WHERE NOT EXISTS"
     "(SELECT 1 FROM dataset_address WHERE dataset_version_id=v.id)"),
    ("CLAUDE.md", "versions total (no-address context)",
     r"one\.\*\* \d+ of (\d+) versions have none", "SELECT COUNT(*) FROM dataset_version"),
    ("CLAUDE.md", "ProteomeTools versions",
     r"\*\*ProteomeTools has (\w+) versions",
     lambda db: {2:"two",3:"three",4:"four",5:"five",6:"six",7:"seven",8:"eight"}.get(
         db.execute("SELECT COUNT(*) FROM dataset_version WHERE dataset_id="
                    "(SELECT id FROM dataset WHERE name='ProteomeTools')").fetchone()[0])),
    # BOTH OF THESE MUST NAME THE DATASET, not just the version label. A
    # version label is unique only within its dataset, and two datasets
    # legitimately share one here: the seven-species benchmark's earliest
    # version is also called 'original (DeepNovo, 2017)', because DeepNovo
    # introduced both. Unscoped, adding it silently moved this count from 16 to
    # 17 and --fix rewrote the prose to match, which is the registry asserting
    # something untrue about the nine-species benchmark.
    ("CLAUDE.md", "nine-species naming the original",
     r"benchmark; (\d+) name the original", "SELECT COUNT(*) FROM publication_dataset pd "
     "JOIN dataset_version v ON v.id=pd.dataset_version_id "
     "JOIN dataset d ON d.id=v.dataset_id "
     "WHERE d.name='Nine-species benchmark' AND v.version='original (DeepNovo, 2017)'"),
    ("CLAUDE.md", "nine-species naming the revised",
     r"name the original, (\d+) the revised", "SELECT COUNT(*) FROM publication_dataset pd "
     "JOIN dataset_version v ON v.id=pd.dataset_version_id "
     "JOIN dataset d ON d.id=v.dataset_id "
     "WHERE d.name='Nine-species benchmark' AND v.version LIKE 'revised%'"),
    ("CLAUDE.md", "nine-species papers",
     r"(\d+) papers use the\s+nine-species benchmark",
     "SELECT COUNT(*) FROM publication_dataset WHERE dataset_id="
     "(SELECT id FROM dataset WHERE name='Nine-species benchmark')"),
    ("CLAUDE.md", "nine-species, version not stated",
     r"and \*\*(\d+) print only a per-species provenance accession\*\*",
     "SELECT COUNT(*) FROM publication_dataset WHERE dataset_version_id IS NULL AND dataset_id="
     "(SELECT id FROM dataset WHERE name='Nine-species benchmark')"),
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
    ("scripts/build_pages.py", "entity pages (search.json)",
     r"keeps ~(\d+) thin pages out of", _generated_pages),
    ("scripts/build_pages.py", "algorithms with exactly one paper",
     r"(\d+) of \d+ algorithms have exactly",
     "SELECT COUNT(*) FROM (SELECT algorithm_id FROM publication_algorithm "
     "GROUP BY algorithm_id HAVING COUNT(*)=1)"),
    ("scripts/build_pages.py", "algorithms total",
     r"\d+ of (\d+) algorithms have exactly", "SELECT COUNT(*) FROM algorithm"),
    ("scripts/slugs.py", "affiliation rows",
     r"(\d+) affiliation rows", "SELECT COUNT(*) FROM affiliation"),
    ("scripts/slugs.py", "distinct institutions",
     r"collapse to (\d+) institutions",
     "SELECT COUNT(DISTINCT name) FROM affiliation"),
    ("scripts/build_candidates.py", "publication_citation rows",
     r"0 of its (\d+) rows point outside",
     "SELECT COUNT(*) FROM publication_citation"),
    ("scripts/build_candidates.py", "publications scored against",
     r"our (\d+) publications link", "SELECT COUNT(*) FROM publication"),
    ("CLAUDE.md", "publication_algorithm 'uses' links",
     r"(\d+) of the \d+ are `'uses'`",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("CLAUDE.md", "publication_algorithm links total",
     r"\d+ of the (\d+) are `'uses'`",
     "SELECT COUNT(*) FROM publication_algorithm"),
    ("CLAUDE.md", "PEAKS papers that only use it",
     r"(\d+) of PEAKS's \d+ papers",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses' "
     "AND algorithm_id=(SELECT id FROM algorithm WHERE name='PEAKS')"),
    ("CLAUDE.md", "PEAKS papers in total",
     r"\d+ of PEAKS's (\d+) papers",
     "SELECT COUNT(*) FROM publication_algorithm "
     "WHERE algorithm_id=(SELECT id FROM algorithm WHERE name='PEAKS')"),
    ("index.qmd", "publication_algorithm 'uses' links",
     r"(\d+) of \d+ publication_algorithm links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("index.qmd", "publication_algorithm links total",
     r"\d+ of (\d+) publication_algorithm links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm"),
    ("scripts/build_pages.py", "publication_algorithm 'uses' links",
     r"(\d+) of \d+ links are 'uses'",
     "SELECT COUNT(*) FROM publication_algorithm WHERE role='uses'"),
    ("scripts/build_pages.py", "publication_algorithm links total",
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
    ("CLAUDE.md", "highest publication id",
     r"-- (\d+) against \d+ publications today",
     "SELECT MAX(id) FROM publication"),
    ("CLAUDE.md", "publications behind that id",
     r"-- \d+ against (\d+) publications today",
     "SELECT COUNT(*) FROM publication"),
    ("CLAUDE.md", "application areas",
     r"table \((\d+) of them\)",
     "SELECT COUNT(*) FROM subdomain"),
    # An invariant written as the number it should always be. Both of these
    # read 0, and a claim that can only ever read 0 is exactly the point: the
    # hook fails the commit the moment a method claims an unregistered area or
    # a registered area stops being used.
    ("CLAUDE.md", "unregistered application areas",
     r"(\d+) areas unregistered",
     "SELECT COUNT(*) FROM (SELECT DISTINCT subdomain FROM algorithm "
     "WHERE COALESCE(subdomain,'') <> '' EXCEPT SELECT name FROM subdomain)"),
    ("CLAUDE.md", "registered but unused application areas",
     r"(\d+) registered but unused",
     "SELECT COUNT(*) FROM (SELECT name FROM subdomain "
     "EXCEPT SELECT DISTINCT subdomain FROM algorithm "
     "WHERE COALESCE(subdomain,'') <> '')"),
    # The family-page threshold, from four angles. These are the numbers that
    # justify not giving all 49 families a page, so a drift in any of them is a
    # drift in the argument.
    ("CLAUDE.md", "families with a page",
     r"\*\*(\d+) of \d+\*\* families that hold two or more",
     "SELECT COUNT(*) FROM (SELECT 1 FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) >= 2)"),
    ("CLAUDE.md", "families in all",
     r"\*\*\d+ of (\d+)\*\* families that hold two or more",
     "SELECT COUNT(DISTINCT algorithm_family) FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> ''"),
    ("CLAUDE.md", "single-method families",
     r"The other \*\*(\d+)\*\* hold\s*\n?exactly one method",
     "SELECT COUNT(*) FROM (SELECT 1 FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) = 1)"),
    ("CLAUDE.md", "methods in a family with a page",
     r"cover \*\*(\d+)\*\* of the \*\*\d+\*\*\s*\n?methods that carry a family",
     "SELECT COUNT(*) FROM algorithm WHERE algorithm_family IN "
     "(SELECT algorithm_family FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) >= 2)"),
    ("CLAUDE.md", "methods that carry a family",
     r"cover \*\*\d+\*\* of the \*\*(\d+)\*\*\s*\n?methods that carry a family",
     "SELECT COUNT(*) FROM algorithm WHERE COALESCE(algorithm_family,'') <> ''"),
    ("CLAUDE.md", "families with a note",
     r"All (\d+) families with a page have a note",
     "SELECT COUNT(*) FROM family_note"),
    # Two invariants that must read zero, the same shape as the subdomain pair
    # above: a family page with no note falls back silently to the generated
    # sentence, and a note for a family with no page is prose nothing renders.
    ("CLAUDE.md", "family pages without a note",
     r"(\d+) pages without a note",
     "SELECT COUNT(*) FROM (SELECT algorithm_family f FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' GROUP BY algorithm_family "
     "HAVING COUNT(*) >= 2) x WHERE x.f NOT IN (SELECT name FROM family_note)"),
    ("CLAUDE.md", "notes without a family page",
     r"(\d+) notes without a page",
     "SELECT COUNT(*) FROM family_note WHERE name NOT IN "
     "(SELECT algorithm_family FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' GROUP BY algorithm_family "
     "HAVING COUNT(*) >= 2)"),
    # The same threshold argued in three source comments, because each of the
    # three files repeats the HAVING clause and each comment says why.
    ("scripts/slugs.py", "single-method families",
     r"(\d+) of \d+ families hold exactly one method",
     "SELECT COUNT(*) FROM (SELECT 1 FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) = 1)"),
    ("scripts/slugs.py", "families in all",
     r"\d+ of (\d+) families hold exactly one method",
     "SELECT COUNT(DISTINCT algorithm_family) FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> ''"),
    ("index.qmd", "families with a page",
     r"(\d+) of \d+ families have a page of their own",
     "SELECT COUNT(*) FROM (SELECT 1 FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) >= 2)"),
    ("index.qmd", "families in all",
     r"\d+ of (\d+) families have a page of their own",
     "SELECT COUNT(DISTINCT algorithm_family) FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> ''"),
    ("index.qmd", "single-method families",
     r"the other (\d+) hold exactly one method",
     "SELECT COUNT(*) FROM (SELECT 1 FROM algorithm "
     "WHERE COALESCE(algorithm_family,'') <> '' "
     "GROUP BY algorithm_family HAVING COUNT(*) = 1)"),
    ("BENCHMARKS.md", "denovo_benchmarks datasets",
     r"(\d+) datasets: instruments, organisms",
     "SELECT COUNT(*) FROM benchmark_dataset"),
    ("BENCHMARKS.md", "ProteoBench spectra",
     r"nine-species benchmark, ([\d,]+) spectra",
     lambda db: f"{db.execute('SELECT MAX(n_spectra) FROM proteobench_submission').fetchone()[0]:,}"),
    ("BENCHMARKS.md", "InstaNovo median AP",
     r"(0\.\d+) median AP over \d+ datasets", _instanovo_median_ap),
    ("BENCHMARKS.md", "datasets behind that median",
     r"0\.\d+ median AP over (\d+) datasets",
     "SELECT COUNT(*) FROM benchmark_dataset"),
    # A deposit happens once. Two DEPOSITING PAPERS is fine when they are a
    # preprint and its version of record; more than one WORK is a role error,
    # and this read 1 after a duplicate-dataset fold-in silently promoted every
    # citing paper to 'introduces'. Must stay 0.
    ("CLAUDE.md", "versions with several introducing works",
     r"must read zero, and\s+does: \*\*(\d+)\*\* dataset versions",
     """WITH canon AS (
          SELECT pd.dataset_version_id AS vid,
                 COALESCE((SELECT pv.published_id FROM publication_version pv
                            WHERE pv.preprint_id = pd.publication_id), pd.publication_id) AS work
            FROM publication_dataset pd
           WHERE pd.role='introduces' AND pd.dataset_version_id IS NOT NULL)
        SELECT COUNT(*) FROM (SELECT vid FROM canon GROUP BY vid HAVING COUNT(DISTINCT work) > 1)"""),
    # The mined comparison tables. Counts first, then three invariants that
    # must read zero: each is a guard of the miner restated in SQL, so a
    # loosened guard fails the commit rather than shipping.
    ("CLAUDE.md", "verified comparison tables",
     r"\*\*(\d+) verified tables from \d+ papers",
     "SELECT COUNT(*) FROM paper_comparison WHERE review_status='verified'"),
    ("CLAUDE.md", "papers with a verified comparison table",
     r"verified tables from (\d+) papers",
     "SELECT COUNT(DISTINCT publication_id) FROM paper_comparison "
     "WHERE review_status='verified'"),
    ("CLAUDE.md", "comparison measurements",
     r"papers, (\d+) measurements\.\*\*",
     "SELECT COUNT(*) FROM paper_comparison_result"),
    ("CLAUDE.md", "rejected comparison tables",
     r"The (\d+) refusals the\s+reviewer confirmed",
     "SELECT COUNT(*) FROM paper_comparison WHERE review_status='rejected'"),
    ("CLAUDE.md", "verified tables without the paper's own method",
     r"(\d+) verified tables without a result for the paper's own method",
     "SELECT COUNT(*) FROM paper_comparison c WHERE review_status='verified' "
     "AND NOT EXISTS (SELECT 1 FROM paper_comparison_result r "
     "WHERE r.comparison_id = c.id AND r.is_self = 1)"),
    ("CLAUDE.md", "duplicate measurements",
     r"(\d+) duplicate\s+measurements within a table",
     "SELECT COUNT(*) FROM (SELECT 1 FROM paper_comparison_result "
     "GROUP BY comparison_id, algorithm_id, COALESCE(variant_printed,''), metric, "
     "level, COALESCE(subset_printed,''), basis HAVING COUNT(*) > 1)"),
    ("CLAUDE.md", "methods with a Reported comparisons section",
     r"\*\*(\d+) methods carry a `## Reported comparisons` section\*\*",
     # kind = 'comparison' only: an own-results table compares nothing and is
     # kept off the method pages, so InstaNovo and InstaNovo+, whose only
     # tables are their own results, carry no such section.
     "SELECT COUNT(DISTINCT r.algorithm_id) FROM paper_comparison_result r "
     "JOIN paper_comparison c ON c.id = r.comparison_id "
     "WHERE r.is_self = 1 AND c.review_status = 'verified' AND c.kind = 'comparison'"),
    ("CLAUDE.md", "results with a subset but no canonical subset",
     r"(\d+) results with a printed subset and no canonical one",
     "SELECT COUNT(*) FROM paper_comparison_result "
     "WHERE COALESCE(subset_printed,'') <> '' AND COALESCE(subset_canonical,'') = ''"),
    ("CLAUDE.md", "quoted results without a cue",
     r"(\d+) quoted results without a cue",
     "SELECT COUNT(*) FROM paper_comparison_result "
     "WHERE basis='quoted' AND basis_cue IS NULL"),
    ("CLAUDE.md", "methods with a checkpoint",
     r"The (\d+) methods with a recorded checkpoint",
     "SELECT COUNT(DISTINCT algorithm_id) FROM checkpoint"),
    ("CLAUDE.md", "checkpoints recorded",
     r"Measured over the (\d+) recorded checkpoints", "SELECT COUNT(*) FROM checkpoint"),
    ("WATCHLIST.md", "review entries",
     r"All (\d+) existing review entries",
     "SELECT COUNT(*) FROM algorithm WHERE kind='review'"),
]

# scripts/README.md describes every script in this folder under a heading that
# is its filename. Not a database count: these read the folder itself, so a
# script added without an entry, or an entry left behind by a deleted script,
# fails the commit like any stale number would.
_SCRIPTS = Path(__file__).resolve().parent


def _script_files(_conn) -> set[str]:
    return {p.name for p in _SCRIPTS.glob("*.py")}


def _readme_entries(_conn) -> set[str]:
    text = (_SCRIPTS / "README.md").read_text(encoding="utf-8")
    return set(re.findall(r"^### `([^`]+\.py)`", text, flags=re.M))


CLAIMS += [
    ("scripts/README.md", "scripts in the folder",
     r"This folder\s+holds \*\*(\d+) scripts\*\*",
     lambda c: len(_script_files(c))),
    ("scripts/README.md", "scripts without a README entry",
     r"\*\*(\d+) scripts\*\* lack an entry below",
     lambda c: len(_script_files(c) - _readme_entries(c))),
    ("scripts/README.md", "README entries naming no script",
     r"\*\*(\d+) entries\*\* name a script that does not\s+exist",
     lambda c: len(_readme_entries(c) - _script_files(c))),
]


# INVARIANTS: claims whose prose states the value the data MUST have, always
# zero. --fix rewrites an ordinary count to match the data; doing that to an
# invariant turns a failing guard into a passing sentence, and it did: the
# Protease strategy family gained a page with no note, and --fix rewrote
# "0 pages without a note" to "1" in the same commit. So a violated invariant
# is never rewritten. It prints VIOLATED and fails the run, --fix or not,
# which fails the pre-commit hook. The fix is to the DATA.
INVARIANTS = frozenset({
    "unregistered application areas",
    "registered but unused application areas",
    "family pages without a note",
    "notes without a family page",
    "versions with several introducing works",
    "verified tables without the paper's own method",
    "duplicate measurements",
    "results with a subset but no canonical subset",
    "quoted results without a cue",
    "scripts without a README entry",
    "README entries naming no script",
})

# publication_type is one sentence listing every type with its count.
CLAIMS += [
    ("CLAUDE.md", f"publication_type {t!r}",
     r"`'" + re.escape(t) + r"'` \((\d+)",
     f"SELECT COUNT(*) FROM publication WHERE publication_type='{t}'")
    for t in ("peer-reviewed", "preprint", "thesis", "ML conference",
              "resource", "postprint", "commentary", "abstract", "presentation")
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

    # A renamed label would silently drop its claim out of the invariant set.
    unknown = INVARIANTS - {c[1] for c in CLAIMS}
    if unknown:
        print(f"error: INVARIANTS names no claim: {sorted(unknown)}", file=sys.stderr)
        return 2

    texts: dict[str, str] = {}
    stale: list[tuple[str, str, str, str]] = []
    missing: list[tuple[str, str]] = []
    violated: list[tuple[str, str, str]] = []

    for fname, label, pattern, query in CLAIMS:
        path = Path(__file__).resolve().parent.parent / fname
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
        if label in INVARIANTS and actual != "0":
            violated.append((fname, label, actual))
            continue
        if m.group(1) != actual:
            stale.append((fname, label, m.group(1), actual))
            if args.fix:
                s = texts[fname]
                texts[fname] = (s[:m.start(1)] + actual + s[m.end(1):])

    for name, why in missing:
        print(f"  UNMATCHED  {name}: {why}")
    for fname, label, now in violated:
        print(f"  VIOLATED   {fname}: {label} must be 0, data says {now} "
              f"(fix the data; the prose is not rewritten)")
    for fname, label, was, now in stale:
        verb = "updated" if args.fix else "STALE  "
        print(f"  {verb}    {fname}: {label}: {was} -> {now}")

    if args.fix and stale:
        for fname in {f for f, _, _, _ in stale}:
            (Path(__file__).resolve().parent.parent / fname).write_text(texts[fname], encoding="utf-8")
        print(f"fixed {len(stale)} of {len(CLAIMS)} counts in "
              f"{len({f for f, _, _, _ in stale})} file(s)")
        return 1 if missing or violated else 0

    if not stale and not missing and not violated:
        if not args.quiet:
            print(f"all {len(CLAIMS)} documented counts agree with denovo.db")
        return 0

    if not args.fix:
        print(f"\n{len(stale)} stale, {len(missing)} unmatched, "
              f"{len(violated)} violated, of {len(CLAIMS)} documented counts.")
        print("Run `uv run python scripts/check_counts.py --fix` to update them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

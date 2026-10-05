#!/usr/bin/env python3
"""Refresh the public-benchmark results from bittremieuxlab/denovo_benchmarks.

That repository runs ~17 de novo tools, each in its own Apptainer container, over
84 public datasets and commits the evaluation curves to `results/<dataset>/`. It
is the only apples-to-apples comparison the field has: same spectra, same
ground-truth PSMs, same metric code, tools run by someone other than their
authors. This catalog already knows what every method IS; this is where it
learns how they DO.

WHAT IS FETCHED. Two of the four CSVs per dataset:

  results/<dataset>/peptide_precision_plot_data.csv
  results/<dataset>/AA_precision_plot_data.csv

Each row is one (tool, version) run and carries `coverage` and `metric` as
JSON arrays plus `auc`, the area under that precision-coverage curve. The other
two files (spectral angle, retention-time difference) describe prediction
quality rather than sequencing accuracy and are not used here.

FOUR THINGS ARE DERIVED AND STORED, nothing else:

  benchmark_result   one row per (dataset, tool, version): the two AP values.
                     Every version is kept, so a chart can show InstaNovo 1.1.2
                     against 1.2.2, but the site aggregates on the latest.
  benchmark_curve    the DATASET-MACRO-AVERAGED precision-coverage curve per
                     tool and level, on a fixed 101-point coverage grid. Each
                     run's curve is interpolated onto the grid and the grid
                     points are averaged, which is the only correct way to
                     average curves whose own x values differ per run. This is
                     figure 1b/1c of the benchmark manuscript.
  benchmark_dataset  the dataset list with a derived category, used to group the
                     heatmap's columns.
  benchmark_tool     the tool list, its latest version, and the `algorithm.id`
                     it corresponds to in this catalog, which is what lets the
                     charts link a bar to a method page.

WHY "LATEST VERSION" AND NOT "BEST". The upstream visualisation PR keeps the
highest-AUC version per (tool, dataset), which lets a tool pick a different
version on every dataset -- a configuration nobody could run. This keeps the
latest version instead: `_old` suffixes lose to their unsuffixed twin, then a
natural-number comparison decides. It matters less than it sounds: measured, the
two rules disagree on 83 of 1452 (tool, dataset) pairs, with a median AP
difference of 0.0000 and a maximum of 0.2088. Note the upstream dashboard's own
"latest" is `sorted(versions)[-1]`, which picks `12.5_old` over `12.5`.

THE NUMBERS WILL NOT MATCH THE MANUSCRIPT, and that is expected: its figure 1
was drawn over 29 datasets and this runs over the 84 now in the repository, with
newer tool versions. The site quotes the commit it was built from for that
reason.

    python3 scripts/build_benchmarks.py            # skip if the upstream HEAD is unchanged
    python3 scripts/build_benchmarks.py --force    # rebuild anyway
    python3 scripts/build_benchmarks.py --dry-run  # fetch and report, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DB_PATH = HERE / "denovo.db"
CACHE = HERE / ".cache" / "benchmarks"

REPO = "bittremieuxlab/denovo_benchmarks"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"

# The two metric levels, keyed by the file that carries them.
LEVELS = {
    "peptide": "peptide_precision_plot_data.csv",
    "amino acid": "AA_precision_plot_data.csv",
}
# 101 points is 1% of coverage per step: enough to draw a smooth curve and small
# enough that all the curves together stay a reviewable diff in denovo.sql.
GRID = [i / 100 for i in range(101)]

# Upstream folder name -> this catalog's algorithm name, for the tools whose
# folder name is not simply the method's name case-folded. Everything else
# resolves automatically against `algorithm.name` and `algorithm.aliases`.
TOOL_ALIASES = {
    # ASCII spelling of the pi prefix.
    "pi-HelixNovo": "π-HelixNovo",
    "pi-PrimeNovo": "π-PrimeNovo",
    # The container runs the DDA model of BiATNovo; the catalog has one row for
    # the method.
    "biatNovo-DDA": "BiATNovo",
    # Not a name variant but an identification, so it is recorded here rather
    # than as an alias on the algorithm row. The container is DeepNovoV2's code
    # with a `model_gcn` module, and Denovo-GCN is the published method that
    # puts a graph convolutional network on DeepNovo's spectrum graph; it is
    # also the only such row in the catalog.
    "gcnovo": "Denovo-GCN",
    # casanovo-scaling is deliberately absent: it is a scaling experiment over a
    # 24-dataset subset, not a released tool, so it has no catalog row and is
    # left out of the cross-dataset charts (see n_datasets below).
}


def http_get(url: str, *, cache_key: str | None = None, retries: int = 4) -> bytes:
    """GET with a disk cache and a polite retry.

    Everything is fetched at a pinned commit, so a cache hit can never be stale:
    the cache key carries the sha. That also makes a re-run after a failure
    nearly free, which matters at 168 files.
    """
    if cache_key:
        cached = CACHE / cache_key
        if cached.exists():
            return cached.read_bytes()

    headers = {"User-Agent": "awesome-de-novo-peptide-sequencing/build_benchmarks"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries:
                raise SystemExit(f"giving up on {url}: {exc}")
            time.sleep(2 * attempt)

    if cache_key:
        cached = CACHE / cache_key
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(body)
    return body


def head_commit() -> tuple[str, str]:
    """(sha, committer date) of the upstream default branch."""
    data = json.loads(http_get(f"{API}/commits/main"))
    return data["sha"], data["commit"]["committer"]["date"]


def dataset_names(sha: str) -> list[str]:
    data = json.loads(http_get(f"{API}/contents/results?ref={sha}",
                               cache_key=f"{sha[:12]}/_datasets.json"))
    return sorted(e["name"] for e in data if e["type"] == "dir")


def categorize(name: str) -> str:
    """Group a dataset for the heatmap's columns.

    The upstream visualisation PR's `categorize_dataset`, plus three rules that
    empty its `Other` bucket: the six datasets that fell into it are a standard
    protein mix, a TMT experiment and four named by their instrument.
    """
    if name.startswith("9_species"):
        return "9 species"
    if name.startswith("21PTMs"):
        return "21 PTMs"
    if name.startswith("PT_"):
        return "ProteomeTools"
    if name.startswith("LFQ_"):
        return "LFQ"
    if name.startswith("CAMPI"):
        return "CAMPI"
    if "herceptin" in name:
        return "Herceptin"
    if "multiprotease_ptm" in name:
        return "Multiprotease PTM"
    if "multiprotease" in name:
        return "Multiprotease"
    if "mAb" in name:
        return "mAb"
    if "phospho" in name:
        return "Phospho"
    if "HLA" in name or "MHC" in name:
        return "Immunopeptidomics"
    if "single_cell" in name:
        return "Single cell"
    if name in ("animal_invertebrate", "animal_mammal_1", "animal_mammal_2"):
        return "Animal"
    if name in ("plant", "fungus"):
        return "Non-model organism"
    if name.startswith("multitool"):
        return "Multitool"
    if name.startswith("iPRG"):
        return "iPRG"
    if name == "UPS":
        return "UPS"
    if "TMT" in name:
        return "TMT"
    if re.search(r"agilent|astral|timstof|sciex|jurkat", name):
        return "Instrument"
    return "Other"


# The heatmap needs column groups wide enough to carry a header, and 17
# categories over 84 datasets gives groups of one and two. These eight are the
# same idea as the benchmark manuscript's figure 1d axes (instrument, organism,
# digestion, PTMs, synthetic) widened to cover what the repository has now. The
# fine category survives in `benchmark_dataset.category` and in the chart's
# tooltip, so nothing is lost by grouping.
CATEGORY_GROUP = {
    "ProteomeTools":      "Synthetic",
    "9 species":          "Organism",
    "Animal":             "Organism",
    "Non-model organism": "Organism",
    "CAMPI":              "Organism",
    "21 PTMs":            "PTMs",
    "Phospho":            "PTMs",
    "Multiprotease PTM":  "PTMs",
    "TMT":                "PTMs",
    "Herceptin":          "Antibody",
    "mAb":                "Antibody",
    "Instrument":         "Instrument",
    "LFQ":                "Instrument",
    "Multitool":          "Instrument",
    "Multiprotease":      "Digestion",
    "Immunopeptidomics":  "HLA / MHC",
    "iPRG":               "Other",
    "Single cell":        "Other",
    "UPS":                "Other",
}


def version_key(version: str) -> tuple:
    """Sort key picking the newest version.

    `_old` loses to its unsuffixed twin first, then the embedded numbers decide,
    so `bm-1.1.0` beats `bm-1.0.0` and `12.5` beats `12.5_old`. A plain string
    sort gets that last pair backwards, which is what the upstream dashboard
    does.
    """
    return (not version.endswith("_old"),
            tuple(int(n) for n in re.findall(r"\d+", version)),
            version)


def interp(grid: list[float], xs: list[float], ys: list[float]) -> list[float]:
    """Linear interpolation of (xs, ys) onto grid; xs must be ascending.

    Hand-rolled rather than numpy's: this is the only array maths in the script
    and numpy is not otherwise a dependency of the builders.
    """
    out, j, n = [], 0, len(xs)
    for x in grid:
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[-1]:
            out.append(ys[-1])
            continue
        while j + 1 < n and xs[j + 1] < x:
            j += 1
        x0, x1, y0, y1 = xs[j], xs[j + 1], ys[j], ys[j + 1]
        out.append(y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return out


def fetch_level(sha: str, datasets: list[str], filename: str) -> dict:
    """{(dataset, tool, version): (ap, curve_on_grid, precision_at_full)}.

    `precision_at_full` is the curve's own last point, at coverage 1, read from
    the raw points rather than the interpolated grid. What it MEANS differs by
    level, and is fixed by upstream's evaluation/evaluate.py:

    - peptide: the curve runs over every LABELLED spectrum sorted by score,
      with an unanswered spectrum counted as a miss, and its denominator is
      the number of labelled spectra. So the last point is correct peptides
      over all spectra: the papers' 'peptide recall'.
    - amino acid: the curve runs over every PREDICTED residue, so the last
      point is correct residues over predicted residues: the papers'
      'amino-acid precision'.
    """
    csv.field_size_limit(10_000_000)

    def one(dataset: str) -> tuple[str, bytes | None]:
        url = f"{RAW}/{sha}/results/{dataset}/{filename}"
        key = f"{sha[:12]}/{dataset}/{filename}"
        try:
            return dataset, http_get(url, cache_key=key)
        except SystemExit:
            # A dataset that has not been evaluated at this level yet is a gap,
            # not a failure: report it and carry on.
            print(f"  missing: {dataset}/{filename}", file=sys.stderr)
            return dataset, None

    out: dict = {}
    skipped = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for dataset, body in pool.map(one, datasets):
            if body is None:
                continue
            for row in csv.DictReader(body.decode("utf-8").splitlines()):
                tool, version, auc = (row.get("algorithm"), row.get("version"),
                                      row.get("auc"))
                if not tool or not version or not auc:
                    skipped += 1
                    continue
                try:
                    xs = json.loads(row["coverage"])
                    ys = json.loads(row["metric"])
                    ap = float(auc)
                except (ValueError, TypeError, KeyError):
                    skipped += 1
                    continue
                if not xs or not ys or len(xs) != len(ys):
                    # One real case: deepnovo on PT_orbitrap_HLAII_TMT at the
                    # amino-acid level has auc 0.0 and an empty curve. The AP is
                    # still meaningful, the curve is not.
                    out[(dataset, tool, version)] = (ap, None, None)
                    continue
                pairs = sorted(zip(xs, ys))
                out[(dataset, tool, version)] = (
                    ap, interp(GRID, [p[0] for p in pairs], [p[1] for p in pairs]),
                    pairs[-1][1] if pairs[-1][0] >= 0.999 else None)
    if skipped:
        print(f"  {skipped} malformed row(s) skipped in {filename}", file=sys.stderr)
    return out


def resolve_tools(db: sqlite3.Connection, tools: list[str],
                  aliases: dict[str, str] | None = None) -> dict[str, tuple]:
    """tool -> (display_name, algorithm_id or None).

    Matching is by normalised name or alias, with `aliases` (TOOL_ALIASES by
    default) for the names that are not the method's name. An unmatched tool is
    kept with a NULL algorithm_id rather than dropped: the benchmark is worth
    showing even for a tool this catalog has not catalogued, and NULL is visible
    in the audit line the callers print, where a wrong guess would not be.

    build_proteobench.py imports this: both benchmarks name the same methods and
    neither should resolve them its own way.
    """
    aliases = TOOL_ALIASES if aliases is None else aliases
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower().replace("π", "pi"))

    index: dict[str, tuple[int, str]] = {}
    for row in db.execute("SELECT id, name, aliases FROM algorithm"):
        names = [row[1]] + [a.strip() for a in (row[2] or "").split(",") if a.strip()]
        for nm in names:
            index.setdefault(norm(nm), (row[0], row[1]))

    resolved = {}
    for tool in tools:
        target = aliases.get(tool, tool)
        hit = index.get(norm(target))
        resolved[tool] = (hit[1] if hit else tool, hit[0] if hit else None)
    return resolved


SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_dataset (
    name      TEXT PRIMARY KEY,  -- upstream results/<name> folder
    category  TEXT NOT NULL,     -- derived from the name, see categorize()
    cat_group TEXT NOT NULL,     -- the coarser grouping the heatmap labels
    n_tools   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_tool (
    tool         TEXT PRIMARY KEY,  -- upstream algorithms/<tool> folder
    display_name TEXT NOT NULL,     -- this catalog's name for it, where known
    algorithm_id INTEGER REFERENCES algorithm(id) ON DELETE SET NULL,
    version      TEXT NOT NULL,     -- the version the site's numbers come from
    n_datasets   INTEGER NOT NULL   -- < the total means partial coverage
);
CREATE TABLE IF NOT EXISTS benchmark_result (
    dataset    TEXT NOT NULL REFERENCES benchmark_dataset(name) ON DELETE CASCADE,
    tool       TEXT NOT NULL REFERENCES benchmark_tool(tool) ON DELETE CASCADE,
    version    TEXT NOT NULL,
    ap_peptide REAL,              -- area under the peptide precision-coverage curve
    ap_aa      REAL,              -- ... and the amino-acid one
    prec_full_peptide REAL,       -- peptide precision at coverage 1: correct
                                  -- over all spectra, the papers' 'peptide recall'
    prec_full_aa      REAL,       -- amino-acid precision at coverage 1
    -- 1 on the newest version of this tool that ran on this dataset. The rule
    -- lives in version_key() and is not expressible in SQL, so it is applied
    -- once here and the site filters on the flag.
    is_latest  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset, tool, version)
);
CREATE TABLE IF NOT EXISTS benchmark_curve (
    tool      TEXT NOT NULL REFERENCES benchmark_tool(tool) ON DELETE CASCADE,
    level     TEXT NOT NULL,     -- 'peptide' | 'amino acid'
    coverage  REAL NOT NULL,
    precision REAL NOT NULL,     -- dataset-macro-averaged at this coverage
    PRIMARY KEY (tool, level, coverage)
);
CREATE TABLE IF NOT EXISTS benchmark_source (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when the upstream commit is unchanged")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, write nothing")
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS adds no column to a table that already exists.
    have_cols = {r[1] for r in db.execute("PRAGMA table_info(benchmark_result)")}
    for col in ("prec_full_peptide", "prec_full_aa"):
        if col not in have_cols:
            db.execute(f"ALTER TABLE benchmark_result ADD COLUMN {col} REAL")

    sha, commit_date = head_commit()
    have = dict(db.execute("SELECT key, value FROM benchmark_source"))
    if have.get("commit") == sha and not args.force:
        print(f"upstream unchanged at {sha[:12]} ({commit_date}); nothing to do")
        return 0
    print(f"building from {REPO}@{sha[:12]} ({commit_date})")

    datasets = dataset_names(sha)
    levels = {level: fetch_level(sha, datasets, filename)
              for level, filename in LEVELS.items()}
    pep, aa = levels["peptide"], levels["amino acid"]

    keys = sorted(set(pep) | set(aa))
    tools = sorted({t for _d, t, _v in keys})
    print(f"  {len(datasets)} datasets, {len(tools)} tools, {len(keys)} runs")

    # The version each tool is judged on, and the per-dataset rows behind it.
    latest = {}
    for dataset, tool, version in keys:
        cur = latest.get((tool, dataset))
        if cur is None or version_key(version) > version_key(cur):
            latest[(tool, dataset)] = version

    resolved = resolve_tools(db, tools)
    unmapped = [t for t, (_n, aid) in resolved.items() if aid is None]
    if unmapped:
        print(f"  no catalog algorithm for: {', '.join(unmapped)}")

    # Macro-average over datasets, per tool and level, on the shared grid.
    curves: dict[tuple[str, str], list[float]] = {}
    for level, rows in levels.items():
        sums: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for (dataset, tool, version), (_ap, curve, _pf) in rows.items():
            if curve is None or latest.get((tool, dataset)) != version:
                continue
            acc = sums.setdefault(tool, [0.0] * len(GRID))
            for i, y in enumerate(curve):
                acc[i] += y
            counts[tool] = counts.get(tool, 0) + 1
        for tool, acc in sums.items():
            curves[(tool, level)] = [v / counts[tool] for v in acc]

    n_datasets = {tool: sum(1 for (t, _d) in latest if t == tool) for tool in tools}
    per_dataset_tools = {d: sum(1 for (_t, dd) in latest if dd == d) for d in datasets}

    if args.dry_run:
        print("\n  tool                 ver          n  median AP  AP of mean curve")
        for tool in sorted(tools, key=lambda t: -(sum(curves.get((t, "peptide"),
                                                                 [0])) or 0)):
            aps = sorted(pep[(d, tool, v)][0] for (t, d), v in latest.items()
                         if t == tool and (d, tool, v) in pep)
            curve = curves.get((tool, "peptide"))
            area = (sum((curve[i] + curve[i + 1]) / 2 * (GRID[i + 1] - GRID[i])
                        for i in range(len(GRID) - 1)) if curve else float("nan"))
            # A real median: with an even count it is the mean of the two
            # middle values, which is what the site's d3.median reports. The
            # upper-middle value alone read 0.854 where the median is 0.853.
            mid = len(aps) // 2
            med = (float("nan") if not aps
                   else aps[mid] if len(aps) % 2
                   else (aps[mid - 1] + aps[mid]) / 2)
            newest = max((v for (t, _d), v in latest.items() if t == tool),
                         key=version_key, default="?")
            print(f"  {resolved[tool][0]:20s} {newest:12s}"
                  f" {n_datasets[tool]:3d}  {med:9.3f}  {area:16.3f}")
        print("\n--dry-run: nothing written")
        return 0

    with db:
        db.execute("DELETE FROM benchmark_curve")
        db.execute("DELETE FROM benchmark_result")
        db.execute("DELETE FROM benchmark_tool")
        db.execute("DELETE FROM benchmark_dataset")
        db.executemany(
            "INSERT INTO benchmark_dataset (name, category, cat_group, n_tools)"
            " VALUES (?,?,?,?)",
            [(d, categorize(d), CATEGORY_GROUP.get(categorize(d), "Other"),
              per_dataset_tools[d]) for d in datasets])
        db.executemany(
            "INSERT INTO benchmark_tool (tool, display_name, algorithm_id, version,"
            " n_datasets) VALUES (?,?,?,?,?)",
            [(tool, resolved[tool][0], resolved[tool][1],
              max((v for (t, _d), v in latest.items() if t == tool),
                  key=version_key, default="?"),
              n_datasets[tool]) for tool in tools])
        db.executemany(
            "INSERT INTO benchmark_result (dataset, tool, version, ap_peptide,"
            " ap_aa, prec_full_peptide, prec_full_aa, is_latest)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(d, t, v,
              pep[(d, t, v)][0] if (d, t, v) in pep else None,
              aa[(d, t, v)][0] if (d, t, v) in aa else None,
              pep[(d, t, v)][2] if (d, t, v) in pep else None,
              aa[(d, t, v)][2] if (d, t, v) in aa else None,
              1 if latest.get((t, d)) == v else 0)
             for (d, t, v) in keys])
        db.executemany(
            "INSERT INTO benchmark_curve (tool, level, coverage, precision)"
            " VALUES (?,?,?,?)",
            [(tool, level, GRID[i], round(ys[i], 6))
             for (tool, level), ys in sorted(curves.items())
             for i in range(len(GRID))])
        db.executemany(
            "INSERT OR REPLACE INTO benchmark_source (key, value) VALUES (?,?)",
            [("repo", REPO), ("commit", sha), ("commit_date", commit_date),
             ("fetched", time.strftime("%Y-%m-%d", time.gmtime())),
             ("n_datasets", str(len(datasets))), ("n_tools", str(len(tools)))])

    print(f"wrote {len(keys)} results, {len(curves)} curves, {len(tools)} tools, "
          f"{len(datasets)} datasets")
    print("regenerate the dump: sqlite3 denovo.db .dump > denovo.sql")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Refresh the ProteoBench de-novo DDA-HCD results.

ProteoBench (https://proteobench.cubimed.rub.de/denovo_DDA_HCD) is the field's
other public benchmark, and it is a different animal from denovo_benchmarks:
**one** dataset, the published nine-species benchmark of 779,879 spectra, run by
whoever submits, with the parameters they used recorded alongside the numbers.
Submissions are committed as one JSON per run to
github.com/Proteobench/Results_denovo_lfq_DDA_HCD, which is what this reads.

The two benchmarks answer different questions and the site shows both. This one
says "with these settings, on this data, here is where the tool lands"; the
other says "across 84 datasets, here is how the tool holds up".

WHAT EACH JSON CARRIES, and what the four metrics mean. ProteoBench computes
them in proteobench/datapoint/denovo_datapoint.py, and the definitions matter
because three of them are easy to confuse:

  precision  correct / predictions MADE. High precision at low coverage is easy:
             answer only the spectra you are sure about.
  recall      correct / all spectra, which is precision at full coverage with an
             unanswered spectrum counted as wrong.
  coverage    predictions made / all spectra. At the amino-acid level it can
             exceed 1, because the denominator is the ground truth's amino-acid
             count and a prediction may be longer.
  auc         area under the precision-coverage curve, the metric the module's
             own design discussion settled on as the default.

Each is reported at two levels (peptide, amino acid) and for two match
definitions: `mass`, where a residue counts as correct when its mass is within
0.1 Da (so I/L are indistinguishable), and `exact`, which requires the sequence
itself. The 80-point precision-coverage curve behind each AUC is stored too.

    python3 build_proteobench.py            # skip if the upstream HEAD is unchanged
    python3 build_proteobench.py --force    # rebuild anyway
    python3 build_proteobench.py --dry-run  # fetch and report, write nothing
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from build_benchmarks import http_get, resolve_tools

HERE = Path(__file__).parent
DB_PATH = HERE / "denovo.db"

REPO = "Proteobench/Results_denovo_lfq_DDA_HCD"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"
MODULE_URL = "https://proteobench.cubimed.rub.de/denovo_DDA_HCD"

LEVELS = ("peptide", "aa")
MATCHES = ("mass", "exact")

# ProteoBench's submission names against this catalog's algorithm names. Only
# the pi- prefix needs help, and resolve_tools() already folds that; the map is
# here for the next submission that does not match, and to document that an
# unmatched name is kept rather than guessed at.
TOOL_ALIASES: dict[str, str] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS proteobench_submission (
    id           TEXT PRIMARY KEY,  -- ProteoBench's own run id, name + timestamp
    hash         TEXT NOT NULL,     -- intermediate_hash, which names the result file
    software     TEXT NOT NULL,     -- as submitted
    version      TEXT,
    display_name TEXT NOT NULL,     -- this catalog's name for it, where known
    algorithm_id INTEGER REFERENCES algorithm(id) ON DELETE SET NULL,
    decoding     TEXT,              -- decoding_strategy, e.g. 'beam search'
    checkpoint   TEXT,              -- model weights used, where given
    pb_version   TEXT,              -- the ProteoBench version that scored it
    n_spectra    INTEGER,
    submitted    TEXT               -- parsed out of the id, YYYY-MM-DD
);
CREATE TABLE IF NOT EXISTS proteobench_metric (
    submission_id TEXT NOT NULL
        REFERENCES proteobench_submission(id) ON DELETE CASCADE,
    level      TEXT NOT NULL,  -- 'peptide' | 'aa'
    match_type TEXT NOT NULL,  -- 'mass' (within 0.1 Da) | 'exact' (sequence)
    precision  REAL,           -- correct / predictions made
    recall     REAL,           -- correct / all spectra = precision at coverage 1
    coverage   REAL,           -- predictions made / all spectra
    auc        REAL,           -- area under the precision-coverage curve
    PRIMARY KEY (submission_id, level, match_type)
);
CREATE TABLE IF NOT EXISTS proteobench_curve (
    submission_id TEXT NOT NULL
        REFERENCES proteobench_submission(id) ON DELETE CASCADE,
    level      TEXT NOT NULL,
    match_type TEXT NOT NULL,
    i          INTEGER NOT NULL,   -- position along the stored curve
    coverage   REAL NOT NULL,
    precision  REAL NOT NULL,
    PRIMARY KEY (submission_id, level, match_type, i)
);
CREATE TABLE IF NOT EXISTS proteobench_source (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ProteoBench stores up to 500 points per curve (CURVE_STORAGE_MAX_POINTS), which
# is more than a 700 px chart can show and 11,264 rows across the submissions.
CURVE_POINTS = 101


def subsample(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """At most CURVE_POINTS of `points`, evenly strided, first and last kept.

    Strided rather than interpolated onto a shared grid, which is what the other
    benchmark's builder does: these curves do not all start at coverage 0 (one
    tool's amino-acid curve starts at 0.028, which the module's design
    discussion flags), and interpolating onto a grid would invent values below a
    curve's first point instead of leaving the gap visible.
    """
    n = len(points)
    if n <= CURVE_POINTS:
        return points
    step = (n - 1) / (CURVE_POINTS - 1)
    idx = sorted({min(n - 1, round(i * step)) for i in range(CURVE_POINTS)})
    return [points[i] for i in idx]


def head_commit() -> tuple[str, str]:
    data = json.loads(http_get(f"{API}/commits/main"))
    return data["sha"], data["commit"]["committer"]["date"]


def submission_files(sha: str) -> list[str]:
    """The result JSONs at this commit: one per submitted run.

    Everything in the repository root that is not documentation or tooling, so a
    new submission is picked up without this script knowing its name.
    """
    data = json.loads(http_get(f"{API}/contents?ref={sha}",
                               cache_key=f"pb-{sha[:12]}/_files.json"))
    return sorted(e["name"] for e in data
                  if e["type"] == "file" and e["name"].endswith(".json"))


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

    sha, commit_date = head_commit()
    have = dict(db.execute("SELECT key, value FROM proteobench_source"))
    if have.get("commit") == sha and not args.force:
        print(f"upstream unchanged at {sha[:12]} ({commit_date}); nothing to do")
        return 0
    print(f"building from {REPO}@{sha[:12]} ({commit_date})")

    subs, metrics, curves = [], [], []
    for name in submission_files(sha):
        raw = http_get(f"{RAW}/{sha}/{name}", cache_key=f"pb-{sha[:12]}/{name}")
        d = json.loads(raw)
        if d.get("is_temporary"):
            # A run someone was still iterating on when it was written out.
            print(f"  skipping temporary submission {d.get('id')}")
            continue
        run_id = d["id"]
        # The id is "<software>_<YYYYMMDD>_<HHMMSS>"; the date is the only record
        # of when a submission was made, since the JSON carries no timestamp.
        stamp = run_id.rsplit("_", 2)
        submitted = (f"{stamp[-2][:4]}-{stamp[-2][4:6]}-{stamp[-2][6:8]}"
                     if len(stamp) == 3 and len(stamp[-2]) == 8 else None)
        subs.append({
            "id": run_id,
            "hash": d.get("intermediate_hash") or Path(name).stem,
            "software": d["software_name"],
            "version": d.get("software_version") or None,
            "decoding": d.get("decoding_strategy") or None,
            "checkpoint": d.get("checkpoint") or None,
            "pb_version": d.get("proteobench_version") or None,
            "n_spectra": (d.get("results", {}).get("in_depth", {})
                          .get("in_FASTA", {}).get("n_spectra")),
            "submitted": submitted,
        })
        for level in LEVELS:
            for match in MATCHES:
                m = d.get("results", {}).get(level, {}).get(match)
                if not m:
                    continue
                metrics.append((run_id, level, match, m.get("precision"),
                                m.get("recall"), m.get("coverage"), m.get("auc")))
                curve = m.get("curve") or {}
                xs, ys = curve.get("coverage") or [], curve.get("precision") or []
                for i, (x, y) in enumerate(subsample(list(zip(xs, ys)))):
                    curves.append((run_id, level, match, i, x, y))

    resolved = resolve_tools(db, [s["software"] for s in subs], TOOL_ALIASES)
    unmapped = sorted({s["software"] for s in subs if resolved[s["software"]][1] is None})
    if unmapped:
        print(f"  no catalog algorithm for: {', '.join(unmapped)}")

    if args.dry_run:
        print(f"\n  {len(subs)} submissions, {len(metrics)} metric rows, "
              f"{len(curves)} curve points")
        print(f"\n  {'software':22s} {'ver':8s} {'pep prec':>8s} {'pep@cov1':>8s} "
              f"{'pep AUC':>8s} {'aa prec':>8s} {'aa AUC':>8s} {'coverage':>8s}")
        by_id = {}
        for run_id, level, match, prec, rec, cov, auc in metrics:
            if match == "mass":
                by_id.setdefault(run_id, {})[level] = (prec, rec, cov, auc)
        for s in sorted(subs, key=lambda s: -(by_id.get(s["id"], {})
                                              .get("peptide", (0,))[0] or 0)):
            p = by_id.get(s["id"], {}).get("peptide", (None,) * 4)
            a = by_id.get(s["id"], {}).get("aa", (None,) * 4)
            fmt = lambda v: f"{v:8.3f}" if isinstance(v, float) else f"{'-':>8s}"
            print(f"  {resolved[s['software']][0]:22s} {(s['version'] or '-')[:8]:8s} "
                  f"{fmt(p[0])} {fmt(p[1])} {fmt(p[3])} {fmt(a[0])} {fmt(a[3])} {fmt(p[2])}")
        print("\n--dry-run: nothing written")
        return 0

    with db:
        db.execute("DELETE FROM proteobench_curve")
        db.execute("DELETE FROM proteobench_metric")
        db.execute("DELETE FROM proteobench_submission")
        db.executemany(
            "INSERT INTO proteobench_submission (id, hash, software, version,"
            " display_name, algorithm_id, decoding, checkpoint, pb_version,"
            " n_spectra, submitted) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(s["id"], s["hash"], s["software"], s["version"],
              resolved[s["software"]][0], resolved[s["software"]][1],
              s["decoding"], s["checkpoint"], s["pb_version"], s["n_spectra"],
              s["submitted"]) for s in subs])
        db.executemany(
            "INSERT INTO proteobench_metric (submission_id, level, match_type,"
            " precision, recall, coverage, auc) VALUES (?,?,?,?,?,?,?)", metrics)
        db.executemany(
            "INSERT INTO proteobench_curve (submission_id, level, match_type, i,"
            " coverage, precision) VALUES (?,?,?,?,?,?)",
            [(r, l, m, i, round(x, 6), round(y, 6)) for r, l, m, i, x, y in curves])
        db.executemany(
            "INSERT OR REPLACE INTO proteobench_source (key, value) VALUES (?,?)",
            [("repo", REPO), ("commit", sha), ("commit_date", commit_date),
             ("module_url", MODULE_URL),
             ("fetched", time.strftime("%Y-%m-%d", time.gmtime())),
             ("n_submissions", str(len(subs)))])

    print(f"wrote {len(subs)} submissions, {len(metrics)} metric rows, "
          f"{len(curves)} curve points")
    print("regenerate the dump: sqlite3 denovo.db .dump > denovo.sql")
    return 0


if __name__ == "__main__":
    sys.exit(main())

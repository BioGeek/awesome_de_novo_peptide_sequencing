#!/usr/bin/env python3
"""Mine the LOCAL PDF library for public-repository accessions and link them to datasets.

Why this is a builder and not a query. `publication_dataset` says which papers
used which data, and no API will tell you: the accession is in the paper's data
availability statement, in prose, often only in a supplementary table. The PDFs
are already on disk for exactly this kind of question.

What it will and will not do:

- It NEVER creates a `dataset`, `dataset_version` or `dataset_address` row.
  Deciding that a pile of spectra is a named dataset, and which version of it,
  is a judgement call -- the whole point of the three-table split -- so an
  accession this script has not seen before goes to `dataset_candidates.csv`
  for a human, ranked by how many papers cite it.
- With `--write` it creates `publication_dataset` rows, and only for accessions
  already recorded in `dataset_address`. That half IS mechanical: if a paper
  prints `MSV000081382`, it used the nine-species benchmark.

**It must never run in CI.** There is no PDF library on a runner. Same rule as
`build_pdf_library.py`, which also owns the filename-to-publication mapping this
script reads out of `pdf_status.csv` rather than redoing.

The ranking in the candidates CSV is the useful output. Accession reuse is
bimodal: a few are shared resources cited by a dozen papers, and a long tail of
several hundred are deposits cited once, by the paper that made them. That split
is what `dataset.kind` records, and the tail is correctly NOT worth a dataset row
each.

    uv run python build_dataset_accessions.py                # report only
    uv run python build_dataset_accessions.py --write        # link known accessions
    uv run python build_dataset_accessions.py --min-papers 3 # tighter candidate list
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import re
import sqlite3
import subprocess
import sys

DB = pathlib.Path(__file__).with_name("denovo.db")
DEFAULT_DIR = pathlib.Path.home() / "Documents" / "De novo peptide sequencing"
CANDIDATES = pathlib.Path(__file__).with_name("dataset_candidates.csv")

# Keep these anchored on the identifier's own shape. A looser pattern picks up
# figure labels and reference numbers, and an accession that is not real cannot
# be told from one that is without a lookup per hit.
PATTERNS = {
    "PRIDE":         re.compile(r"\bPXD\d{6}\b"),
    "MassIVE":       re.compile(r"\bMSV\d{9}\b"),
    "jPOST":         re.compile(r"\bJPST\d{6}\b"),
    "iProX":         re.compile(r"\bIPX\d{7,}\b"),
    "PeptideAtlas":  re.compile(r"\bPAe\d{6}\b"),
    "Zenodo":        re.compile(r"10\.5281/zenodo\.\d+", re.I),
    "figshare":      re.compile(r"10\.6084/m9\.figshare\.\d+", re.I),
    "Hugging Face":  re.compile(r"huggingface\.co/datasets/([\w.-]+/[\w.-]+)", re.I),
}


def pdf_text(path: pathlib.Path) -> str:
    """pdftotext, collapsed to one line so an accession split over a line break still matches."""
    try:
        out = subprocess.run(["pdftotext", "-q", str(path), "-"],
                             capture_output=True, text=True, timeout=180).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  !! {path.name}: {exc}", file=sys.stderr)
        return ""
    return re.sub(r"\s+", " ", out)


def file_to_publication(con: sqlite3.Connection, library: pathlib.Path) -> dict[str, int]:
    """Call build_pdf_library.py's own matcher rather than matching titles again.

    That matching is the hard part and it is already solved and audited there
    (prefix-against-prefix, cut to the shorter string, with a year tie-break);
    a second copy here would be a worse one that could disagree with the
    filenames on disk.

    Do NOT read the `file` column of `pdf_status.csv` for this, which is the
    obvious-looking shortcut and is wrong: that column is written by `fetch` for
    the files it downloads, so it covers neither hand-filed PDFs nor renames.
    Measured when this script first ran, it named 130 of the 243 files present
    and 82 names that no longer existed, which silently halved the scan.

    Importing build_pdf_library pulls in rapidfuzz, so run this script under
    `uv run python`, not bare python3.
    """
    import build_pdf_library as bpl

    pubs = bpl.load_publications(con)
    out: dict[str, int] = {}
    for pid, paths in bpl.coverage(pubs, library).items():
        for path in paths:
            out[path.name] = pid
    return out


def scan(library: pathlib.Path, only: set[int] | None, pub_of: dict[str, int]):
    pdfs = sorted(p for p in library.glob("*.pdf"))
    hits: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
    for n, pdf in enumerate(pdfs, 1):
        pid = pub_of.get(pdf.name)
        if pid is None or (only and pid not in only):
            continue
        text = pdf_text(pdf)
        for repo, rx in PATTERNS.items():
            for m in rx.finditer(text):
                acc = m.group(1) if rx.groups else m.group(0)
                hits[(repo, acc)].add(pid)
        if n % 50 == 0:
            print(f"  {n}/{len(pdfs)}", file=sys.stderr)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR,
                    help=f"PDF library root (default {DEFAULT_DIR})")
    ap.add_argument("--write", action="store_true",
                    help="create publication_dataset rows for KNOWN accessions")
    ap.add_argument("--ids", help="comma-separated publication ids to restrict the scan to")
    ap.add_argument("--min-papers", type=int, default=2,
                    help="candidate CSV keeps accessions cited by at least this many papers (default 2)")
    args = ap.parse_args()

    if not args.dir.is_dir():
        sys.exit(f"no PDF library at {args.dir}")

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")

    # accession -> (dataset_id, version_id or None). A version_id is only
    # asserted when exactly one NON-provenance address claims the accession: a
    # provenance accession says where spectra came from, not which curated
    # version a paper actually ran on, so it resolves to the dataset with the
    # version left NULL. That NULL is a finding, not a gap.
    known: dict[str, tuple[int, int | None]] = {}
    rows = con.execute("""
        SELECT a.accession, v.dataset_id, v.id, a.is_provenance
          FROM dataset_address a JOIN dataset_version v ON v.id = a.dataset_version_id
    """).fetchall()
    by_acc: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
    for acc, dsid, vid, prov in rows:
        by_acc[acc.split("#")[0]].append((dsid, vid, prov))
    for acc, claims in by_acc.items():
        datasets = {d for d, _, _ in claims}
        direct = [(d, v) for d, v, p in claims if not p]
        if len(datasets) == 1 and len(direct) == 1:
            known[acc] = direct[0]
        elif len(datasets) == 1:
            known[acc] = (next(iter(datasets)), None)
        # An accession claimed by two different datasets is ambiguous and is
        # left out rather than guessed at.

    only = {int(i) for i in args.ids.split(",")} if args.ids else None
    pub_of = file_to_publication(con, args.dir)
    print(f"{len(pub_of)} filed PDFs, {len(known)} known accessions", file=sys.stderr)
    hits = scan(args.dir, only, pub_of)

    introduced = dict(con.execute(
        "SELECT introduced_by, id FROM dataset_version WHERE introduced_by IS NOT NULL").fetchall())

    linked = skipped = 0
    # Several accessions legitimately resolve to the same link: a paper that
    # lists all nine provenance submissions of the nine-species benchmark is one
    # publication_dataset row and nine hits. Without this set the report counts
    # hits while --write counts rows, so the two disagree (187 against 86) and
    # the report is the wrong one.
    seen: set[tuple[int, int, int, str]] = set()
    unknown: list[tuple[int, str, str, str]] = []
    for (repo, acc), pids in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        target = known.get(acc)
        if target is None:
            title = con.execute("SELECT title FROM publication WHERE id=?",
                                (sorted(pids)[0],)).fetchone()
            unknown.append((len(pids), repo, acc,
                            (title[0] if title else "")[:70]))
            continue
        dsid, vid = target
        for pid in sorted(pids):
            role = "introduces" if introduced.get(pid) == vid and vid else "uses"
            key = (pid, dsid, vid if vid is not None else -1, role)
            if key in seen:
                continue
            seen.add(key)
            exists = con.execute("""SELECT 1 FROM publication_dataset
                 WHERE publication_id=? AND dataset_id=? AND IFNULL(dataset_version_id,-1)=?
                   AND role=?""", key).fetchone()
            if exists:
                skipped += 1
                continue
            if args.write:
                con.execute("""INSERT INTO publication_dataset
                    (publication_id,dataset_id,dataset_version_id,role) VALUES(?,?,?,?)""",
                            (pid, dsid, vid, role))
            linked += 1
    if args.write:
        con.commit()

    unknown.sort(reverse=True)
    kept = [u for u in unknown if u[0] >= args.min_papers]
    with CANDIDATES.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["papers", "repository", "accession", "example_paper"])
        w.writerows(kept)

    print()
    print(f"  accessions seen          {len(hits)}")
    print(f"  of those already known   {len(hits) - len(unknown)}")
    print(f"  publication_dataset rows {'written' if args.write else 'proposed'}: {linked}"
          f" ({skipped} already present)")
    print(f"  unknown accessions       {len(unknown)}, "
          f"{len(kept)} cited by >= {args.min_papers} papers -> {CANDIDATES.name}")
    if not args.write:
        print("\nReport only. Re-run with --write to create the links.")
    print("Datasets are never created automatically: review the CSV by hand.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

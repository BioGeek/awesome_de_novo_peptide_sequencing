#!/usr/bin/env python3
"""Propose code repositories for methods that have none, from their own PDFs.

`algorithm_repository` has no writer: every one of its rows was added by hand,
and the daily `build_repo_metrics.py` only refreshes repositories already in it.
So a method added in bulk (the backlog and the citation sweep added ~320) has no
repository link at all, even when its paper prints one.

This reads the LOCAL PDF library, takes every repository URL printed in a
paper that DESCRIBES a method still lacking a repository, and scores each one:

  name    how well the repository's name matches the method's name or aliases
  context whether the sentence around it reads like a code-availability
          statement ("code is available at", "implemented in", "our")
  owner   penalised when the URL is already another method's repository, or a
          well-known library, because a paper cites the tools it USED as well
          as the one it wrote

REPORT-ONLY, like build_candidates.py and build_dataset_accessions.py: it
writes repository_candidates.csv and never touches denovo.db. Deciding that a
URL is a method's own code is a judgement, because a paper's PDF is full of
other people's repositories.

    uv run python build_repository_candidates.py
    uv run python build_repository_candidates.py --ids 512,513   # algorithm ids

Never in CI: it needs the PDF library. It imports build_pdf_library for the
audited filename matcher and rapidfuzz, so run it under `uv run`.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import subprocess
from pathlib import Path

from rapidfuzz import fuzz

import build_pdf_library as bpl

HERE = Path(__file__).parent
OUT = HERE / "repository_candidates.csv"

HOSTS = r"(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|sourceforge\.net/projects|gitee\.com)"
# A URL a line break split arrives as "github.com/owner/ repo" or with the
# break inside a name ("github.com/own- er/repo"). Join those first.
SPLIT = re.compile(rf"({HOSTS}/[\w.-]*[/-])\s+([\w.-]+)")
URL = re.compile(rf"(?:https?://)?(?:www\.)?({HOSTS})/([\w.-]+)(?:/([\w.-]+))?", re.I)
# Libraries and platforms every methods section cites; never a method's own code.
COMMON = re.compile(
    r"^(pytorch|tensorflow|keras-team|huggingface|scikit-learn|numpy|pandas-dev|"
    r"openms|compomics|bigbio|wfondrie|vgteam|biopython|matplotlib|scipy|"
    r"pyteomics|levitsky|deepmind|facebookresearch|google|google-research|"
    r"microsoft|openai|nvidia|anthropics|lightning-ai|pytorchlightning|"
    r"proteomicsstandardsinitiative|hupo-psi|pride-archive|bittremieuxlab|"
    r"proteobench|massive|grosenberger|smith-chem-wisc)$", re.I)
AVAIL = re.compile(r"\b(available|availability|source code|code|implement|"
                   r"download|freely|open[- ]source|repository|software|our)\b", re.I)


def pdf_text(path: Path) -> str:
    try:
        out = subprocess.run(["pdftotext", "-q", str(path), "-"],
                             capture_output=True, text=True, timeout=180).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    text = re.sub(r"\s+", " ", out)
    for _ in range(2):
        text = SPLIT.sub(r"\1\2", text)
    return text


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("π", "pi"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated algorithm ids to restrict to")
    args = ap.parse_args()

    con = sqlite3.connect(HERE / "denovo.db")
    con.row_factory = sqlite3.Row
    pubs = bpl.load_publications(con)
    cov = bpl.coverage(pubs, bpl.DEFAULT_DIR)
    owned: dict[str, list[str]] = {}
    for r in con.execute("SELECT r.url, a.name FROM algorithm_repository r "
                         "JOIN algorithm a ON a.id = r.algorithm_id"):
        key = re.sub(r"^https?://(www\.)?", "", r["url"].lower()).rstrip("/")
        owned.setdefault(key, []).append(r["name"])

    algs = con.execute(
        "SELECT a.id, a.name, a.aliases, a.kind FROM algorithm a "
        "WHERE NOT EXISTS (SELECT 1 FROM algorithm_repository r WHERE r.algorithm_id = a.id) "
        "AND a.kind NOT IN ('review', 'meta')").fetchall()
    if args.ids:
        want = {int(i) for i in args.ids.split(",")}
        algs = [a for a in algs if a["id"] in want]

    texts: dict[Path, str] = {}
    rows = []
    for a in algs:
        names = [a["name"]] + [x.strip() for x in (a["aliases"] or "").split(",") if x.strip()]
        pids = [r[0] for r in con.execute(
            "SELECT publication_id FROM publication_algorithm "
            "WHERE algorithm_id = ? AND role = 'describes'", (a["id"],))]
        found: dict[str, dict] = {}
        for pid in pids:
            for f in cov.get(pid, []):
                if f not in texts:
                    texts[f] = pdf_text(f)
                t = texts[f]
                for m in URL.finditer(t):
                    host, owner, repo = m.group(1).lower(), m.group(2), (m.group(3) or "")
                    repo = repo.rstrip(".").removesuffix(".git")
                    if not repo or COMMON.match(owner):
                        continue
                    url = f"https://{host}/{owner}/{repo}"
                    key = f"{host}/{owner}/{repo}".lower()
                    ctx = t[max(0, m.start() - 160):m.end() + 60]
                    name_score = max(fuzz.ratio(norm(repo), norm(n)) for n in names)
                    if any(norm(n) and len(norm(n)) >= 4 and norm(n) in norm(repo) for n in names):
                        name_score = max(name_score, 95)
                    score = name_score + (25 if AVAIL.search(t[max(0, m.start() - 160):m.start()]) else 0)
                    others = [o for o in owned.get(key, []) if o != a["name"]]
                    if others:
                        score -= 60
                    prev = found.get(key)
                    if not prev or score > prev["score"]:
                        found[key] = dict(alg_id=a["id"], method=a["name"], kind=a["kind"],
                                          url=url, score=score, name_match=name_score,
                                          already_repo_of="; ".join(others),
                                          publication_id=pid, pdf=f.name,
                                          context=ctx.strip())
        rows += sorted(found.values(), key=lambda r: -r["score"])

    rows.sort(key=lambda r: (-r["score"], r["alg_id"]))
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["alg_id", "method", "kind", "url", "score",
                                           "name_match", "already_repo_of",
                                           "publication_id", "pdf", "context"])
        w.writeheader()
        w.writerows(rows)
    with_pdf = {a["id"] for a in algs if any(
        cov.get(p) for (p,) in con.execute(
            "SELECT publication_id FROM publication_algorithm WHERE algorithm_id=? "
            "AND role='describes'", (a["id"],)))}
    strong = {r["alg_id"] for r in rows if r["score"] >= 100 and not r["already_repo_of"]}
    print(f"{len(algs)} methods without a repository, {len(with_pdf)} with a local PDF")
    print(f"{len(rows)} candidate URLs for {len({r['alg_id'] for r in rows})} methods; "
          f"{len(strong)} methods have a strong candidate (name match plus "
          f"availability wording, not another method's repo)")
    print(f"-> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

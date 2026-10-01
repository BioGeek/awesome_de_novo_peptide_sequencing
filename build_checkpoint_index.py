#!/usr/bin/env python3
"""Generate the /checkpoints index page for the personal site, from the catalog.

The mirror's files live on Hugging Face and the index that points at them lives
at <https://jeroen.vangoey.be/checkpoints>, which is a page in a DIFFERENT
repository (BioGeek/biogeek.github.io, a Quarto site). Writing that page by
hand would mean a second copy of the checkpoint list drifting away from
`denovo.db`, so it is generated from the table instead.

**Why the files are not served from jeroen.vangoey.be itself.** That domain is
GitHub Pages, which rejects any file over 100 MB on push; every checkpoint here
is 270 MB to 2 GB. Git LFS does not help, because Pages serves the LFS pointer
text rather than the object. So the bytes are on Hugging Face and only the index
is on Pages.

    python3 build_checkpoint_index.py                   # print to stdout
    python3 build_checkpoint_index.py --out ../biogeek.github.io/checkpoints.qmd

The page deliberately links the ORIGINAL first and the backup second. A mirror
is a fallback; leading with it would obscure that the authors published the
weights themselves, and the original is what a paper should cite.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
from datetime import date

DB = pathlib.Path(__file__).with_name("denovo.db")
CATALOG = "https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/"
MIRROR = "https://huggingface.co/BioGeek/denovo-checkpoints"


def human(n: int | None) -> str:
    if not n:
        return "—"
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT a.name AS method, c.* FROM checkpoint c
          JOIN algorithm a ON a.id = c.algorithm_id
         ORDER BY a.name, c.archival DESC, c.tool_version, c.id
    """).fetchall()

    n_mirrored = sum(1 for r in rows if r["mirror_url"])
    L = [
        "---",
        'title: "De novo peptide sequencing checkpoints"',
        'description: "Published model weights for de novo peptide sequencing, with a '
        'backup copy of the ones hosted on links that can go stale."',
        "---", "",
        "Trained weights for *de novo* peptide sequencing models, as published by their",
        f"authors, with a backup copy of the ones whose only home is a link that carries",
        "no DOI and no preservation commitment: a Google Drive file, a Dropbox folder, a",
        "personal academic URL.",
        "",
        "**Cite the paper and prefer the original link.** Nothing here was trained by me.",
        f"The backups are a hedge against link rot, not a distribution channel; they live in",
        f"a [Hugging Face repository]({MIRROR}) whose README records, for every file, where",
        "it came from and what licence its source repository states.",
        "",
        f"The methods themselves are catalogued at [Awesome De Novo Peptide Sequencing]({CATALOG}).",
        "",
        f"<small>Generated {date.today().isoformat()} from the catalog. "
        f"{len(rows)} checkpoints, {n_mirrored} with a backup copy.</small>",
        "",
    ]

    current = None
    for r in rows:
        if r["method"] != current:
            current = r["method"]
            L += ["", f"## {current}", ""]
            L.append("| Version | Trained on | Original | Licence | Size | Backup |")
            L.append("|---|---|---|---|---|---|")
        ver = r["tool_version"] or r["label"] or r["filename"] or "—"
        host = f"[{r['host']}]({r['url']})"
        if r["archival"]:
            host += " <small>(archival)</small>"
        backup = f"[download]({r['mirror_url']})" if r["mirror_url"] else "—"
        L.append(f"| {ver} | {r['trained_on'] or '—'} | {host} | "
                 f"{r['licence'] or 'not stated'} | {human(r['size_bytes'])} | {backup} |")

    L += [
        "", "## What is not here", "",
        "Training data. The point of a backup is to keep a *model* reachable, and the",
        "datasets are both far larger and already in DOI'd archives: the MassIVE-KB",
        "Casanovo splits alone are 63 GB, and DIANovo's Dropbox folder is a 24 GB archive",
        "of sample data with the weights inside it. Those stay with their original hosts.",
        "",
        "Checkpoints whose host is already archival. A Zenodo record has a DOI and a",
        "preservation commitment, so copying it would duplicate an archive with better",
        "guarantees than this page can offer.",
        "",
    ]
    text = "\n".join(L) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text)} bytes, {len(rows)} checkpoints, "
              f"{n_mirrored} with a backup)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

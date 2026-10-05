#!/usr/bin/env python3
"""Mine the DNPS-DR daily report for papers this catalog is missing.

`https://huggingface.co/spaces/yangtingpeng/DNPS-DR` is a Hugging Face Space
that posts a daily de novo peptide sequencing literature briefing. It is itself
publication 272, a `resource`. This reads its backing file and asks the one
question the catalog cannot ask of it: which of these papers belong here and
are not here yet.

Like `build_candidates.py`, it NEVER touches denovo.db and inserts nothing.
Deciding what belongs is a judgement call; this writes `dnps_candidates.csv`
for review.

WHAT IT READS. The Space's repo carries `data/summaries.json`, a dict of
`{date: [{title, link, date, summary}]}` -- 1167 entries over 1030 dates, each
`link` a PubMed URL. The `summary` field is ignored: it contains the raw
chain-of-thought of whatever model wrote it, `<think>` blocks and all, in
Chinese, so it is not a summary of anything.

**THE FEED IS STALE, and a repo crawl cannot fix that.** Its newest entry is
2026-08-24 and the file was last committed 2026-08-27. The Space's own
scheduler writes inside the running container, so new days never reach the git
repo unless the author commits them. This script therefore surfaces the
BACKLOG, which is large and worth having, and will show nothing new until
upstream commits again. The `lastModified` check below is what makes a quiet
month cost one API call.

TWO SENSES OF "DE NOVO", and the whole value of this script is telling them
apart. The feed's query is far broader than this catalog's scope, so of 1167
entries:

  * 207 are already catalogued, which is the reassuring part: the feed really
    does cover the field;
  * most of the rest that say "de novo" mean **de novo DESIGN** -- RFdiffusion
    antibodies, GLP-1 agonists, taste peptides, "generative" anything. A
    different field that shares a Latin phrase.
  * others are de novo SEQUENCING of a different analyte: glycans,
    oligonucleotides, siRNA, DNA, transcriptomes.
  * 74 are peptide or protein de novo sequencing and are not in the catalog.

So a title must name sequencing AND a peptide-ish analyte, and must not match
the design or wrong-analyte vocabularies. Anything rejected is COUNTED in the
report rather than dropped silently, because a filter nobody can see is a
filter nobody can correct.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent.parent
DB_PATH = HERE / "denovo.db"
CACHE = HERE / ".cache" / "dnps"
OUT = HERE / "dnps_candidates.csv"
SPACE = "yangtingpeng/DNPS-DR"
MAILTO = "j.vangoey@instadeep.com"
UA = ("awesome_de_novo_peptide_sequencing/1.0 "
      "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
      f"mailto:{MAILTO})")

# A title must say it sequences, and say what: both halves are required.
SEQ = re.compile(r"de ?novo[^.]{0,40}?sequenc|sequenc\w*[^.]{0,30}de ?novo", re.I)
ANALYTE = re.compile(r"peptid|proteom|protein|antibod|immunopeptid|conotoxin|"
                     r"mass spectrom|MS/MS|tandem mass|neuropeptid", re.I)
# "de novo design" is a different field that shares the Latin.
DESIGN = re.compile(r"de ?novo (design|generat|synthes)|generative|RFdiffusion|"
                    r"\bdesign(ing)? of\b|GPT|de ?novo discovery", re.I)
# de novo sequencing of something that is not a peptide.
OTHER_ANALYTE = re.compile(r"glycan|oligonucleotid|ribonucleic|nucleic acid|\bDNA\b|"
                           r"\bRNA\b|siRNA|transcriptom|genome assembl|metabolit", re.I)


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def fetch(url: str, want_json: bool = True):
    """curl, for the reason build_pdf_library.py documents at length."""
    cp = subprocess.run(["curl", "-sS", "-L", "--max-time", "90", "-A", UA, url],
                        capture_output=True, text=True, timeout=120)
    if cp.returncode != 0:
        return None
    try:
        return json.loads(cp.stdout) if want_json else cp.stdout
    except ValueError:
        return None


def cached_json(key: str, url: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / (re.sub(r"[^A-Za-z0-9._-]", "_", key)[:150] + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    val = fetch(url)
    if val is not None:
        p.write_text(json.dumps(val))
    return val


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when the Space has not changed")
    ap.add_argument("--summary", type=Path,
                    help="also write a GitHub job summary to this path")
    args = ap.parse_args()

    meta = fetch(f"https://huggingface.co/api/spaces/{SPACE}")
    if meta is None:
        print("could not reach the Hugging Face API", file=sys.stderr)
        return 1
    stamp = meta.get("lastModified", "")
    marker = CACHE / "last_modified"
    if not args.force and marker.exists() and marker.read_text().strip() == stamp:
        print(f"{SPACE} unchanged since {stamp}; nothing to do.")
        return 0

    raw = fetch(f"https://huggingface.co/spaces/{SPACE}/raw/main/data/summaries.json")
    if not isinstance(raw, dict):
        print("could not read data/summaries.json", file=sys.stderr)
        return 1
    entries = [dict(it, day=day) for day, items in raw.items() for it in items
               if isinstance(it, dict) and it.get("title")]

    conn = sqlite3.connect(DB_PATH)
    catalog = [norm(t) for (t,) in conn.execute("SELECT title FROM publication")]
    watchlist = (HERE / "WATCHLIST.md").read_text(encoding="utf-8").lower()

    rows, counts = [], {"catalogued": 0, "design": 0, "other analyte": 0,
                        "not sequencing": 0, "watchlist": 0}
    for e in entries:
        title = e["title"].strip()
        t = norm(title)
        if max((fuzz.token_set_ratio(c, t) for c in catalog), default=0) >= 92:
            counts["catalogued"] += 1
            continue
        pmid = (re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", e.get("link", "")) or [None, ""])[1]
        if pmid and pmid in watchlist or norm(title)[:60] in norm(watchlist):
            counts["watchlist"] += 1
            continue
        if not (SEQ.search(title) and ANALYTE.search(title)):
            counts["not sequencing"] += 1
            continue
        if DESIGN.search(title):
            counts["design"] += 1
            continue
        if OTHER_ANALYTE.search(title):
            counts["other analyte"] += 1
            continue
        rows.append({"feed_date": e.get("day", ""), "pmid": pmid, "title": title,
                     "link": e.get("link", ""), "doi": "", "journal": "", "year": ""})

    # Resolve only the SURVIVORS to a DOI, so review has something citable.
    # Europe PMC by PMID, one at a time, cached, paced.
    for r in rows:
        if not r["pmid"]:
            continue
        d = cached_json(f"epmc_{r['pmid']}",
                        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                        "?format=json&resultType=core&query="
                        + quote(f'EXT_ID:{r["pmid"]} AND SRC:MED', safe=""))
        hit = ((d or {}).get("resultList", {}) or {}).get("result", [])
        if hit:
            # The venue is NESTED. `journalTitle` is None on every record in
            # resultType=core; the title lives at journalInfo.journal.title,
            # which is why the first run wrote an empty venue column for all 70.
            j = ((hit[0].get("journalInfo") or {}).get("journal") or {})
            r["doi"] = hit[0].get("doi", "") or ""
            r["journal"] = j.get("title") or j.get("medlineAbbreviation") or ""
            r["year"] = hit[0].get("pubYear", "") or ""
        time.sleep(0.2)

    # A DOI already in the catalog is a title the fuzzy match missed.
    have_doi = {d.lower() for (d,) in conn.execute(
        "SELECT doi FROM publication WHERE COALESCE(doi,'') <> ''")}
    before = len(rows)
    rows = [r for r in rows if (r["doi"] or "").lower() not in have_doi]
    counts["catalogued"] += before - len(rows)

    rows.sort(key=lambda r: r["feed_date"], reverse=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["feed_date", "pmid", "title", "link", "doi", "journal", "year"])
        w.writeheader()
        w.writerows(rows)
    marker.write_text(stamp)

    lines = [f"{len(entries)} entries in the feed, last changed {stamp}", ""]
    for k, v in counts.items():
        lines.append(f"  {v:5d}  skipped: {k}")
    lines.append(f"  {len(rows):5d}  CANDIDATES -> {OUT.name}")
    print("\n".join(lines))
    print("\nnewest 15:")
    for r in rows[:15]:
        print(f"  {r['feed_date']}  {r['title'][:88]}")
        print(f"              {r['doi'] or r['link']}  {r['journal'][:40]}")

    if args.summary:
        md = ["## DNPS-DR candidates", "",
              f"Feed last changed **{stamp}**, {len(entries)} entries, "
              f"**{len(rows)} candidates** not in the catalog.", "",
              "| skipped | why |", "|---|---|"]
        md += [f"| {v} | {k} |" for k, v in counts.items()]
        md += ["", "| feed date | title | DOI | venue |", "|---|---|---|---|"]
        md += [f"| {r['feed_date']} | {r['title'][:90]} | "
               f"{('`'+r['doi']+'`') if r['doi'] else r['link']} | {r['journal'][:34]} |"
               for r in rows[:40]]
        if len(rows) > 40:
            md.append(f"\n_{len(rows) - 40} more in the CSV artifact._")
        args.summary.write_text("\n".join(md) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Find papers that probably belong in the catalog but are not in it yet.

The catalog cannot answer this on its own. publication_citation stores ONLY
intra-catalog edges (verified: 0 of its 2176 rows point outside), so every
reference to the outside world is discarded at build time. This script goes and
gets the outside, from OpenAlex, in both directions:

  REFERENCES (backward)  works that OUR papers cite. A work cited by many of
                         our papers is probably a foundational de novo paper we
                         never entered.
  CITATIONS (forward)    works that cite OUR papers, scored by how many of them
                         they cite. A paper citing six of our de novo papers is
                         very likely a de novo paper itself. This is the half
                         that surfaces NEW work, month after month.

Both scores are "how many of our 300 publications link to this thing", which is
the useful signal: a work linked to one of our papers is noise, a work linked to
eight is a gap. It writes candidates.csv for review and NEVER touches denovo.db,
because deciding what belongs in the catalog is a judgement call (see the bar
recorded in WATCHLIST.md) and not something a citation count should automate.

NOT A FIFTH REFRESH WORKFLOW. The four build_* refreshers each own exactly one
table, which is the invariant .github/actions/commit-refreshed-db relies on to
replay its rows after a push race. This script owns no table and writes a CSV,
so it must not use that action.

Excluded from the output: anything already in the catalog, by OpenAlex id or by
DOI, and anything already argued about in WATCHLIST.md, so a paper deliberately
rejected does not come back every month.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "denovo.db"
WATCHLIST = HERE / "WATCHLIST.md"
API = "https://api.openalex.org/works"
MAILTO = "j.vangoey@instadeep.com"
BACKWARD_BATCH = 50      # ids per request when fetching our own works
FORWARD_BATCH = 20       # ids per cites: request; longer filters get rejected
RETRIES = 3


def get(params: dict) -> dict:
    """GET with a short retry. OpenAlex 429s under load and 500s occasionally."""
    params = {**params, "mailto": MAILTO}
    url = f"{API}?{urllib.parse.urlencode(params)}"
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"awesome-de-novo ({MAILTO})"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == RETRIES - 1:
                print(f"    give up after {RETRIES}: {e}", file=sys.stderr)
                return {}
            time.sleep(2 * (attempt + 1))
    return {}


def short(oa_id: str) -> str:
    """'https://openalex.org/W123' and 'W123' both -> 'W123'."""
    return (oa_id or "").rsplit("/", 1)[-1]


def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I).lower()


def batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def norm_title(t: str | None) -> str:
    """Lowercase, strip everything but alphanumerics. Matches build_versions.py."""
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load_ours(db: sqlite3.Connection) -> tuple[list[str], set[str], set[str], set[str]]:
    ids = [short(r[0]) for r in db.execute(
        "SELECT openalex_id FROM publication_impact WHERE COALESCE(openalex_id,'') <> ''")]
    dois = {norm_doi(r[0]) for r in db.execute(
        "SELECT doi FROM publication WHERE COALESCE(doi,'') <> ''")}
    watch = set()
    if WATCHLIST.exists():
        for m in re.findall(r"10\.\d{4,9}/[^\s`)>,]+", WATCHLIST.read_text(encoding="utf-8")):
            watch.add(norm_doi(m.rstrip(".,")))
    titles = {norm_title(r[0]) for r in db.execute("SELECT title FROM publication")}
    return sorted(set(ids)), dois - {""}, watch - {""}, titles - {""}


def harvest_references(ids: list[str]) -> dict[str, int]:
    """For each of our works, what it cites. Returns candidate -> our-paper count."""
    tally: dict[str, int] = defaultdict(int)
    for n, batch in enumerate(batched(ids, BACKWARD_BATCH), 1):
        data = get({"filter": f"openalex_id:{'|'.join(batch)}",
                    "select": "id,referenced_works", "per-page": BACKWARD_BATCH})
        for w in data.get("results", []):
            for ref in w.get("referenced_works") or []:
                tally[short(ref)] += 1
        print(f"  references: batch {n}, {len(tally)} distinct cited works so far", flush=True)
    return tally


def harvest_citations(ids: list[str]) -> tuple[dict[str, int], dict[str, dict]]:
    """Works citing ours, scored by how many of ours they cite. Metadata comes free."""
    ours = set(ids)
    tally: dict[str, int] = {}
    meta: dict[str, dict] = {}
    for n, batch in enumerate(batched(ids, FORWARD_BATCH), 1):
        cursor = "*"
        seen = 0
        while cursor:
            data = get({"filter": f"cites:{'|'.join(batch)}",
                        "select": "id,doi,title,publication_year,cited_by_count,"
                                  "referenced_works,primary_location,type",
                        "per-page": 200, "cursor": cursor})
            results = data.get("results", [])
            for w in results:
                key = short(w["id"])
                overlap = len({short(r) for r in (w.get("referenced_works") or [])} & ours)
                # Keep the larger score: a work can appear under several batches,
                # and its own referenced_works list gives the true total.
                tally[key] = max(tally.get(key, 0), overlap)
                meta.setdefault(key, w)
            seen += len(results)
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not results:
                break
        print(f"  citations: batch {n}/{(len(ids) + FORWARD_BATCH - 1)//FORWARD_BATCH}, "
              f"{seen} citing works, {len(tally)} distinct so far")
    return tally, meta


def fetch_meta(keys: list[str], have: dict[str, dict]) -> dict[str, dict]:
    """Fill in metadata for candidates the forward pass did not already describe."""
    need = [k for k in keys if k not in have]
    out = dict(have)
    for n, batch in enumerate(batched(need, BACKWARD_BATCH), 1):
        data = get({"filter": f"openalex_id:{'|'.join(batch)}",
                    "select": "id,doi,title,publication_year,cited_by_count,"
                              "primary_location,type",
                    "per-page": BACKWARD_BATCH})
        for w in data.get("results", []):
            out[short(w["id"])] = w
        print(f"  metadata: batch {n}, {len(out)} described", flush=True)
    return out


def venue_of(w: dict) -> str:
    return (((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direction", choices=["both", "references", "citations"], default="both")
    ap.add_argument("--min-links", type=int, default=3,
                    help="report a candidate only if this many of our papers link it (default 3)")
    ap.add_argument("--out", default=str(HERE / "candidates.csv"))
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"error: {DB_PATH} not found", file=sys.stderr)
        return 2
    db = sqlite3.connect(DB_PATH)
    ids, our_dois, watch_dois, our_titles = load_ours(db)
    our_ids = set(ids)
    print(f"catalog: {len(ids)} works with an OpenAlex id, {len(our_dois)} DOIs, "
          f"{len(watch_dois)} DOIs already on the watch list", flush=True)

    refs: dict[str, int] = {}
    cites: dict[str, int] = {}
    meta: dict[str, dict] = {}
    if args.direction in ("both", "references"):
        refs = harvest_references(ids)
    if args.direction in ("both", "citations"):
        cites, meta = harvest_citations(ids)

    keys = set(refs) | set(cites)
    keys -= our_ids
    print(f"{len(keys)} distinct works linked to the catalog, before filtering", flush=True)

    scored = []
    for k in keys:
        total = refs.get(k, 0) + cites.get(k, 0)
        if total >= args.min_links:
            scored.append(k)
    print(f"{len(scored)} clear the --min-links {args.min_links} bar", flush=True)

    meta = fetch_meta(sorted(scored), meta)

    rows = []
    skipped_known = 0
    skipped_title = 0
    for k in scored:
        w = meta.get(k, {})
        doi = norm_doi(w.get("doi"))
        if doi and (doi in our_dois or doi in watch_dois):
            skipped_known += 1
            continue
        if norm_title(w.get("title")) in our_titles:
            skipped_title += 1
            continue
        rows.append({
            "openalex_id": k,
            "cited_by_our_papers": refs.get(k, 0),
            "cites_our_papers": cites.get(k, 0),
            "links_total": refs.get(k, 0) + cites.get(k, 0),
            "year": w.get("publication_year") or "",
            "type": w.get("type") or "",
            "cited_by_count": w.get("cited_by_count") or 0,
            "title": (w.get("title") or "").replace("\n", " "),
            "venue": venue_of(w),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else f"https://openalex.org/{k}",
        })
    rows.sort(key=lambda r: (-r["links_total"], -r["cited_by_count"], r["title"]))

    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                                ["openalex_id", "cited_by_our_papers", "cites_our_papers",
                                 "links_total", "year", "type", "cited_by_count", "title",
                                 "venue", "doi", "url"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nskipped {skipped_known} by DOI (in the catalog or on the watch list) "
          f"and {skipped_title} more by title match", flush=True)
    print(f"wrote {len(rows)} candidates to {out}", flush=True)
    if rows:
        print("\ntop 15 by links to the catalog:", flush=True)
        for r in rows[:15]:
            print(f"  {r['links_total']:3d} links ({r['cited_by_our_papers']} cited by us, "
                  f"{r['cites_our_papers']} cite us)  {r['year']}  {r['title'][:66]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

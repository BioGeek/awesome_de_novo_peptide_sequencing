#!/usr/bin/env python3
"""Build the publication_citation graph from Crossref + Semantic Scholar.

Run offline (not in CI). Re-execute when new papers are added or to refresh
citation counts. Idempotent — INSERT OR IGNORE keeps existing edges.

Outputs:
  - rows inserted into `publication_citation` in denovo.db
  - citation_audit.csv next to this script: every fuzzy-matched edge with the
    title pair and similarity score, for human review.

References resolved by DOI (primary, exact) or fuzzy title match (secondary,
token-set ratio >= 0.92 over normalized titles).
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz

DB_PATH    = Path(__file__).parent / "denovo.db"
AUDIT_PATH = Path(__file__).parent / "citation_audit.csv"

USER_AGENT     = "awesome-de-novo-peptide-sequencing/0.1 (https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; mailto:j.vangoey@instadeep.com)"
CROSSREF_BASE  = "https://api.crossref.org/works"
SS_BASE        = "https://api.semanticscholar.org/graph/v1/paper"

FUZZY_THRESHOLD = 92  # token-set ratio
CROSSREF_DELAY  = 0.2  # seconds between requests; "polite pool" is ~50 req/sec
SS_DELAY        = 1.5  # seconds; unauthenticated public-API limit ≈ 100/5min

TITLE_PUNCT_RE = re.compile(r"[\W_]+")


def normalize_title(s: str) -> str:
    if not s:
        return ""
    return TITLE_PUNCT_RE.sub(" ", s).strip().lower()


def fetch_crossref_refs(doi: str) -> list[dict]:
    """Return the list of references for a DOI, or [] on any failure."""
    try:
        r = requests.get(
            f"{CROSSREF_BASE}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("reference", []) or []
    except requests.RequestException:
        return []


def fetch_semantic_scholar_refs(doi: str | None, title: str) -> list[dict]:
    """Return Semantic Scholar references for a paper.

    Tries DOI lookup first, falls back to title search if no DOI. Returns [] on
    any failure (network, 404, rate-limit).
    """
    paper_id = f"DOI:{doi}" if doi else None
    if not paper_id and title:
        # Search by title
        try:
            r = requests.get(
                f"{SS_BASE}/search",
                params={"query": title, "limit": 1, "fields": "paperId,title"},
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return []
            # Confirm the search hit looks like our paper before pulling refs.
            hit_title = (data[0].get("title") or "").strip()
            if fuzz.token_set_ratio(normalize_title(hit_title), normalize_title(title)) < FUZZY_THRESHOLD:
                return []
            paper_id = data[0]["paperId"]
            time.sleep(SS_DELAY)
        except requests.RequestException:
            return []

    if not paper_id:
        return []

    try:
        r = requests.get(
            f"{SS_BASE}/{paper_id}/references",
            params={"fields": "title,externalIds", "limit": 1000},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        r.raise_for_status()
        return [item.get("citedPaper") or {} for item in r.json().get("data", [])]
    except requests.RequestException:
        return []


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur  = conn.cursor()

    pubs = cur.execute(
        "SELECT id, title, doi FROM publication ORDER BY id"
    ).fetchall()
    print(f"Loaded {len(pubs)} publications from the DB.")

    # Lookup tables: DOI → pub_id, normalized_title → pub_id
    by_doi   = {(d or "").lower(): pid for pid, _, d in pubs if d}
    by_title = {normalize_title(t): pid for pid, t, _ in pubs if t}
    all_titles_norm = list(by_title.keys())

    audit_rows: list[dict] = []
    edges: dict[tuple[int, int], set[str]] = {}  # (citing, cited) → {sources}

    for idx, (pid, title, doi) in enumerate(pubs, 1):
        print(f"[{idx}/{len(pubs)}] pub {pid}: {title[:60]}")

        # 1) Crossref by DOI
        crossref_refs: list[dict] = []
        if doi:
            crossref_refs = fetch_crossref_refs(doi)
            time.sleep(CROSSREF_DELAY)

        # 2) Semantic Scholar (works with or without DOI; uses title search as fallback)
        ss_refs = fetch_semantic_scholar_refs(doi, title)
        time.sleep(SS_DELAY)

        # ---- Match Crossref references back to our DB ----
        for ref in crossref_refs:
            ref_doi = (ref.get("DOI") or "").lower().strip()
            ref_title = (
                ref.get("article-title")
                or ref.get("series-title")
                or ref.get("unstructured")
                or ""
            )
            cited = match_reference(ref_doi, ref_title, by_doi, by_title, all_titles_norm, audit_rows, pid)
            if cited and cited != pid:
                edges.setdefault((pid, cited), set()).add("crossref")

        # ---- Match Semantic Scholar references ----
        for ref in ss_refs:
            ext = ref.get("externalIds") or {}
            ref_doi   = (ext.get("DOI") or "").lower().strip()
            ref_title = (ref.get("title") or "")
            cited = match_reference(ref_doi, ref_title, by_doi, by_title, all_titles_norm, audit_rows, pid)
            if cited and cited != pid:
                edges.setdefault((pid, cited), set()).add("semanticscholar")

    # ---- Write edges to DB ----
    print(f"\nResolved {len(edges)} unique citation edges.")
    for (citing, cited), srcs in sorted(edges.items()):
        source = "both" if len(srcs) == 2 else next(iter(srcs))
        cur.execute(
            "INSERT OR IGNORE INTO publication_citation (citing_id, cited_id, source) VALUES (?, ?, ?)",
            (citing, cited, source),
        )
    conn.commit()
    conn.close()

    # ---- Write audit CSV ----
    if audit_rows:
        with AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(audit_rows[0].keys()))
            w.writeheader()
            w.writerows(audit_rows)
        print(f"Wrote {len(audit_rows)} fuzzy-match audit rows to {AUDIT_PATH}.")
    else:
        print("No fuzzy matches; no audit file written.")
    return 0


def match_reference(
    ref_doi: str,
    ref_title: str,
    by_doi: dict[str, int],
    by_title: dict[str, int],
    all_titles_norm: list[str],
    audit_rows: list[dict],
    citing_id: int,
) -> int | None:
    """Resolve a single reference to a publication id in our DB.

    DOI exact-match wins. Otherwise fuzzy title match against every paper in
    the DB (token-set ratio ≥ FUZZY_THRESHOLD). Fuzzy matches go into
    audit_rows for human review.
    """
    if ref_doi and ref_doi in by_doi:
        return by_doi[ref_doi]
    if not ref_title:
        return None
    norm = normalize_title(ref_title)
    if not norm:
        return None
    # Exact normalized-title match → strong, no audit row needed.
    if norm in by_title:
        return by_title[norm]
    # Fuzzy match against every title.
    best_title, best_score = "", 0
    for cand in all_titles_norm:
        s = fuzz.token_set_ratio(norm, cand)
        if s > best_score:
            best_score, best_title = s, cand
    if best_score >= FUZZY_THRESHOLD:
        audit_rows.append({
            "citing_pub_id": citing_id,
            "reference_doi": ref_doi or "",
            "reference_title": ref_title.strip()[:160],
            "matched_pub_id": by_title[best_title],
            "matched_title_normalized": best_title[:160],
            "score": best_score,
        })
        return by_title[best_title]
    return None


if __name__ == "__main__":
    sys.exit(main())

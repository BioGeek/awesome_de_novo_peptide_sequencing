#!/usr/bin/env python3
"""Link preprints to the peer-reviewed version of the same work.

Run offline (not in CI). Re-execute when papers are added.

Until now this relation existed nowhere in the schema, and the site's
"Publication lifecycle" chart GUESSED it at render time by bucketing
publications on (first algorithm name, version) and greedily pairing each
preprint with the earliest later peer-reviewed paper in the bucket. That is
wrong in three ways: it reads only the first of a paper's algorithm links, a
paper with no algorithm link can never pair at all, and greedy date order can
hand the same journal paper to the wrong preprint.

Sources, most authoritative first:

  1. bioRxiv API `published` field. bioRxiv itself tracks where a preprint was
     published and returns the journal DOI (or "NA"). Unambiguous.
  2. Crossref relations: `is-preprint-of` on the preprint DOI, and
     `has-preprint` on the published DOI.
  3. Normalised-title exact match. Cheap, and it turns out to be reliable here
     because 16 preprint/published pairs in this catalog carry byte-identical
     titles.
  4. Fuzzy title match (token_set_ratio >= 92, the same threshold
     build_citations.py uses). These are NOT inserted. They go to
     version_audit.csv for human review, exactly as build_citations.py does
     with citation_audit.csv.

A preprint whose published version is NOT in the catalog is reported
separately: that is a reading list, not an error.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import requests
from rapidfuzz import fuzz

DB_PATH = Path(__file__).parent / "denovo.db"
AUDIT_PATH = Path(__file__).parent / "version_audit.csv"

USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)

BIORXIV_BASE = "https://api.biorxiv.org/details/biorxiv"
CROSSREF_BASE = "https://api.crossref.org/works"

REQUEST_DELAY = 0.2
TITLE_MATCH_THRESHOLD = 92

BIORXIV_PREFIXES = ("10.1101/", "10.64898/")

# publication_type values that represent a post-preprint version of record.
# 'resource' and 'commentary' are excluded: neither is a journal version of a
# preprint.
PUBLISHED_TYPES = ("peer-reviewed", "ML conference", "thesis")


def ensure_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS publication_version (
            preprint_id  INTEGER NOT NULL
                REFERENCES publication(id) ON DELETE CASCADE ON UPDATE CASCADE,
            published_id INTEGER NOT NULL
                REFERENCES publication(id) ON DELETE CASCADE ON UPDATE CASCADE,
            source       TEXT NOT NULL,  -- 'biorxiv' | 'crossref' | 'title' | 'manual'
            PRIMARY KEY (preprint_id, published_id)
        )
        """
    )
    # A journal paper is the version of record of at most ONE preprint. Without
    # this, two preprints can both claim the same paper: the fuzzy pass scores
    # both 222 ("Regressor-guided Diffusion Model...") and 116 ("Diffusion
    # Decoding for Peptide De Novo Sequencing") against publication 7.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_version_published "
        "ON publication_version(published_id)"
    )
    # Reject nonsense at insert time, so hand-written INSERTs are guarded too,
    # in the same spirit as the existing publication_date citation triggers.
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS publication_version_sanity
        BEFORE INSERT ON publication_version
        FOR EACH ROW
        BEGIN
            SELECT CASE
              WHEN (SELECT publication_type FROM publication WHERE id = NEW.preprint_id)
                   <> 'preprint'
                THEN RAISE(ABORT, 'preprint_id must reference a publication_type=preprint row')
              WHEN NEW.preprint_id = NEW.published_id
                THEN RAISE(ABORT, 'a publication cannot be its own other version')
              WHEN date((SELECT publication_date FROM publication WHERE id = NEW.published_id))
                   < date((SELECT publication_date FROM publication WHERE id = NEW.preprint_id))
                THEN RAISE(ABORT, 'published version predates the preprint')
            END;
        END
        """
    )


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def get_json(url: str) -> dict | None:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def biorxiv_published_doi(doi: str) -> str | None:
    data = get_json(f"{BIORXIV_BASE}/{doi}")
    if not data:
        return None
    for rec in reversed(data.get("collection") or []):
        published = (rec.get("published") or "").strip()
        if published and published.upper() != "NA":
            return published.lower()
    return None


def crossref_related_dois(doi: str, relation: str) -> list[str]:
    data = get_json(f"{CROSSREF_BASE}/{doi}")
    if not data:
        return []
    relations = ((data.get("message") or {}).get("relation") or {}).get(relation) or []
    out = []
    for rel in relations:
        if (rel.get("id-type") or "").upper() == "DOI" and rel.get("id"):
            out.append(rel["id"].strip().lower())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="skip the bioRxiv and Crossref passes; title matching only")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be inserted, write nothing")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_table(cur)
    conn.commit()

    rows = cur.execute(
        "SELECT id, title, publication_date, publication_type, doi, journal "
        "FROM publication ORDER BY id"
    ).fetchall()

    by_doi = {(r[4] or "").strip().lower(): r[0] for r in rows if (r[4] or "").strip()}
    preprints = [r for r in rows if r[3] == "preprint"]
    published = [r for r in rows if r[3] in PUBLISHED_TYPES]
    meta = {r[0]: r for r in rows}

    print(f"{len(preprints)} preprints, {len(published)} published records\n")

    links: dict[int, tuple[int, str]] = {}   # preprint_id -> (published_id, source)
    published_offsite: list[tuple[int, str, str]] = []
    audit: list[dict] = []

    # --- pass 1 + 2: authoritative, per preprint --------------------------
    if not args.offline:
        for idx, (pid, title, _date, _type, doi, _journal) in enumerate(preprints, 1):
            doi = (doi or "").strip().lower()
            if not doi:
                continue
            target_doi, source = None, None

            if doi.startswith(BIORXIV_PREFIXES):
                target_doi, source = biorxiv_published_doi(doi), "biorxiv"
                time.sleep(REQUEST_DELAY)

            if not target_doi:
                rel = crossref_related_dois(doi, "is-preprint-of")
                time.sleep(REQUEST_DELAY)
                target_doi, source = (rel[0] if rel else None), "crossref"

            if not target_doi:
                continue
            local = by_doi.get(target_doi)
            if local is None:
                published_offsite.append((pid, title, target_doi))
                print(f"[{idx}/{len(preprints)}] pub {pid}: published as "
                      f"{target_doi}, NOT in the catalog")
                continue
            links[pid] = (local, source)
            print(f"[{idx}/{len(preprints)}] pub {pid} -> {local} ({source})")

    # --- pass 3: exact normalised-title match ------------------------------
    pub_by_norm: dict[str, list[int]] = {}
    for r in published:
        pub_by_norm.setdefault(norm_title(r[1]), []).append(r[0])

    for pid, title, date, _type, _doi, _journal in preprints:
        if pid in links:
            continue
        cands = pub_by_norm.get(norm_title(title), [])
        cands = [c for c in cands
                 if (meta[c][2] or "") >= (date or "") and c not in
                 {v[0] for v in links.values()}]
        if len(cands) == 1:
            links[pid] = (cands[0], "title")
            print(f"pub {pid} -> {cands[0]} (title, exact)")

    # --- pass 4: fuzzy, audit only -----------------------------------------
    claimed = {v[0] for v in links.values()}
    for pid, title, date, _type, _doi, _journal in preprints:
        if pid in links:
            continue
        for c in published:
            if c[0] in claimed or (c[2] or "") < (date or ""):
                continue
            score = fuzz.token_set_ratio(title, c[1])
            if score >= TITLE_MATCH_THRESHOLD:
                audit.append({
                    "score": round(score, 1),
                    "preprint_id": pid, "preprint_date": date, "preprint_title": title,
                    "published_id": c[0], "published_date": c[2],
                    "published_journal": c[5], "published_title": c[1],
                })

    if audit:
        audit.sort(key=lambda r: (-r["score"], r["preprint_id"]))
        with AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(audit[0].keys()))
            writer.writeheader()
            writer.writerows(audit)

    # --- write -------------------------------------------------------------
    inserted, rejected = 0, []
    if not args.dry_run:
        for pid, (target, source) in sorted(links.items()):
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO publication_version "
                    "(preprint_id, published_id, source) VALUES (?, ?, ?)",
                    (pid, target, source),
                )
                inserted += cur.rowcount
            except sqlite3.IntegrityError as exc:
                rejected.append((pid, target, str(exc)))
        conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM publication_version").fetchone()[0]
    print(f"\nDone. {len(links)} links resolved, {inserted} inserted, "
          f"{total} rows in publication_version.")
    by_source = cur.execute(
        "SELECT source, COUNT(*) FROM publication_version GROUP BY source ORDER BY 2 DESC"
    ).fetchall()
    for source, n in by_source:
        print(f"  {source:9s} {n}")

    if rejected:
        print(f"\n{len(rejected)} rejected by a constraint (resolve by hand):")
        for pid, target, exc in rejected:
            print(f"  {pid} -> {target}: {exc}")

    if published_offsite:
        print(f"\n{len(published_offsite)} preprints are published somewhere not in "
              f"the catalog. Worth adding:")
        for pid, title, doi in published_offsite:
            print(f"  pub {pid:4d}  {doi}  {title[:56]}")

    if audit:
        print(f"\n{len(audit)} fuzzy candidates written to {AUDIT_PATH.name} "
              f"for review. None were inserted.")

    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        conn.rollback()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

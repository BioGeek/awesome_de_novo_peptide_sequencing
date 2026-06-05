#!/usr/bin/env python3
"""Fetch global publication citation counts from OpenAlex.

Run offline (not in CI). Re-execute when papers are added or when citation
counts should be refreshed.

The table stores OpenAlex `cited_by_count` per publication. DOI lookup is used
first because it is exact; title search is used as a fallback for records
without a DOI or records that OpenAlex cannot resolve by DOI.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from rapidfuzz import fuzz

DB_PATH = Path(__file__).parent / "denovo.db"
OPENALEX_BASE = "https://api.openalex.org/works"
USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)
REQUEST_DELAY = 0.2
TITLE_MATCH_THRESHOLD = 92


def ensure_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS publication_impact (
            publication_id INTEGER PRIMARY KEY,
            openalex_id TEXT,
            cited_by_count INTEGER,
            match_method TEXT NOT NULL,
            match_score REAL,
            year_collected INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (publication_id) REFERENCES publication(id)
                ON DELETE CASCADE
                ON UPDATE CASCADE
        )
        """
    )


def get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=25,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else None
    except (requests.RequestException, ValueError):
        return None


def fetch_by_doi(doi: str) -> dict | None:
    return get_json(
        f"{OPENALEX_BASE}/https://doi.org/{requests.utils.quote(doi, safe='')}",
        params={"mailto": "j.vangoey@instadeep.com"},
    )


def fetch_by_title(title: str) -> tuple[dict | None, float | None]:
    body = get_json(
        OPENALEX_BASE,
        params={
            "search": title,
            "per-page": 5,
            "select": "id,display_name,doi,cited_by_count",
            "mailto": "j.vangoey@instadeep.com",
        },
    )
    if not body:
        return None, None
    hits = body.get("results") or []
    best: tuple[float, dict] | None = None
    for hit in hits:
        score = fuzz.token_set_ratio(title, hit.get("display_name") or "")
        if best is None or score > best[0]:
            best = (score, hit)
    if not best or best[0] < TITLE_MATCH_THRESHOLD:
        return None, best[0] if best else None
    return best[1], best[0]


def normalize_openalex_id(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    return raw_id.rsplit("/", 1)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", type=str, default=None,
                    help="Comma-separated publication ids to refresh.")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_table(cur)
    conn.commit()

    pubs = cur.execute(
        "SELECT id, title, doi FROM publication ORDER BY id"
    ).fetchall()
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        pubs = [p for p in pubs if p[0] in wanted]
        print(f"--only filter: {len(pubs)} publications selected.")
    else:
        print(f"Loaded {len(pubs)} publications from the DB.")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    year = datetime.now(timezone.utc).year
    matched_doi = matched_title = unmatched = 0

    for idx, (pid, title, doi) in enumerate(pubs, 1):
        work = None
        method = "unmatched"
        score = None
        if doi:
            work = fetch_by_doi(doi)
            time.sleep(REQUEST_DELAY)
            if work:
                method = "doi"
        if not work:
            work, score = fetch_by_title(title)
            time.sleep(REQUEST_DELAY)
            if work:
                method = "title"

        if work:
            if method == "doi":
                matched_doi += 1
            else:
                matched_title += 1
            cited_by_count = work.get("cited_by_count")
            openalex_id = normalize_openalex_id(work.get("id"))
        else:
            unmatched += 1
            cited_by_count = None
            openalex_id = None

        cur.execute(
            """
            INSERT INTO publication_impact
                (publication_id, openalex_id, cited_by_count, match_method,
                 match_score, year_collected, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(publication_id) DO UPDATE SET
                openalex_id = excluded.openalex_id,
                cited_by_count = excluded.cited_by_count,
                match_method = excluded.match_method,
                match_score = excluded.match_score,
                year_collected = excluded.year_collected,
                fetched_at = excluded.fetched_at
            """,
            (pid, openalex_id, cited_by_count, method, score, year, fetched_at),
        )
        label = f"{cited_by_count} citations" if cited_by_count is not None else "unmatched"
        print(f"[{idx}/{len(pubs)}] pub {pid}: {label} ({method})")

    conn.commit()
    conn.close()
    print(
        f"\nDone. DOI matches={matched_doi}; title matches={matched_title}; "
        f"unmatched={unmatched}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

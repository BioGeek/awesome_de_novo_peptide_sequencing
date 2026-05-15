#!/usr/bin/env python3
"""Fetch OpenAlex venue metrics for every peer-reviewed journal in the catalog.

Run offline (not in CI). Re-execute when new venues are added or to refresh
metrics annually.

Source: https://api.openalex.org/sources?search={journal}
       https://api.openalex.org/sources/{openalex_id}

We pull `summary_stats.2yr_mean_citedness` — methodologically equivalent to the
JCR Impact Factor (mean citations in year t to articles published in years t-1
and t-2) but computed over OpenAlex's open citation graph. We also store
`summary_stats.h_index` and `works_count` for context.

Conference proceedings and preprint servers are skipped — they don't have a
meaningful IF-style metric. The script identifies them by their publication_type
in `publication` (peer-reviewed / ML conference are eligible; preprint / thesis
/ commentary are not).

Idempotent: re-running upserts metrics for journals already present.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from rapidfuzz import fuzz

DB_PATH = Path(__file__).parent / "denovo.db"
USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)
OPENALEX_BASE = "https://api.openalex.org/sources"
REQUEST_DELAY = 0.2   # polite spacing; OpenAlex allows 10 req/sec for the polite pool

# Conferences and proceedings to skip — OpenAlex does have venue records for
# them but the 2yr citedness is misleading for non-rolling venues.
CONFERENCE_VENUES = {
    "ICLR 2025", "AAAI 2024", "AAAI 2026", "NeurIPS 2024", "IJCAI 2025",
    "ICML 2022",
    "Bioinformatics and Computational Biology (BICOB 2025)",
}

# Manual journal → OpenAlex source-id pin. Use this for venues whose
# OpenAlex /search returns the wrong hit (homonyms, sub-imprints, etc.).
PINNED_IDS: dict[str, str] = {
    # PNAS /search returns "PNAS Nexus" first; pin to the actual PNAS.
    "PNAS":                    "S125754415",
    # Applied Sciences (MDPI) — name "(MDPI)" qualifier confuses /search.
    "Applied Sciences (MDPI)": "S4210205812",
}


def fetch_source(journal: str) -> dict | None:
    """Return the best OpenAlex source record for `journal`, or None on failure."""
    if journal in PINNED_IDS:
        try:
            r = requests.get(
                f"{OPENALEX_BASE}/{PINNED_IDS[journal]}",
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    try:
        r = requests.get(
            OPENALEX_BASE,
            params={"search": journal, "per-page": 5},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            return None
        results = body.get("results") or []
        if not results:
            return None
        # Pick the result whose display_name fuzzy-matches our journal best.
        norm = journal.lower()
        scored = []
        for hit in results:
            display = (hit.get("display_name") or "").lower()
            scored.append((fuzz.token_set_ratio(norm, display), hit))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        best_score, best_hit = scored[0]
        if best_score < 80:
            print(f"  - low confidence ({best_score}) for {journal!r} → "
                  f"{best_hit.get('display_name')!r}")
            return None
        return best_hit
    except (requests.RequestException, ValueError):
        return None


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur  = conn.cursor()

    # Every distinct journal that's actually peer-reviewed-flavoured.
    journals = [
        row[0] for row in cur.execute(
            """
            SELECT DISTINCT journal FROM publication
            WHERE publication_type IN ('peer-reviewed', 'ML conference', 'thesis')
              AND journal IS NOT NULL AND journal != ''
            ORDER BY journal
            """
        ).fetchall()
    ]
    print(f"Found {len(journals)} candidate venues.")

    inserted = updated = skipped = 0
    this_year = datetime.utcnow().year
    for idx, j in enumerate(journals, 1):
        if j in CONFERENCE_VENUES:
            print(f"[{idx}/{len(journals)}] {j!r:60s} → SKIPPED (conference)")
            skipped += 1
            continue

        src = fetch_source(j)
        time.sleep(REQUEST_DELAY)
        if not src:
            print(f"[{idx}/{len(journals)}] {j!r:60s} → no OpenAlex match")
            continue

        oa_id = src.get("id", "").rsplit("/", 1)[-1]
        stats = src.get("summary_stats", {}) or {}
        citedness = stats.get("2yr_mean_citedness")
        h_idx     = stats.get("h_index")
        works     = src.get("works_count")
        display   = src.get("display_name", "?")

        print(f"[{idx}/{len(journals)}] {j!r:60s} → {display} (citedness={citedness}, h={h_idx})")

        cur.execute(
            """
            INSERT INTO journal_impact (journal, openalex_id, two_yr_citedness,
                                        h_index, works_count, year_collected)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(journal) DO UPDATE SET
                openalex_id      = excluded.openalex_id,
                two_yr_citedness = excluded.two_yr_citedness,
                h_index          = excluded.h_index,
                works_count      = excluded.works_count,
                year_collected   = excluded.year_collected
            """,
            (j, oa_id, citedness, h_idx, works, this_year),
        )
        if cur.rowcount == 1:
            inserted += 1
        else:
            updated += 1

    conn.commit()
    conn.close()
    print(f"\nDone. inserted/updated venues: {inserted + updated}; skipped: {skipped}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

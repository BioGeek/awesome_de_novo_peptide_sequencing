#!/usr/bin/env python3
"""Backfill publication abstracts from bioRxiv, arXiv, OpenAlex and Crossref.

Run offline (not in CI). Abstracts are static once fetched, so this is closer
to a one-off than to the four recurring metric refreshes; re-run it after
adding papers.

Sources, tried in this order per publication, most authoritative first:

  1. bioRxiv API   (DOI prefix 10.1101 or 10.64898) - always carries the
                   abstract, and it is the version of record for the preprint.
  2. arXiv API     (DOI prefix 10.48550) - likewise always carries it.
  3. OpenAlex      via the `openalex_id` already stored in publication_impact,
                   falling back to a DOI lookup when there is none (a freshly
                   added paper has no id until the weekly impact refresh runs,
                   which is exactly when you want this script). Broadest
                   coverage, but the abstract arrives as an INVERTED INDEX
                   (word -> positions) and has to be reassembled in position
                   order. Elsevier and Springer Nature frequently withhold it,
                   which is why bioRxiv and arXiv are tried first.
  4. Crossref      `abstract`, which is JATS XML and needs its tags stripped.

Provenance goes in the new `publication.abstract_source` column, mirroring how
`publication_impact.match_method` records how a row was matched. Without it
there is no way to tell a hand-curated abstract from a scraped one, and this
script would eventually overwrite careful manual work.

Idempotent: a publication that already has an abstract is skipped unless
--force. That protects the hand-entered abstracts (borgonovo, pi-xNovo and
others) which are better than anything the APIs return.
"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "denovo.db"

USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)

BIORXIV_BASE = "https://api.biorxiv.org/details/biorxiv"
ARXIV_BASE = "http://export.arxiv.org/api/query"
OPENALEX_BASE = "https://api.openalex.org/works"
CROSSREF_BASE = "https://api.crossref.org/works"

REQUEST_DELAY = 0.2   # polite spacing; OpenAlex and Crossref both allow 10 req/sec
ARXIV_DELAY = 3.0     # arXiv explicitly asks for one request every 3 seconds

BIORXIV_PREFIXES = ("10.1101/", "10.64898/")
ARXIV_PREFIX = "10.48550/"

# Shortest plausible abstract. Anything below this is a stub or a stray label
# rather than real text, and storing it would be worse than storing nothing.
MIN_ABSTRACT_CHARS = 120

# Strings some publishers put in the abstract field instead of leaving it null.
PLACEHOLDERS = (
    "no abstract available",
    "abstract not available",
    "not available",
    "n/a",
)


def ensure_column(cur: sqlite3.Cursor) -> None:
    """Add publication.abstract_source if this is the first run."""
    cols = {row[1] for row in cur.execute("PRAGMA table_info(publication)")}
    if "abstract_source" not in cols:
        cur.execute("ALTER TABLE publication ADD COLUMN abstract_source TEXT")
        print("added column publication.abstract_source")


def clean(text: str | None) -> str | None:
    """Strip JATS/HTML markup and normalise whitespace; None if unusable."""
    if not text:
        return None
    text = html.unescape(text)
    # JATS abstracts open with a redundant <title>Abstract</title>.
    text = re.sub(r"<title>\s*abstract\s*</title>", " ", text, flags=re.I)
    # Paragraph and section breaks become spaces, not empty string, so words
    # on either side of a tag do not get glued together.
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    # Some records prefix the literal word "Abstract".
    text = re.sub(r"^abstract[:.\s]+", "", text, flags=re.I).strip()
    text = scrub_contacts(text)
    if len(text) < MIN_ABSTRACT_CHARS:
        return None
    if text.lower().rstrip(".") in PLACEHOLDERS:
        return None
    # Reject text that starts mid-sentence. Some OpenAlex records carry a
    # PARTIAL abstract_inverted_index holding only the tail of the abstract,
    # which reassembles into something that reads like a fragment ("peptide
    # sequencing tools, increasing both recall and..."). A fragment presented
    # as an abstract is worse than no abstract, so fall through to the next
    # source instead.
    if text[0].islower():
        return None
    return text


def scrub_contacts(text: str) -> str:
    """Drop author email addresses from abstract text.

    Structured abstracts in Bioinformatics and similar journals append a
    "Contact: someone@somewhere" clause. That is journal boilerplate rather
    than abstract prose, and these abstracts are rendered on crawlable,
    sitemap-listed pages, so republishing the addresses there is an invitation
    to scrapers. The "Availability and implementation" clause is kept, because
    the repository URL in it is genuinely useful.
    """
    text = re.sub(
        r"\s*Contact:\s*.*?(?=Supplementary information:|Availability|$)",
        " ", text, flags=re.I | re.S,
    )
    # Belt and braces for an address appearing outside a Contact: clause.
    text = re.sub(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}", "", text)
    text = re.sub(r"\s*;\s*(?=[.;]|$)", "", text)
    return re.sub(r"\s+", " ", text).strip(" ;,")


def get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30
        )
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def from_biorxiv(doi: str) -> str | None:
    data = get_json(f"{BIORXIV_BASE}/{doi}")
    if not data:
        return None
    collection = data.get("collection") or []
    if not collection:
        return None
    # The collection lists every posted version; the last is the most recent.
    return clean(collection[-1].get("abstract"))


def from_arxiv(doi: str) -> str | None:
    # DOIs are minted as 10.48550/arXiv.2512.12272, but the API's id_list wants
    # the bare 2512.12272. Leaving the prefix on returns an empty feed, which
    # silently falls through to OpenAlex instead of erroring.
    arxiv_id = re.sub(r"^arxiv\.", "", doi[len(ARXIV_PREFIX):], flags=re.I)
    try:
        r = requests.get(
            ARXIV_BASE,
            params={"id_list": arxiv_id, "max_results": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
    except (requests.RequestException, ET.ParseError):
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    node = root.find("a:entry/a:summary", ns)
    return clean(node.text if node is not None else None)


def from_openalex(openalex_id: str | None, doi: str | None = None) -> str | None:
    # Prefer the id already resolved by build_publication_impact.py, but fall
    # back to a DOI lookup. Without the fallback a freshly added paper gets
    # nothing from OpenAlex until the weekly impact refresh has assigned it an
    # id, which is precisely when you want to run this script.
    if openalex_id:
        data = get_json(f"{OPENALEX_BASE}/{openalex_id}")
    elif doi:
        data = get_json(f"{OPENALEX_BASE}/doi:{doi}")
    else:
        return None
    if not data:
        return None
    index = data.get("abstract_inverted_index")
    if not index:
        return None
    # word -> [positions]; invert it back into running text.
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        positions.extend((spot, word) for spot in spots)
    positions.sort()
    return clean(" ".join(word for _, word in positions))


def from_crossref(doi: str) -> str | None:
    data = get_json(f"{CROSSREF_BASE}/{doi}")
    if not data:
        return None
    return clean((data.get("message") or {}).get("abstract"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="refetch publications that already have an abstract "
             "(WILL overwrite hand-curated text)",
    )
    parser.add_argument(
        "--only", type=int, action="append", metavar="ID",
        help="restrict to these publication ids (repeatable)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and report, but write nothing",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_column(cur)
    conn.commit()

    rows = cur.execute(
        """
        SELECT p.id, p.title, p.doi, p.abstract, pi.openalex_id
        FROM publication p
        LEFT JOIN publication_impact pi ON pi.publication_id = p.id
        ORDER BY p.id
        """
    ).fetchall()

    if args.only:
        wanted = set(args.only)
        rows = [r for r in rows if r[0] in wanted]

    have_before = sum(1 for r in rows if r[3])
    todo = [r for r in rows if args.force or not r[3]]
    print(f"{len(rows)} publications, {have_before} already have an abstract, "
          f"{len(todo)} to fetch\n")

    counts: dict[str, int] = {}
    misses: list[tuple[int, str]] = []

    for idx, (pub_id, title, doi, _abstract, openalex_id) in enumerate(todo, 1):
        doi = (doi or "").strip()
        abstract, source = None, None

        if doi.startswith(BIORXIV_PREFIXES):
            abstract, source = from_biorxiv(doi), "biorxiv"
            time.sleep(REQUEST_DELAY)
        elif doi.startswith(ARXIV_PREFIX):
            abstract, source = from_arxiv(doi), "arxiv"
            time.sleep(ARXIV_DELAY)

        if not abstract and (openalex_id or doi):
            abstract, source = from_openalex(openalex_id, doi), "openalex"
            time.sleep(REQUEST_DELAY)

        if not abstract and doi:
            abstract, source = from_crossref(doi), "crossref"
            time.sleep(REQUEST_DELAY)

        label = title[:52]
        if abstract:
            counts[source] = counts.get(source, 0) + 1
            print(f"[{idx}/{len(todo)}] pub {pub_id}: {label} "
                  f"({source}, {len(abstract)} chars)")
            if not args.dry_run:
                cur.execute(
                    "UPDATE publication SET abstract = ?, abstract_source = ? "
                    "WHERE id = ?",
                    (abstract, source, pub_id),
                )
                conn.commit()
        else:
            misses.append((pub_id, title))
            print(f"[{idx}/{len(todo)}] pub {pub_id}: {label} (no abstract found)")

    total_after = cur.execute(
        "SELECT COUNT(*) FROM publication WHERE abstract IS NOT NULL AND abstract <> ''"
    ).fetchone()[0]
    n_pubs = cur.execute("SELECT COUNT(*) FROM publication").fetchone()[0]

    print(f"\nDone. {sum(counts.values())} fetched "
          f"({', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'}), "
          f"{len(misses)} without one.")
    print(f"Coverage: {total_after}/{n_pubs} publications have an abstract.")
    if misses:
        print("\nNo abstract found for these; fill by hand if they matter:")
        for pub_id, title in misses:
            print(f"  {pub_id:4d}  {title[:70]}")

    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        conn.rollback()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

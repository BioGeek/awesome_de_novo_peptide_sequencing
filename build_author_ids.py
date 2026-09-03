#!/usr/bin/env python3
"""Fill author.orcid and author.openalex_id from OpenAlex, matched per publication.

Run offline (not in CI). Re-run after adding papers.

WHY PER PUBLICATION. A name-based lookup would be unsafe in this catalog: three
different researchers are called Xiang Zhang, which is the whole reason the
author_display view and the disambiguator column exist. So this never searches
by name globally. For each publication it already has an OpenAlex id for, it
compares OUR author list for that paper against OPENALEX's authorship list for
the same paper. "Xiang Zhang on paper 4" and "Xiang Zhang on paper 199" are
therefore resolved independently and can land on different ORCIDs, which is the
correct behaviour.

WHAT IT REFUSES TO DO. Two conflict checks, because a wrong identifier is worse
than a missing one:

  * forward conflict: one of our authors matching two different ORCIDs across
    papers. Usually means two real people are still merged into one author row.
  * reverse conflict: one ORCID matching two of our author rows. Usually means
    one person is split across two rows, or a name match was too loose.

Neither is written. Both go to author_id_audit.csv, because each one is a
data-quality finding about the catalog rather than a lookup failure.

Google Scholar is deliberately NOT attempted. There is no public API, profile
ids are not programmatically discoverable, and scraping the profile search is
both blocked and against their terms. The handful of scholar_id values in the
catalog were verified by hand and should stay that way.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "denovo.db"
AUDIT_PATH = Path(__file__).parent / "author_id_audit.csv"

USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)
OPENALEX_BASE = "https://api.openalex.org/works"
REQUEST_DELAY = 0.12   # OpenAlex allows 10 req/sec for the polite pool


def ensure_columns(cur: sqlite3.Cursor) -> None:
    cols = {row[1] for row in cur.execute("PRAGMA table_info(author)")}
    for name in ("orcid", "openalex_id"):
        if name not in cols:
            cur.execute(f"ALTER TABLE author ADD COLUMN {name} TEXT")
            print(f"added column author.{name}")


def tokens(name: str) -> list[str]:
    """Lowercase ASCII name tokens, accents folded, initials dropped."""
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    parts = re.sub(r"[^a-z ]", " ", n.lower()).split()
    return [p for p in parts if len(p) > 1]      # drop single-letter initials


def same_person(a: str, b: str) -> bool:
    """Conservative name agreement for two authors of the SAME paper.

    Requires the surname (last multi-letter token) to match, plus at least one
    other token. "Wout Bittremieux" vs "W. Bittremieux" agrees on the surname
    only, so it is rejected: within one paper's author list a surname alone is
    too weak, and a missed match costs nothing while a wrong one is permanent.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    if ta[-1] != tb[-1]:
        return False
    return len(set(ta) & set(tb)) >= 2 or (ta == tb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="overwrite ids that are already set")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_columns(cur)
    conn.commit()

    works = cur.execute(
        "SELECT pi.publication_id, pi.openalex_id FROM publication_impact pi "
        "WHERE IFNULL(pi.openalex_id,'') <> '' ORDER BY pi.publication_id"
    ).fetchall()

    ours: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for pid, aid, name in cur.execute(
        "SELECT pa.publication_id, a.id, a.name FROM publication_author pa "
        "JOIN author a ON a.id = pa.author_id "
        "ORDER BY pa.publication_id, pa.author_order, a.id"
    ):
        ours[pid].append((aid, name))

    # author id -> {orcid: [publication ids]} so a conflict names its evidence
    orcid_votes: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    oa_votes: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    names = {aid: nm for aid, nm in cur.execute("SELECT id, name FROM author")}

    for idx, (pid, openalex_id) in enumerate(works, 1):
        try:
            r = requests.get(f"{OPENALEX_BASE}/{openalex_id}",
                             headers={"User-Agent": USER_AGENT}, timeout=30)
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            data = None
        time.sleep(REQUEST_DELAY)
        if not data:
            print(f"[{idx}/{len(works)}] pub {pid}: OpenAlex lookup failed")
            continue

        remote = []
        for a in data.get("authorships") or []:
            au = a.get("author") or {}
            remote.append((au.get("display_name") or "",
                           (au.get("orcid") or "").rsplit("/", 1)[-1] or None,
                           (au.get("id") or "").rsplit("/", 1)[-1] or None))

        matched = 0
        for aid, name in ours.get(pid, []):
            hits = [t for t in remote if same_person(name, t[0])]
            # Ambiguous within one paper: skip rather than guess.
            if len(hits) != 1:
                continue
            _rname, orcid, oa_id = hits[0]
            matched += 1
            if orcid:
                orcid_votes[aid][orcid].append(pid)
            if oa_id:
                oa_votes[aid][oa_id].append(pid)
        if idx % 25 == 0:
            print(f"[{idx}/{len(works)}] pub {pid}: {matched}/{len(ours.get(pid, []))} "
                  f"authors matched")

    # ---------------------------------------------------------------- resolve
    audit: list[dict] = []

    def resolve(votes, label):
        """id -> value, refusing anything ambiguous in either direction."""
        clean, forward_conflicts = {}, 0
        for aid, options in votes.items():
            if len(options) > 1:
                forward_conflicts += 1
                audit.append({
                    "kind": f"forward conflict ({label})",
                    "author_id": aid, "author": names.get(aid, "?"),
                    "values": "; ".join(f"{v} on pubs {sorted(p)}"
                                        for v, p in sorted(options.items())),
                })
                continue
            clean[aid] = next(iter(options))
        # reverse: one value claimed by several of our author rows
        owners = defaultdict(list)
        for aid, value in clean.items():
            owners[value].append(aid)
        reverse = {v: a for v, a in owners.items() if len(a) > 1}
        for value, aids in sorted(reverse.items()):
            audit.append({
                "kind": f"reverse conflict ({label})",
                "author_id": ";".join(map(str, sorted(aids))),
                "author": " | ".join(names.get(a, "?") for a in sorted(aids)),
                "values": value,
            })
        final = {aid: v for aid, v in clean.items() if v not in reverse}
        print(f"\n{label}: {len(final)} resolved, {forward_conflicts} forward "
              f"conflicts, {len(reverse)} reverse conflicts")
        return final

    orcids = resolve(orcid_votes, "orcid")
    oa_ids = resolve(oa_votes, "openalex_id")

    # ------------------------------------------------------------------ write
    written = {"orcid": 0, "openalex_id": 0}
    if not args.dry_run:
        for column, values in (("orcid", orcids), ("openalex_id", oa_ids)):
            for aid, value in sorted(values.items()):
                existing = cur.execute(
                    f"SELECT IFNULL({column},'') FROM author WHERE id = ?", (aid,)
                ).fetchone()[0]
                if existing and not args.force:
                    continue
                if existing == value:
                    continue
                cur.execute(f"UPDATE author SET {column} = ? WHERE id = ?", (value, aid))
                written[column] += 1
        conn.commit()

    if audit:
        audit.sort(key=lambda r: (r["kind"], str(r["author_id"])))
        with AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(audit[0].keys()))
            w.writeheader()
            w.writerows(audit)

    total = cur.execute("SELECT COUNT(*) FROM author").fetchone()[0]
    have = cur.execute("SELECT COUNT(*) FROM author WHERE IFNULL(orcid,'') <> ''").fetchone()[0]
    print(f"\nDone. wrote {written['orcid']} orcid, {written['openalex_id']} openalex_id.")
    print(f"Coverage: {have}/{total} authors have an ORCID.")
    if audit:
        print(f"{len(audit)} conflicts written to {AUDIT_PATH.name}, none applied. "
              f"Each is a data-quality finding worth reading.")
    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        conn.rollback()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

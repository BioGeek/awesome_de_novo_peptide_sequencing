#!/usr/bin/env python3
"""Fill affiliations, departments, cities and countries from OpenAlex.

Run offline (not in CI). Re-run after adding papers.

WHY THIS EXISTS. Until now nothing wrote an affiliation: `affiliation`,
`author_affiliation`, `city` and `country` were the only entity tables with no
builder at all, so every institution, department, city and country in the
catalog was typed in by hand from a paper's byline. One commit in the log is a
single affiliation row plus a 25-line message reasoning about which of three
authors a dagger symbol in a 1999 ACS byline referred to.

Meanwhile the data was already being downloaded and thrown away.
`build_author_ids.py` fetches the full OpenAlex Work and loops `authorships[]`,
reading only `author.{display_name, orcid, id}`; on the same matched authorship
it discards `institutions[]` (`display_name`, `ror`, `country_code`) and
`raw_affiliation_strings`. Measured on a 30-paper sample: 93% of authorships
carry an institution with a ROR and a country code, and 95% carry a raw
affiliation string.

IDENTITY IS THE ROR, NOT THE NAME. The measured disagreement between OpenAlex
and the curated rows (13% of authorships) is not error, it is two different
facts wearing one column:

    affiliation.name            as PRINTED on the paper (curator's, displayed)
    affiliation.canonical_name  current legal name       (OpenAlex's, not shown)
    affiliation.ror             identity                 (neither, immortal)

"Swiss Federal Institute of Technology" is what the 1999 byline says;
"Ecole Polytechnique Federale de Lausanne" is what EPFL is called now. Both are
right. Keyed on `https://ror.org/02s376052` they stop competing, and the site
keeps saying what the paper said. Same for "Amgen Inc." vs "Amgen (United
States)".

The ROR also permanently fixes the duplicate problem that has cost five
hand-merge commits so far ("Sun Yat-Sen" vs "Sun Yat-sen"): two spellings, one
ROR, one row.

WHAT IT REFUSES TO DO. Every writable column has a sibling `*_source`, and a
NULL source means a human typed it. The rule, borrowed verbatim from
`build_abstracts.py`'s `abstract_source` convention:

    write only if the value is empty, OR its *_source is not NULL

so hand-curated text is never overwritten without --force. Everything it would
have changed goes to affiliation_audit.csv instead.

Institution resolution is deliberately three-tiered, because merging two real
universities into one row is invisible and destroys curated data:

  1. by ROR                                        -> write
  2. exact normalised name match to name/canonical_name  -> write, backfill ROR
  3. our affiliation.name appears verbatim in the authorship's raw affiliation
     string AND that authorship has exactly one institution -> write, backfill
     ROR, store OpenAlex's name as canonical_name. This is the EPFL/Amgen case:
     the paper's own byline text contains our spelling, and there is only one
     institution it can bind to.
  4. anything else -> audit CSV, no write. No fuzzy search hit is ever accepted
     silently.

DEPARTMENT IS PARSED, SO IT IS BIASED TOWARD NULL. OpenAlex has no structured
department field; it exists only inside `raw_affiliation_strings`. Because
`affiliation` is UNIQUE(name, department), a WRONG department does not just
mislabel a row, it creates a duplicate institution and pollutes the institution
page set and the geo chart. A MISSING department costs one blank table cell. So
a segment must clear three tests (see `parse_department`) or it is dropped.

CITY AND COORDINATES come from /institutions/{ror}, whose `geo` block is a 1:1
fit for the city table. This is the immediately visible win: 106 of 256 cities
had no lat/lng, so they were missing from the map.

PER-BYLINE, NOT PER-AUTHOR. `author_affiliation` is author-level, but OpenAlex
is per-(paper, author), and 372 of 1274 authors carry more than one affiliation
(max 7). So joining publication_author -> author_affiliation returns every
institution an author was EVER given, not the one on that byline, which makes
any per-paper geography claim wrong. Rather than migrate 618 hand-curated rows
on a guess, this writes a new `publication_author_affiliation` table and leaves
`author_affiliation` alone. Pages and charts can prefer the precise row where it
exists and fall back to the author-level one.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

from build_author_ids import same_person, tokens  # noqa: F401  (tokens re-exported)

DB_PATH = Path(__file__).parent / "denovo.db"
AUDIT_PATH = Path(__file__).parent / "affiliation_audit.csv"
CACHE_DIR = Path(__file__).parent / ".cache" / "openalex"

USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)
WORKS_BASE = "https://api.openalex.org/works"
INSTITUTIONS_BASE = "https://api.openalex.org/institutions"
REQUEST_DELAY = 0.12   # OpenAlex allows 10 req/sec for the polite pool

# Organisational-unit words that mark a department segment. Multilingual because
# the catalog's bylines are: Institut, Laboratoire, Facultad, Abteilung all occur.
UNIT_WORDS = re.compile(
    r"\b(depart(e)?ment|dept\.?|institut(e|o)?|school|faculty|facultad|"
    r"laborator(y|ies)|laboratoire|lab|division|divisione|centre|center|"
    r"centro|unit|chair|abteilung|college|programme?|graduate\s+school|"
    r"research\s+group|section)\b",
    re.I,
)

# The curator's country shorthand, which must survive: geo.country says
# "United Kingdom" where this catalog says "UK". Matching on ISO-2 means the
# display name is never touched.
ISO2_OVERRIDES = {
    "UK": "GB", "USA": "US", "UAE": "AE", "Russia": "RU",
    "South Korea": "KR", "Czech Republic": "CZ", "Iran": "IR",
    "Taiwan": "TW", "Vietnam": "VN", "Hong Kong": "HK",
}


# --------------------------------------------------------------------------
# schema (additive only: nothing existing is dropped or rewritten)
# --------------------------------------------------------------------------

def ensure_schema(cur: sqlite3.Cursor) -> None:
    def add(table: str, column: str, decl: str = "TEXT") -> None:
        cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            print(f"added column {table}.{column}")

    add("affiliation", "ror")
    add("affiliation", "canonical_name")
    add("affiliation", "name_source")
    add("affiliation", "department_source")
    add("affiliation", "geo_source")
    add("country", "iso2")
    add("city", "lat_source")
    add("city", "lng_source")

    # NOT unique. A ROR identifies an INSTITUTION, but this table's grain is
    # (institution, department): 106 institutions have more than one row, and
    # Technical University of Munich alone has ten. A unique index here made
    # every multi-row institution unresolvable.
    cur.execute("DROP INDEX IF EXISTS ux_affiliation_ror")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_affiliation_ror "
                "ON affiliation(ror) WHERE ror IS NOT NULL")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_country_iso2 "
                "ON country(iso2) WHERE iso2 IS NOT NULL")

    # Per-byline affiliations. Builder-owned and single-writer, so it satisfies
    # the one-table-per-workflow invariant .github/actions/commit-refreshed-db
    # depends on if this is ever put on a schedule.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS publication_author_affiliation (
            publication_id INTEGER NOT NULL REFERENCES publication(id) ON DELETE CASCADE,
            author_id      INTEGER NOT NULL REFERENCES author(id)      ON DELETE CASCADE,
            affiliation_id INTEGER NOT NULL REFERENCES affiliation(id) ON DELETE CASCADE,
            position       INTEGER NOT NULL DEFAULT 0,
            source         TEXT,
            PRIMARY KEY (publication_id, author_id, affiliation_id)
        )
    """)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def norm(text: str | None) -> str:
    """Casefolded, accent-folded, punctuation-stripped form for name equality."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\b(the|of|at|for|and)\b", " ", t.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def strip_country_suffix(name: str) -> str:
    """OpenAlex disambiguates corporates as "Amgen (United States)"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def parse_department(raw: str, inst_names: list[str]) -> str | None:
    """Best-effort department from a raw affiliation string, or None.

    Three tests, all required, because a wrong department creates a duplicate
    institution row while a missing one costs a blank cell:

      1. the segment names an organisational unit (UNIT_WORDS)
      2. the SAME raw string also names the institution, so we know this string
         belongs to the authorship we are attributing it to
      3. exactly one segment qualifies, so there is nothing to choose between
    """
    if not raw or not inst_names:
        return None
    if not any(norm(n) and norm(n) in norm(raw) for n in inst_names):
        return None

    segments = [s.strip() for s in re.split(r"[;,·]", raw) if s.strip()]
    hits = [
        s for s in segments
        if UNIT_WORDS.search(s)
        # A segment that IS the institution is not its department.
        and not any(norm(s) == norm(n) for n in inst_names)
        # Addresses and postcodes are not departments.
        and not re.search(r"\d{4}", s)
    ]
    if len(hits) != 1:
        return None
    dept = hits[0]
    return dept if 3 <= len(dept) <= 120 else None


def pick_row(rows: list[dict], raw: str) -> dict:
    """Choose which (institution, department) row a byline binds to.

    `affiliation` conflates institution and department, so resolving an
    OpenAlex institution gives 1..10 candidate rows. Prefer the row whose
    department is actually named in the byline text, then the row with no
    department (the institution as a whole), then the lowest id so the choice
    is stable. slugs.py keys institution PAGES by MIN(id) over the same
    grouping, so that fallback agrees with the published URL.
    """
    nraw = norm(raw)
    if nraw:
        named = [r for r in rows if r["department"] and norm(r["department"]) in nraw]
        if named:
            return min(named, key=lambda r: r["id"])
    plain = [r for r in rows if not r["department"]]
    if plain:
        return min(plain, key=lambda r: r["id"])
    return min(rows, key=lambda r: r["id"])


def cached_get(url: str, key: str) -> dict | None:
    """GET with an on-disk cache. Institutions are static, so no expiry."""
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        data = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        data = None
    time.sleep(REQUEST_DELAY)
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return data


def ror_key(ror: str) -> str:
    return ror.rstrip("/").rsplit("/", 1)[-1]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true",
                        help="apply changes (default: report only, change nothing)")
    parser.add_argument("--force", action="store_true",
                        help="also overwrite hand-curated values (rarely correct)")
    parser.add_argument("--only-new", action="store_true",
                        help="only publications with no per-byline affiliation yet")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N publications (for a quick look)")
    parser.add_argument("--no-geo", action="store_true",
                        help="skip the /institutions lookups that fill city lat/lng")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()

    audit: list[dict] = []

    # --- existing state ---------------------------------------------------
    affs: list[dict] = [
        {"id": r[0], "name": r[1], "department": r[2], "ror": r[3],
         "canonical_name": r[4], "name_source": r[5], "department_source": r[6]}
        for r in cur.execute(
            "SELECT id, name, department, ror, canonical_name, name_source, "
            "department_source FROM affiliation ORDER BY id")
    ]
    # Indexed by INSTITUTION, not by row: `affiliation` conflates institution and
    # department, so one institution is 1..10 rows and matching must resolve to
    # the institution first and then choose a row.
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for a in affs:
        by_inst[norm(a["name"])].append(a)
        if a["canonical_name"] and norm(a["canonical_name"]) != norm(a["name"]):
            by_inst[norm(a["canonical_name"])].append(a)
    by_ror: dict[str, list[dict]] = defaultdict(list)
    for a in affs:
        if a["ror"]:
            by_ror[a["ror"]].append(a)

    ours: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for pid, aid, name in cur.execute(
        "SELECT pa.publication_id, a.id, a.name FROM publication_author pa "
        "JOIN author a ON a.id = pa.author_id "
        "ORDER BY pa.publication_id, pa.author_order, a.id"
    ):
        ours[pid].append((aid, name))

    works = cur.execute(
        "SELECT pi.publication_id, pi.openalex_id FROM publication_impact pi "
        "WHERE IFNULL(pi.openalex_id,'') <> '' ORDER BY pi.publication_id"
    ).fetchall()

    if args.only_new:
        done = {r[0] for r in cur.execute(
            "SELECT DISTINCT publication_id FROM publication_author_affiliation")}
        works = [w for w in works if w[0] not in done]
    if args.limit:
        works = works[:args.limit]

    print(f"{len(works)} publications with an OpenAlex id to inspect\n")

    # --- walk the works ---------------------------------------------------
    seen_ror: dict[str, str] = {}        # ror -> OpenAlex display_name
    ror_country: dict[str, str] = {}     # ror -> country_code
    byline: list[tuple[int, int, int, int]] = []   # pub, author, aff, position
    new_affs: dict[tuple[str, str | None], dict] = {}
    stats = defaultdict(int)

    for idx, (pid, oa_id) in enumerate(works, 1):
        data = cached_get(f"{WORKS_BASE}/{oa_id}", f"works/{oa_id}")
        if not data:
            print(f"[{idx}/{len(works)}] pub {pid}: lookup failed")
            stats["lookup_failed"] += 1
            continue

        remote = data.get("authorships") or []
        matched = 0
        for aid, our_name in ours.get(pid, []):
            hits = [a for a in remote
                    if same_person(our_name, ((a.get("author") or {}).get("display_name") or ""))]
            if len(hits) != 1:           # ambiguous within one paper: never guess
                stats["author_ambiguous"] += 1
                continue
            hit = hits[0]
            matched += 1
            insts = hit.get("institutions") or []
            raws = hit.get("raw_affiliation_strings") or []
            raw = "; ".join(raws)

            if not insts:
                stats["authorship_no_institution"] += 1
                continue

            for pos, inst in enumerate(insts):
                ror = inst.get("ror") or None
                oa_name = inst.get("display_name") or ""
                cc = inst.get("country_code") or None
                if ror:
                    seen_ror[ror] = oa_name
                    if cc:
                        ror_country[ror] = cc

                rows: list[dict] = []
                tier = None

                # tier 1: known ROR
                if ror and by_ror.get(ror):
                    rows, tier = by_ror[ror], "ror"

                # tier 2: exact normalised institution-name match
                if not rows:
                    for candidate_name in (oa_name, strip_country_suffix(oa_name)):
                        hit = by_inst.get(norm(candidate_name))
                        if hit:
                            rows, tier = hit, "name"
                            break

                # tier 3: our as-printed name appears verbatim in the byline
                # text, and there is exactly one institution to bind it to.
                # Longest match wins; two equally-long different institutions
                # in one string is ambiguous, so it is refused.
                if not rows and len(insts) == 1 and raw:
                    nraw = norm(raw)
                    present = sorted({norm(a["name"]) for a in affs
                                      if norm(a["name"]) and norm(a["name"]) in nraw},
                                     key=len, reverse=True)
                    if present:
                        longest = [n for n in present if len(n) == len(present[0])]
                        if len(longest) == 1:
                            rows, tier = by_inst.get(longest[0], []), "raw-string"

                if not rows:
                    stats["institution_unresolved"] += 1
                    audit.append({
                        "kind": "unresolved-institution",
                        "publication_id": pid, "author_id": aid,
                        "our_value": "", "openalex_value": oa_name,
                        "ror": ror or "", "evidence": raw[:300],
                    })
                    key = (oa_name, None)
                    new_affs.setdefault(key, {
                        "name": oa_name, "ror": ror, "country_code": cc,
                        "raw": raw,
                    })
                    continue

                target = pick_row(rows, raw)
                stats[f"resolved_{tier}"] += 1
                byline.append((pid, aid, target["id"], pos))

                # Backfill the ROR onto EVERY row of this institution, not just
                # the one this byline chose: the ROR identifies the institution,
                # and its department rows are the same institution.
                #
                # Tiers 1 and 2 ONLY. Tier 3 establishes that a byline belongs to
                # one of our rows, because our as-printed name appears in the
                # paper's own affiliation string. It does NOT establish that our
                # row is the same organisation OpenAlex listed: a single raw
                # string routinely names a company AND a funding body, so
                # trusting it bound "Baizhen Biotechnologies Inc." to the Wuhan
                # Science and Technology Bureau's ROR. The byline link is still
                # worth keeping; the identity claim is not.
                if ror and tier in ("ror", "name"):
                    to_fill = [r for r in rows if not r["ror"]]
                    if to_fill:
                        if args.write:
                            cur.executemany(
                                "UPDATE affiliation SET ror = ?, "
                                "canonical_name = COALESCE(canonical_name, ?) "
                                "WHERE id = ?",
                                [(ror, oa_name, r["id"]) for r in to_fill])
                        for r in to_fill:
                            r["ror"] = ror
                        by_ror[ror] = rows
                        stats["ror_backfilled"] += len(to_fill)
                    if norm(target["name"]) != norm(oa_name):
                        audit.append({
                            "kind": "name-differs",
                            "publication_id": pid, "author_id": aid,
                            "our_value": target["name"], "openalex_value": oa_name,
                            "ror": ror, "evidence": "as-printed kept, canonical stored",
                        })

                # department, only where we have none
                if not target["department"]:
                    names = [n for n in (target["name"], target["canonical_name"],
                                         oa_name) if n]
                    dept = parse_department(raw, names)
                    if dept:
                        stats["department_parsed"] += 1
                        audit.append({
                            "kind": "department-parsed",
                            "publication_id": pid, "author_id": aid,
                            "our_value": "", "openalex_value": dept,
                            "ror": ror or "", "evidence": raw[:300],
                        })
                        # NOT written: affiliation is UNIQUE(name, department), so
                        # setting it here would collide with, or duplicate, an
                        # existing row. Review the CSV and apply deliberately.

        if matched == 0 and ours.get(pid):
            stats["publication_no_author_match"] += 1
        if idx % 25 == 0 or idx == len(works):
            print(f"[{idx}/{len(works)}] {len(byline)} byline affiliations, "
                  f"{stats['institution_unresolved']} unresolved")

    # --- geography --------------------------------------------------------
    geo_updates = 0
    if not args.no_geo and seen_ror:
        print(f"\nresolving geography for {len(seen_ror)} institutions")
        iso_to_country = {
            r[1]: r[0] for r in cur.execute(
                "SELECT id, iso2 FROM country WHERE IFNULL(iso2,'') <> ''")
        }
        # Seed iso2 from the curator's names, honouring the shorthand.
        # fetchall() first: issuing an UPDATE on the same cursor mid-iteration
        # invalidates it, which silently set exactly one country instead of all.
        for cid, cname in cur.execute("SELECT id, name FROM country").fetchall():
            iso = ISO2_OVERRIDES.get(cname)
            if iso and iso not in iso_to_country:
                if args.write:
                    cur.execute("UPDATE country SET iso2 = ? WHERE id = ?", (iso, cid))
                iso_to_country[iso] = cid
                stats["country_iso2_set"] += 1

        for ror in sorted(seen_ror):
            inst = cached_get(f"{INSTITUTIONS_BASE}/{ror}",
                              f"institutions/{ror_key(ror)}")
            geo = (inst or {}).get("geo") or {}
            city_name, lat, lng = geo.get("city"), geo.get("latitude"), geo.get("longitude")
            if not city_name:
                continue
            row = cur.execute(
                "SELECT id, lat, lng FROM city WHERE name = ? COLLATE NOCASE "
                "ORDER BY id LIMIT 1", (city_name,)).fetchone()
            if not row:
                stats["city_absent"] += 1
                audit.append({
                    "kind": "city-not-in-catalog",
                    "publication_id": "", "author_id": "",
                    "our_value": "", "openalex_value": city_name,
                    "ror": ror, "evidence": f"lat={lat} lng={lng}",
                })
                continue
            cid, have_lat, have_lng = row
            if (have_lat is None or have_lng is None) and lat and lng:
                if args.write:
                    cur.execute(
                        "UPDATE city SET lat = COALESCE(lat, ?), lng = COALESCE(lng, ?), "
                        "lat_source = COALESCE(lat_source, 'openalex'), "
                        "lng_source = COALESCE(lng_source, 'openalex') WHERE id = ?",
                        (lat, lng, cid))
                geo_updates += 1

    # --- write the per-byline table --------------------------------------
    inserted = 0
    if args.write and byline:
        for pid, aid, affid, pos in byline:
            cur.execute(
                "INSERT OR IGNORE INTO publication_author_affiliation "
                "(publication_id, author_id, affiliation_id, position, source) "
                "VALUES (?, ?, ?, ?, 'openalex')", (pid, aid, affid, pos))
            inserted += cur.rowcount
        conn.commit()
    elif args.write:
        conn.commit()

    # --- audit ------------------------------------------------------------
    if audit:
        with AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(audit[0].keys()))
            w.writeheader()
            w.writerows(audit)

    # --- report -----------------------------------------------------------
    print("\n--- summary ---")
    for key in sorted(stats):
        print(f"  {key:32s} {stats[key]}")
    print(f"  {'byline rows resolved':32s} {len(byline)}")
    print(f"  {'city coordinates fillable':32s} {geo_updates}")
    print(f"  {'institutions OpenAlex knows':32s} {len(seen_ror)}")
    if audit:
        print(f"\n{len(audit)} findings written to {AUDIT_PATH.name}")
    if args.write:
        print(f"\nWROTE: {inserted} publication_author_affiliation rows.")
        print("Departments are NEVER auto-written: review the CSV and apply by hand.")
    else:
        print("\nReport only, nothing written. Re-run with --write to apply.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

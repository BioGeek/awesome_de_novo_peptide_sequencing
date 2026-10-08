#!/usr/bin/env python3
"""URL slugs for the generated entity pages.

Imported by both `build_pages.py` (which writes the pages) and, eventually, the
Python chunk in `index.qmd` (which needs the same slugs to link INTO them). One
module so the two can never disagree: a mismatch would render a link to a page
that does not exist.

Run this file directly to audit the whole catalog:

    python3 scripts/slugs.py

It prints one line per entity type and exits non-zero if any slug had to fall
back to an id suffix, which is the signal that either the disambiguation policy
needs extending or the DB has a duplicate that should be merged instead.

SLUGS.LOCK
----------
Every slug is a published URL, and 2400-odd of them are indexed by Google. A
slug is derived from mutable data -- a paper's title, an author's name, an
algorithm's name -- so an innocuous edit silently rewrites a URL, 404s the old
one and throws away whatever search equity it had. That is invisible at commit
time and expensive months later.

    python3 scripts/slugs.py --check     # fail if any existing URL changed or vanished
    python3 scripts/slugs.py --write     # accept the current slugs as the new baseline

`slugs.lock` is a committed record of every (type, id) -> slug. `--check` is a
backstop in CI and in the pre-commit hook; `--write` is deliberate and manual.

It is deliberately NOT auto-refreshed by the hook, unlike check_counts.py.
Rewriting the baseline automatically is exactly the failure being guarded
against: it would turn a URL change from a loud error into a silent one. When a
rename is intended, run --write and let the lock diff record it.
"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "denovo.db"
LOCK_PATH = Path(__file__).resolve().parent.parent / "slugs.lock"

MAX_SLUG_CHARS = 80

# Characters that survive NFKD folding and would otherwise be silently dropped.
# Verified against every publication title, author name, algorithm name,
# institution name and venue in the catalog: this is the complete set, and
# dropping them would be a lie rather than a simplification (pi-HelixNovo would
# become "helixnovo").
TRANSLITERATE = {
    "π": "pi",
    "—": "-",
    "–": "-",
    "ø": "o", "Ø": "O",
    "ß": "ss",
    "ı": "i",
    # Stroke and ligature letters do NOT decompose under NFKD, so without an
    # explicit mapping they are stripped as punctuation and silently mangle a
    # name: "Ruder Boskovic" would slug to "ru-er-boskovic". Only the first
    # group occurs in the catalog today; the rest are here because the failure
    # is silent and the next Polish or Maltese affiliation should not hit it.
    #
    # Croatian d-stroke maps to "dj" rather than "d" following the convention
    # the institute's own English-language materials use, which also keeps the
    # existing published slug stable.
    "đ": "dj", "Đ": "Dj",
    "ł": "l", "Ł": "L",
    "ħ": "h", "Ħ": "H",
    "ŧ": "t", "Ŧ": "T",
    "æ": "ae", "Æ": "Ae",
    "œ": "oe", "Œ": "Oe",
    "þ": "th", "Þ": "Th",
    "ð": "d", "Ð": "D",
}


# RETIRED URLS, each redirecting to its successor. kind -> {old slug: new slug}.
# A slug change is deliberate (slugs.py --write) but the old URL is published
# and indexed, so build_pages.py gives the successor page a Quarto `aliases:`
# entry, which writes a redirect page at the old address. Add to this, never
# prune it: an old URL can be linked from anywhere for years.
#
# 2026-10-05: titles carrying publisher markup ("<i>de novo</i>") had slugged
# to "...-i-de-novo-i-...", and two journal names stored as "&amp;" had their
# own venue pages. slugify() now strips markup and entities, and the two
# journal names were decoded into the real venues.
#
# 2026-10-05: a duplicate-author sweep merged 28 rows into the person they
# duplicate (name variants sharing co-authors or an institution, and three
# same-name splits the evidence contradicted). Each retired author URL points
# at the surviving row's page.
#
# 2026-10-05: "GenomicPeptideFinder de novo genomic mining" (2006) and
# "Genomic Peptide Finder" (2011) were one tool from one lab entered twice; the
# 2006 row was folded into the 2011 one, which carries the repository.
# Yi Liu (Western Ontario) was the same person as the ORCID Yi Liu row.
# "Liu Yang" was SSRN's reversed spelling of PeposX-Exhaust's 6th author, Yang Liu.
# "PNAS" was one paper's spelling of a venue eight others give in full.
# Nine author names were stored "Surname, Given"; four were people already here.
REDIRECTS: dict[str, dict[str, str]] = {'algorithms': {'genomicpeptidefinder-de-novo-genomic-mining': 'genomic-peptide-finder'},
 'authors': {'beatrix-m-ueberheide': 'beatrix-ueberheide',
             'binhai-zhu': 'binhai-zhu-montana-state-university',
             'calomeno-celso-vitor-alves-queiroz': 'celso-vitor-a-q-calomeno',
             'd-dutta': 'debojyoti-dutta',
             'darville-bowleg-lancia-nadinia-fallen': 'lancia-n-f-darville',
             'e-mori': 'elisa-mori',
             'e-v-grishin': 'eugene-v-grishin',
             'fanny-guzman-2964': 'fanny-guzman',
             'h-park': 'heejin-park',
             'j-jeong': 'jaeho-jeong',
             'j-seo': 'jangho-seo',
             'j-v-olsen': 'jesper-v-olsen',
             'jonathan-krieger': 'jonathan-r-krieger',
             'kleffmann-torsten': 'torsten-kleffmann',
             'liu-yang': 'yang-liu',
             'moeke-cassidy': 'cassidy-moeke',
             'muller-matthias': 'matthias-muller',
             'nan-liu': 'nan-liu-shandong-jianzhu-university',
             'natalie-e-castellana': 'natalie-castellana',
             'pavel-a-pevzner-1448': 'pavel-a-pevzner',
             'pavel-pevzner': 'pavel-a-pevzner',
             'penna-paolo': 'paolo-penna',
             'pieter-c-dorrestein-1453': 'pieter-c-dorrestein',
             'polonca-trebse-1667': 'polonca-trebse',
             'r-a-zubarev': 'roman-a-zubarev',
             'r-day': 'r-m-day',
             't-a-egorov': 'tsezi-a-egorov',
             'tatiana-y-samgina': 'tatiana-yu-samgina',
             'victoria-c-pham': 'victoria-pham',
             'vladimir-havlicek-3200': 'vladimir-havlicek',
             'warren-rene-l': 'rene-l-warren',
             'wen-ting-li': 'wenting-li',
             'wendy-n-sandoval': 'wendy-sandoval',
             'wilfred-tang': 'wilfred-h-tang',
             'wilson-susan': 'susan-r-wilson',
             'yi-liu-western-ontario': 'yi-liu',
             'yuanliang-zhang-hong-kong-polytechnic-university': 'yuanliang-zhang',
             'zomaya-albert-y': 'albert-y-zomaya'},
 'publications': {'193-nm-ultraviolet-photodissociation-of-imidazolinylated-lys-n-peptides-for-i': '193-nm-ultraviolet-photodissociation-of-imidazolinylated-lys-n-peptides-for-de',
                  'an-improved-method-for-i-de-novo-i-sequencing-of-arginine-containing-n-sup-sup': 'an-improved-method-for-de-novo-sequencing-of-arginine-containing-n-tris-2-4-6',
                  'analysis-of-root-plasma-membrane-aquaporins-from-i-brassica-oleracea-i-post': 'analysis-of-root-plasma-membrane-aquaporins-from-brassica-oleracea-post',
                  'development-of-a-host-blood-meal-database-i-de-novo-i-sequencing-of-hemoglobin': 'development-of-a-host-blood-meal-database-de-novo-sequencing-of-hemoglobin-from',
                  'extensive-i-de-novo-i-sequencing-of-new-parvalbumin-isoforms-using-a-novel': 'extensive-de-novo-sequencing-of-new-parvalbumin-isoforms-using-a-novel',
                  'functional-peptidomics-analysis-of-italic-saccharomyces-pastorianus-italic': 'functional-peptidomics-analysis-of-saccharomyces-pastorianus-protein',
                  'high-resolution-mass-spectrometry-and-partial-i-de-novo-i-sequencing-constitute': 'high-resolution-mass-spectrometry-and-partial-de-novo-sequencing-constitute-a',
                  'i-de-novo-i-sequencing-and-characterization-of-a-novel-bowman-birk-inhibitor': 'de-novo-sequencing-and-characterization-of-a-novel-bowman-birk-inhibitor-from',
                  'i-de-novo-i-sequencing-of-a-21-kda-cytochrome-i-c-i-sub-4-sub-from-i-thiocapsa': 'de-novo-sequencing-of-a-21-kda-cytochrome-c-4-from-thiocapsa-roseopersicina-by',
                  'i-de-novo-i-sequencing-of-antimicrobial-peptides-isolated-from-the-venom-glands': 'de-novo-sequencing-of-antimicrobial-peptides-isolated-from-the-venom-glands-of',
                  'i-de-novo-i-sequencing-of-novel-neuropeptides-directly-from-i-ascaris-suum-i': 'de-novo-sequencing-of-novel-neuropeptides-directly-from-ascaris-suum-tissue',
                  'i-de-novo-i-sequencing-of-two-new-cyclic-i-i-defensins-from-baboon-i-papio': 'de-novo-sequencing-of-two-new-cyclic-defensins-from-baboon-papio-hamadryas',
                  'identification-of-proteins-induced-by-polycyclic-aromatic-hydrocarbon-in-b-i': 'identification-of-proteins-induced-by-polycyclic-aromatic-hydrocarbon-in',
                  'ltq-orbitrap-velos-in-routine-i-de-novo-i-sequencing-of-non-tryptic-skin': 'ltq-orbitrap-velos-in-routine-de-novo-sequencing-of-non-tryptic-skin-peptides',
                  'lys-tag-an-easy-and-robust-chemical-modification-for-improved-i-de-novo-i': 'lys-tag-an-easy-and-robust-chemical-modification-for-improved-de-novo',
                  'manual-mass-spectrometry-i-de-novo-i-sequencing-of-the-anionic-host-defense': 'manual-mass-spectrometry-de-novo-sequencing-of-the-anionic-host-defense',
                  'mining-novel-allergens-from-coconut-pollen-employing-manual-i-de-novo-i': 'mining-novel-allergens-from-coconut-pollen-employing-manual-de-novo-sequencing',
                  'top-down-analysis-of-protein-samples-by-i-de-novo-i-sequencing-techniques': 'top-down-analysis-of-protein-samples-by-de-novo-sequencing-techniques',
                  'top-down-i-de-novo-i-protein-sequencing-of-a-13-6-kda-camelid-single-heavy': 'top-down-de-novo-protein-sequencing-of-a-13-6-kda-camelid-single-heavy-chain'},
 'venues': {'molecular-amp-cellular-proteomics': 'molecular-cellular-proteomics',
            'pnas': 'proceedings-of-the-national-academy-of-sciences',
            'protein-amp-peptide-letters': 'protein-peptide-letters'}}


def slugify(text: str, maxlen: int = MAX_SLUG_CHARS) -> str:
    """ASCII kebab-case slug. Raises on input that cannot produce one."""
    if not text or not text.strip():
        raise ValueError("cannot slugify empty text")
    # Publisher markup is typography, not words: "<i>de novo</i>" used to slug
    # to "i-de-novo-i". Strip tags (and entities) before anything else.
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(text)))
    for src, dst in TRANSLITERATE.items():
        text = text.replace(src, dst)
    # Before stripping punctuation, so InstaNovo and InstaNovo+ stay distinct.
    text = text.replace("+", "-plus")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(slug) > maxlen:
        # Cut on a word boundary; a mid-word cut reads like a typo. Falls back
        # to a hard cut for a single token longer than maxlen.
        head = slug[:maxlen]
        slug = head.rsplit("-", 1)[0] if "-" in head else head
    slug = slug.strip("-")
    if not slug:
        raise ValueError(f"slugified to nothing: {text!r}")
    return slug


def assign_unique(
    entries: list[tuple[int, str]],
    suffix_of: dict[int, str] | None = None,
    maxlen: int = MAX_SLUG_CHARS,
) -> tuple[dict[int, str], list[int]]:
    """Map entity id -> unique slug.

    Collisions are resolved by GROUP, not in iteration order. That matters: a
    first version of this walked entries by ascending id and let the lowest id
    keep the bare slug, which put "-preprint" on whichever half happened to be
    inserted second. Six publications ended up falling back to id suffixes
    purely because the journal version was added after its preprint.

    Instead, every member of a colliding group that has a SEMANTIC disambiguator
    (from `suffix_of`, e.g. "preprint") takes `base-suffix`, and the one member
    without takes the bare slug. So the journal version always owns the clean
    URL and the preprint is always explicitly marked, whatever order they were
    entered in. Verified: all 19 colliding publication titles are exactly one
    preprint plus one non-preprint, so this resolves every case.

    Returns (slugs, fell_back). A non-empty `fell_back` means a group had two or
    more members with no way to tell them apart, which is a smell: extend the
    policy, or merge what is probably a duplicate.
    """
    suffix_of = suffix_of or {}

    groups: dict[str, list[int]] = {}
    order: list[int] = []
    for entity_id, text in entries:
        groups.setdefault(slugify(text, maxlen), []).append(entity_id)
        order.append(entity_id)

    slugs: dict[int, str] = {}
    fell_back: list[int] = []

    for base, ids in groups.items():
        if len(ids) == 1:
            slugs[ids[0]] = base
            continue
        bare_taken = False
        for entity_id in sorted(ids):
            semantic = suffix_of.get(entity_id)
            if semantic:
                suffix = slugify(semantic)
                # Re-truncate the base so base+suffix still fits the limit.
                head = slugify(base, max(8, maxlen - len(suffix) - 1))
                slugs[entity_id] = f"{head}-{suffix}"
            elif not bare_taken:
                slugs[entity_id] = base
                bare_taken = True
            else:
                slugs[entity_id] = f"{base}-{entity_id}"
                fell_back.append(entity_id)

    # Belt and braces: the policy above should already guarantee this.
    seen: dict[str, int] = {}
    for entity_id in order:
        slug = slugs[entity_id]
        if slug in seen and seen[slug] != entity_id:
            raise AssertionError(
                f"slug collision survived: {slug!r} for ids {seen[slug]} and {entity_id}"
            )
        seen[slug] = entity_id

    return slugs, fell_back


# --------------------------------------------------------------------------
# The catalog's seven entity types, and how each is keyed.
#
# Institutions are keyed by NAME, not by affiliation row: 1359 affiliation rows
# collapse to 1094 institutions because one institution has many departments,
# and every chart groups on `af.name` (index.qmd projects `af.name AS
# affiliation` with no department). A page per row would leave a click on
# "Utrecht University" ambiguous across three targets.
# --------------------------------------------------------------------------

ENTITY_QUERIES: dict[str, str] = {
    "publications": """
        SELECT id, title FROM publication ORDER BY id
    """,
    "authors": """
        SELECT id,
               CASE WHEN disambiguator IS NOT NULL AND disambiguator <> ''
                    THEN name || ' (' || disambiguator || ')'
                    ELSE name END
        FROM author ORDER BY id
    """,
    "algorithms": """
        SELECT id, name FROM algorithm ORDER BY id
    """,
    # Datasets, keyed by their own id: `dataset.name` is UNIQUE, so unlike
    # institutions and venues there is nothing to collapse. The name is a
    # repository title for most of them, which makes a long slug, and that is
    # still better than an opaque accession in the URL.
    "datasets": """
        SELECT id, name FROM dataset ORDER BY id
    """,
    "institutions": """
        SELECT MIN(id), name FROM affiliation GROUP BY name ORDER BY MIN(id)
    """,
    "venues": """
        SELECT MIN(id), journal FROM publication
        WHERE journal IS NOT NULL AND journal <> ''
        GROUP BY journal ORDER BY MIN(id)
    """,
    # The slug comes from `name`, not `label`: the name is already a slug and
    # the shorter URL is the better one. /subdomains/immunopeptidomics.html
    # rather than /subdomains/immunopeptidomics-neoantigen.html.
    "subdomains": """
        SELECT id, name FROM subdomain ORDER BY id
    """,
    # Architecture families, keyed by MIN(id) over the algorithms that carry the
    # family, the same value-keyed pattern institutions and venues use. There is
    # no `family` table to key on: `algorithm.algorithm_family` is free text, and
    # a table would have to be edited by hand every time a family gained or lost
    # a method.
    #
    # HAVING COUNT(*) >= 2 is the whole page policy, in SQL, on purpose.
    # 17 of 50 families hold exactly one method, and a page for one of those
    # would carry that method's papers, that method's authors and its dates:
    # a duplicate of a page that already exists. A family earns a page when it
    # has something to aggregate. The threshold is derived rather than curated,
    # so a family reaching two methods gains a page with no extra step -- which
    # shows up here as an ADDED slug, and --check passes on additions.
    #
    # A family dropping back to one method REMOVES a published URL and so fails
    # --check, which is the intended loudness: it is a real 404 and wants a
    # deliberate --write.
    "families": """
        SELECT MIN(id), algorithm_family FROM algorithm
        WHERE COALESCE(algorithm_family, '') <> ''
        GROUP BY algorithm_family HAVING COUNT(*) >= 2
        ORDER BY MIN(id)
    """,
}


def all_slugs(conn: sqlite3.Connection) -> dict[str, dict[int, str]]:
    """type -> {entity id -> slug}, with the publication preprint policy applied."""
    out: dict[str, dict[int, str]] = {}
    fallbacks: dict[str, list[int]] = {}

    # In every colliding publication title pair, exactly one side is the version
    # of record and the other is a preprint or a postprint, so the type supplies
    # the disambiguator semantically. Without the postprint entry, publication 30
    # (an arXiv posting of a BIBE conference paper) would fall back to "-30".
    preprint_suffix = {
        pid: suffix
        for pid, suffix in conn.execute(
            "SELECT id, publication_type FROM publication "
            "WHERE publication_type IN ('preprint', 'postprint', 'abstract')"
        )
    }

    for kind, query in ENTITY_QUERIES.items():
        entries = [(int(i), t) for i, t in conn.execute(query)]
        suffixes = preprint_suffix if kind == "publications" else None
        slugs, fell_back = assign_unique(entries, suffixes)
        out[kind] = slugs
        if fell_back:
            fallbacks[kind] = fell_back

    if fallbacks:
        out["__fallbacks__"] = fallbacks  # type: ignore[assignment]
    return out


LOCK_HEADER = (
    "# slugs.lock -- the published URL of every generated entity page.\n"
    "# Written by `python3 scripts/slugs.py --write`, checked by `--check`.\n"
    "# A diff here is a URL change: every line is a live, indexed address.\n"
    "# Format: type<TAB>entity id<TAB>slug\n"
)


def lock_lines(slugs: dict[str, dict[int, str]]) -> list[str]:
    """Deterministic TSV: sorted by type then id, so a diff is readable."""
    out = []
    for kind in sorted(slugs):
        for entity_id in sorted(slugs[kind]):
            out.append(f"{kind}\t{entity_id}\t{slugs[kind][entity_id]}")
    return out


def read_lock() -> dict[tuple[str, int], str]:
    if not LOCK_PATH.exists():
        return {}
    found = {}
    for line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        kind, entity_id, slug = line.split("\t")
        found[(kind, int(entity_id))] = slug
    return found


def check_lock(slugs: dict[str, dict[int, str]], quiet: bool = False) -> int:
    """Compare current slugs against the committed baseline.

    A CHANGED or REMOVED entry breaks a URL that is already published and
    probably indexed, so either is an error. ADDED entries are new pages and are
    always fine.
    """
    if not LOCK_PATH.exists():
        print(f"{LOCK_PATH.name} does not exist. Create it with "
              f"`python3 scripts/slugs.py --write`.")
        return 1

    was = read_lock()
    now = {(k, i): s for k, ids in slugs.items() for i, s in ids.items()}

    changed = sorted((k, i, was[(k, i)], now[(k, i)])
                     for (k, i) in was.keys() & now.keys()
                     if was[(k, i)] != now[(k, i)])
    removed = sorted(k_i for k_i in was.keys() - now.keys())
    added = sorted(k_i for k_i in now.keys() - was.keys())

    for kind, entity_id, old, new in changed:
        print(f"  URL CHANGED  {kind} {entity_id}")
        print(f"                 was pages/{kind}/{old}.html")
        print(f"                 now pages/{kind}/{new}.html")
    for kind, entity_id in removed:
        print(f"  URL REMOVED  {kind} {entity_id} -> "
              f"pages/{kind}/{was[(kind, entity_id)]}.html will 404")
    if added and not (changed or removed) and not quiet:
        print(f"  {len(added)} new page(s), no existing URL touched.")

    if changed or removed:
        print(f"\n{len(changed)} changed, {len(removed)} removed, "
              f"{len(added)} added.")
        print("Each one breaks a published, probably-indexed URL.")
        print("If the rename is intended, run `python3 scripts/slugs.py --write` and "
              "commit the lock diff alongside it.")
        return 1

    if not quiet:
        print(f"{len(now)} slugs, {len(added)} new, no existing URL changed.")
    return 0


def write_lock(slugs: dict[str, dict[int, str]]) -> int:
    lines = lock_lines(slugs)
    LOCK_PATH.write_text(LOCK_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {LOCK_PATH.name}: {len(lines)} slugs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="fail if an existing page's URL changed or vanished")
    group.add_argument("--write", action="store_true",
                       help="accept the current slugs as the new baseline")
    parser.add_argument("--quiet", action="store_true",
                        help="with --check, print nothing unless a URL changed")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    result = all_slugs(conn)
    fallbacks = result.pop("__fallbacks__", {})  # type: ignore[arg-type]

    if args.check or args.write:
        # A fallback to an id suffix is a URL too, so the lock records it either
        # way; it is reported by the default audit, not here.
        rc = (write_lock(result) if args.write
              else check_lock(result, quiet=args.quiet))
        conn.close()
        return rc

    total = 0
    for kind, slugs in result.items():
        total += len(slugs)
        longest = max(slugs.values(), key=len)
        print(f"{kind:14s} {len(slugs):5d} pages   longest slug {len(longest):2d} chars")

    print(f"\n{total} pages total")

    if fallbacks:
        print("\nFELL BACK to id suffixes (extend the policy, or merge a duplicate):")
        for kind, ids in fallbacks.items():
            for entity_id in ids:
                print(f"  {kind} {entity_id}")
        conn.close()
        return 1

    print("Every slug is unique without an id suffix.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""URL slugs for the generated entity pages.

Imported by both `build_pages.py` (which writes the pages) and, eventually, the
Python chunk in `index.qmd` (which needs the same slugs to link INTO them). One
module so the two can never disagree: a mismatch would render a link to a page
that does not exist.

Run this file directly to audit the whole catalog:

    python3 slugs.py

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

    python3 slugs.py --check     # fail if any existing URL changed or vanished
    python3 slugs.py --write     # accept the current slugs as the new baseline

`slugs.lock` is a committed record of every (type, id) -> slug. `--check` is a
backstop in CI and in the pre-commit hook; `--write` is deliberate and manual.

It is deliberately NOT auto-refreshed by the hook, unlike check_counts.py.
Rewriting the baseline automatically is exactly the failure being guarded
against: it would turn a URL change from a loud error into a silent one. When a
rename is intended, run --write and let the lock diff record it.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).parent / "denovo.db"
LOCK_PATH = Path(__file__).parent / "slugs.lock"

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


def slugify(text: str, maxlen: int = MAX_SLUG_CHARS) -> str:
    """ASCII kebab-case slug. Raises on input that cannot produce one."""
    if not text or not text.strip():
        raise ValueError("cannot slugify empty text")
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
# The catalog's five entity types, and how each is keyed.
#
# Institutions are keyed by NAME, not by affiliation row: 621 affiliation rows
# collapse to 395 institutions because one institution has many departments,
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
    "institutions": """
        SELECT MIN(id), name FROM affiliation GROUP BY name ORDER BY MIN(id)
    """,
    "venues": """
        SELECT MIN(id), journal FROM publication
        WHERE journal IS NOT NULL AND journal <> ''
        GROUP BY journal ORDER BY MIN(id)
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
            "WHERE publication_type IN ('preprint', 'postprint')"
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
    "# Written by `python3 slugs.py --write`, checked by `--check`.\n"
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
              f"`python3 slugs.py --write`.")
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
        print("If the rename is intended, run `python3 slugs.py --write` and "
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

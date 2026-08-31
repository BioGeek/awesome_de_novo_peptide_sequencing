#!/usr/bin/env python3
"""URL slugs for the generated entity pages.

Imported by both `build_pages.py` (which writes the pages) and, eventually, the
Python chunk in `index.qmd` (which needs the same slugs to link INTO them). One
module so the two can never disagree: a mismatch would render a link to a page
that does not exist.

Run this file directly to audit the whole catalog:

    uv run python slugs.py

It prints one line per entity type and exits non-zero if any slug had to fall
back to an id suffix, which is the signal that either the disambiguation policy
needs extending or the DB has a duplicate that should be merged instead.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).parent / "denovo.db"

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
    "ø": "o",
    "ß": "ss",
    "ı": "i",
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
# Institutions are keyed by NAME, not by affiliation row: 512 affiliation rows
# collapse to 335 institutions because one institution has many departments,
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

    # Verified: in every colliding publication title pair, exactly one side is a
    # preprint, so "-preprint" resolves all of them semantically.
    preprint_suffix = {
        pid: "preprint"
        for (pid,) in conn.execute(
            "SELECT id FROM publication WHERE publication_type = 'preprint'"
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


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    result = all_slugs(conn)
    fallbacks = result.pop("__fallbacks__", {})  # type: ignore[arg-type]

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

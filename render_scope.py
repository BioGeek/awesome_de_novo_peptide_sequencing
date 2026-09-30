#!/usr/bin/env python3
"""Decide what the publish needs to render: everything, some pages, or the index.

A full render is 1017 seconds, 98.7% of the publish job, and almost all of it
is the ~2485 generated entity pages at roughly 0.4s each. index.qmd is 13.5s of
that. So the question worth asking on every push is which pages actually
changed.

Usually none. build_pages.py deliberately keeps volatile metrics out of the
pages ("repository stars are NOT baked into pages"), so the DAILY
refresh-repo-metrics commit -- the single most frequent trigger -- changes
exactly zero of them. Measured: bumping repository_metrics and regenerating
leaves the whole page set byte-identical.

But some commits change a HANDFUL. A new paper touches its own page, its
authors' pages and the pages of anything it links. The weekly benchmark refresh
touches the 17 method pages that carry a benchmark line and nothing else. For
those, rendering all 2485 pages to update 17 is ~16 minutes of waste, so this
emits `partial` and the workflow renders index.qmd plus the changed pages, one
`quarto render` per file.

WHY THERE IS NO "RENDER NOTHING" MODE. index.qmd's footer carries the build date
and its charts read the database at render time, so its output changes on every
run even when no page does. The cheapest possible scope is therefore
"index.qmd only", never "nothing".

WHY _site IS RESTORED FROM gh-pages FIRST. A fast-path render produces a _site
holding index.html, the libs and whatever pages it was asked for, and nothing
else. Publishing that would be a disaster if `quarto publish gh-pages` mirrors
rather than merges; restoring the previous _site first makes the directory
complete either way, so the question never arises. It is also what lets the
manifest below travel from one run to the next, and what keeps sitemap.xml
whole -- see below.

WHY PER-FILE. `quarto render a.qmd b.qmd` silently renders only the first
input, which is the kind of thing that would have shipped as "the partial
render works" while quietly publishing stale pages. One invocation per file
costs ~5s each against ~0.4s inside a full render, hence MAX_PARTIAL: past
about sixty files the full render is both faster and more predictable.

A FULL project render empties _site and repopulates it at the end -- watch it
from outside and the directory is bare for the whole 17 minutes -- while a
partial render writes into whatever is already there. So the
restore-from-gh-pages step matters only to the two fast paths, and the manifest
this script writes is recreated after every render rather than preserved.

WHAT A PARTIAL RENDER LEAVES ALONE, all of it verified rather than assumed:

  * the other pages in _site, which keep their bytes;
  * sitemap.xml, which Quarto MERGES into rather than rewrites -- it keeps all
    2449 entries and refreshes the ones it rendered. Note it only merges when
    the file is already there: delete it and an index-only render produces a
    sitemap with one URL. That is the second reason the workflow restores _site
    from gh-pages before rendering anything;
  * search.json, which never carried the generated pages anyway
    (`search: false` in their metadata).

WHAT FORCES A FULL RENDER. The Quarto version, because every page embeds
<meta name="generator"> and links a content-hashed stylesheet; _quarto.yml and
custom.scss, which set the theme, the fonts and the per-page includes for every
page at once; a missing or pre-manifest _site; more than MAX_PARTIAL changed
pages; `full_render` on the workflow; and a REMOVED page, because the sitemap
merge above cannot drop an entry and only the full render rebuilds the sitemap
from what it actually produced.

Anything not in that list cannot change a rendered page, which is the property
that makes skipping safe. If in doubt, force a full render: the fast paths are
only ever optimisations of a result the slow path would reach anyway.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PAGES = HERE / "pages"
SITE = HERE / "_site"
# Lives inside _site on purpose: gh-pages carries it to the next run, which is
# the same trick that makes the restore step a free and always-exact cache.
MANIFEST = SITE / ".render-manifest"
# The list the workflow feeds to `quarto render`, one path per line.
FILES = SITE / ".render-files"
LEGACY_KEY = SITE / ".render-key"

# Past this many changed pages, render the project instead: 60 files is about
# five minutes of per-file renders against seventeen for the whole site, and
# the ratio gets worse from there.
MAX_PARTIAL = 60


def quarto_version() -> str:
    try:
        out = subprocess.run(["quarto", "--version"], capture_output=True,
                             text=True, timeout=60)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def global_key() -> str:
    """Everything that changes EVERY page's bytes at once."""
    h = hashlib.sha256()
    h.update(b"v2\n")
    h.update(quarto_version().encode() + b"\n")
    for name in ("_quarto.yml", "custom.scss"):
        p = HERE / name
        h.update(name.encode() + b"\n")
        h.update(p.read_bytes() if p.exists() else b"<missing>")
        h.update(b"\n")
    return h.hexdigest()


def page_hashes() -> dict[str, str]:
    """{path relative to the repo root: sha256 of its bytes}.

    Keyed by path as well as hashed by content, so a slug rename registers as a
    removal plus an addition even when the two files are byte-identical.
    """
    out = {}
    for qmd in sorted(PAGES.rglob("*.qmd")):
        out[str(qmd.relative_to(HERE))] = hashlib.sha256(qmd.read_bytes()).hexdigest()
    return out


def record() -> int:
    """Write the manifest describing what _site now contains.

    Called AFTER a successful render, so it can never claim a state that a
    failed render did not produce.
    """
    SITE.mkdir(parents=True, exist_ok=True)
    pages = page_hashes()
    MANIFEST.write_text(json.dumps({"global": global_key(), "pages": pages},
                                   indent=0, sort_keys=True), encoding="utf-8")
    FILES.unlink(missing_ok=True)
    # Superseded by the manifest; drop it so a mirroring publish stops carrying
    # it and the next run cannot read a stale key.
    LEGACY_KEY.unlink(missing_ok=True)
    print(f"recorded {MANIFEST.name}: {len(pages)} pages")
    return 0


def emit(mode: str, reason: str) -> None:
    print(f"render scope: {mode} ({reason})", file=sys.stderr)
    line = f"mode={mode}\n"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        Path(out).open("a", encoding="utf-8").write(line)
    else:
        sys.stdout.write(line)


def main() -> int:
    # --write-key is the name publish.yml used before the manifest; accepted so
    # a workflow run started from an older commit does not fail.
    if "--record" in sys.argv or "--write-key" in sys.argv:
        return record()

    pages = page_hashes()

    def full(reason: str) -> int:
        emit("full", reason)
        return 0

    if os.environ.get("FULL_RENDER") == "true":
        return full("forced by workflow input")
    if not SITE.exists() or not any(SITE.rglob("*.html")):
        return full("no previous _site to build on")
    if not MANIFEST.exists():
        return full("previous _site predates the render manifest")

    try:
        have = json.loads(MANIFEST.read_text(encoding="utf-8"))
        old_pages = have["pages"]
    except (ValueError, KeyError, TypeError):
        return full("unreadable render manifest")

    if have.get("global") != global_key():
        return full(f"{len(pages)} pages, Quarto version or site config changed")

    changed = sorted(p for p, h in pages.items() if old_pages.get(p) != h)
    removed = sorted(p for p in old_pages if p not in pages)

    # A REMOVED page needs the full render, and not because of the page: it is
    # the sitemap. Quarto merges into an existing sitemap.xml rather than
    # rewriting it, so neither fast path can drop the entry, and the restore
    # brings both the entry and the orphaned .html back on every subsequent run.
    # A full render empties _site and rebuilds the sitemap from what it
    # actually produced, which is the only thing that forgets a page.
    # Removals are rare and deliberate -- slugs.py --check fails the commit on
    # one -- so paying 17 minutes for them is the right trade.
    if removed:
        return full(f"{len(removed)} page(s) removed ({removed[0]}"
                    + (f" and {len(removed) - 1} more" if len(removed) > 1 else "")
                    + "), which only a full render drops from the sitemap")

    if not changed:
        emit("index", f"{len(pages)} pages byte-identical to the published set")
        return 0

    if len(changed) > MAX_PARTIAL:
        return full(f"{len(changed)} of {len(pages)} pages changed, over the "
                    f"{MAX_PARTIAL}-file partial limit")

    FILES.write_text("\n".join(changed) + "\n", encoding="utf-8")
    emit("partial", f"{len(changed)} of {len(pages)} pages changed: "
                    + ", ".join(changed[:4])
                    + (f", and {len(changed) - 4} more" if len(changed) > 4 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

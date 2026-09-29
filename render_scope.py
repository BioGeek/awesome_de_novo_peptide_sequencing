#!/usr/bin/env python3
"""Decide whether the publish needs a full render or only index.qmd.

A full render is 1017 seconds, 98.7% of the publish job, and almost all of it
is the ~2485 generated entity pages at roughly 0.4s each. index.qmd is 13.5s of
that. So the question worth asking on every push is simply: did any entity page
actually change?

Usually not. build_pages.py deliberately keeps volatile metrics out of the
pages ("repository stars are NOT baked into pages"), so the DAILY
refresh-repo-metrics commit -- the single most frequent trigger -- changes
exactly zero of them. Measured: bumping repository_metrics and regenerating
leaves the whole page set byte-identical. Bumping one publication_impact row
changes exactly one page.

WHY index.qmd IS ALWAYS RENDERED. Its footer carries the build date and its
charts read the database at render time, so its output changes on every run
even when no page does. "Skip the publish entirely" would therefore almost
never fire, which is why the fast path is "render index.qmd only" rather than
"render nothing".

WHY _site IS RESTORED FIRST. Rendering index.qmd alone produces a _site holding
index.html and the libs, and nothing else. Publishing that would be a disaster
if `quarto publish gh-pages` mirrors rather than merges. Restoring the previous
_site from the gh-pages branch first makes the directory complete either way,
so the question never arises.

THE KEY covers everything that can change a page's bytes:

  * every generated .qmd, by path AND content, so a renamed slug counts
  * the Quarto version, because every page embeds
    <meta name="generator" content="quarto-x.y.z"> and links a content-hashed
    stylesheet, so an upgrade rewrites all of them
  * _quarto.yml and custom.scss, which set the theme, the fonts and the
    per-page includes without touching any .qmd

Anything not in that list cannot change a rendered page, which is the property
that makes skipping safe. If in doubt, force a full render: the workflow takes
a `full_render` input, and the fast path is only ever an optimisation of a
result the slow path would reach anyway.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PAGES = HERE / "pages"
SITE = HERE / "_site"
KEY_FILE = SITE / ".render-key"


def quarto_version() -> str:
    try:
        out = subprocess.run(["quarto", "--version"], capture_output=True,
                             text=True, timeout=60)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def render_key() -> str:
    h = hashlib.sha256()
    h.update(b"v1\n")
    h.update(quarto_version().encode() + b"\n")
    for name in ("_quarto.yml", "custom.scss"):
        p = HERE / name
        h.update(name.encode() + b"\n")
        h.update(p.read_bytes() if p.exists() else b"<missing>")
        h.update(b"\n")
    # Path as well as content: a slug rename moves a published URL, and the
    # content of the two files can be identical.
    for qmd in sorted(PAGES.rglob("*.qmd")):
        h.update(str(qmd.relative_to(HERE)).encode() + b"\n")
        h.update(qmd.read_bytes())
        h.update(b"\n")
    return h.hexdigest()


def emit(mode: str, reason: str) -> None:
    print(f"render scope: {mode} ({reason})", file=sys.stderr)
    out = os.environ.get("GITHUB_OUTPUT")
    line = f"mode={mode}\n"
    if out:
        Path(out).open("a", encoding="utf-8").write(line)
    else:
        sys.stdout.write(line)


def main() -> int:
    key = render_key()

    if "--write-key" in sys.argv:
        # Called AFTER a successful render, so the key describes what _site
        # actually contains. Writing it earlier would record a state that a
        # failed render never produced.
        SITE.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key + "\n", encoding="utf-8")
        print(f"wrote {KEY_FILE.name}")
        return 0

    n_pages = sum(1 for _ in PAGES.rglob("*.qmd")) if PAGES.exists() else 0

    if os.environ.get("FULL_RENDER") == "true":
        emit("full", "forced by workflow input")
    elif not SITE.exists() or not any(SITE.rglob("*.html")):
        emit("full", "no previous _site to build on")
    elif not KEY_FILE.exists():
        emit("full", "previous _site predates the render key")
    elif KEY_FILE.read_text(encoding="utf-8").strip() != key:
        emit("full", f"{n_pages} pages, key changed")
    else:
        emit("index", f"{n_pages} pages byte-identical to the published set")
    return 0


if __name__ == "__main__":
    sys.exit(main())

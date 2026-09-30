#!/usr/bin/env python3
"""Post-render fixes that Quarto cannot express itself.

Quarto writes the home page into sitemap.xml as `<loc>.../index.html</loc>`,
but everything that links here -- `site-url`, the README, the GitHub repo
homepage field -- uses the directory form `.../`. Both URLs serve
byte-identical content, and with no canonical to break the tie Google reported
the page as "Duplicate without user-selected canonical".

index.qmd now declares the directory form as canonical. This makes the sitemap
agree with that instead of contradicting it, which is the state that caused the
report in the first place.

Stdlib only, no network. publish.yml runs it as its own step, after whichever
render scope render_scope.py chose, NOT as a Quarto post-render hook -- that
hook is handed the full output file list in an environment variable and dies on
Linux's 128 KiB MAX_ARG_STRLEN at this page count.

It runs on every scope, including the fast paths, because Quarto merges into an
existing sitemap.xml on a single-file render rather than leaving it alone: the
home-page entry comes back as `index.html` and needs rewriting again.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SITEMAP = ROOT / "_site" / "sitemap.xml"


def site_url() -> str:
    """Read site-url from _quarto.yml so the two cannot drift apart."""
    text = (ROOT / "_quarto.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*site-url:\s*(\S+)\s*$", text, re.M)
    if not m:
        raise SystemExit("postrender: no site-url in _quarto.yml")
    return m.group(1).rstrip("/") + "/"


def main() -> int:
    # A sitemap is normally always there, since even a single-file render
    # merges into it. The one way to be here without one is a first build with
    # no gh-pages branch to restore from, which is not an error.
    if not SITEMAP.exists():
        return 0

    base = site_url()
    text = SITEMAP.read_text(encoding="utf-8")
    before = text.count("<loc>")
    target = f"<loc>{base}index.html</loc>"
    if target not in text:
        return 0

    text = text.replace(target, f"<loc>{base}</loc>")
    after = text.count("<loc>")
    # Rewriting must never drop an entry.
    if after != before:
        raise SystemExit(f"postrender: sitemap lost entries, {before} -> {after}")

    SITEMAP.write_text(text, encoding="utf-8")
    print(f"postrender: sitemap home page -> {base} ({after} urls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

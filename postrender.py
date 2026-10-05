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

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SITEMAP = ROOT / "_site" / "sitemap.xml"
SEARCH = ROOT / "_site" / "search.json"
PAGES = ROOT / "pages"

# The generated pages are kept OUT of Quarto's own index (`search: false` in
# their metadata), because Quarto indexes a page's full text and every visitor
# downloads search.json before their first keystroke. But a reader typing an
# author's or a method's name into the search box should find its page. So this
# adds ONE compact record per page: its title, its type and the one-line
# description already written for its meta tag, nothing more.
ACCESSION = re.compile(r"\b(?:PXD\d{6}|MSV\d{9}|IPX\d{7}|PRD\d{6}|JPST\d{6})\b")
KIND_LABEL = {"publications": "Paper", "authors": "Author", "algorithms": "Method",
              "datasets": "Dataset", "institutions": "Institution", "venues": "Venue",
              "families": "Architecture family", "subdomains": "Application area"}
# Quarto never indexes anything under pages/ itself (search: false), so a
# record whose href starts there is one of ours, and a rerun replaces them.


def site_url() -> str:
    """Read site-url from _quarto.yml so the two cannot drift apart."""
    text = (ROOT / "_quarto.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*site-url:\s*(\S+)\s*$", text, re.M)
    if not m:
        raise SystemExit("postrender: no site-url in _quarto.yml")
    return m.group(1).rstrip("/") + "/"


def front_matter(path: Path) -> dict[str, str]:
    """title / subtitle / description from a generated page's YAML header."""
    head = path.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    out = {}
    for key in ("title", "subtitle", "description"):
        m = re.search(rf'^{key}: "((?:[^"\\]|\\.)*)"', head, re.M)
        if m:
            out[key] = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return out


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def add_entity_search() -> None:
    """Append one compact search record per generated page to search.json."""
    if not SEARCH.exists() or not PAGES.exists():
        return
    records = [r for r in json.loads(SEARCH.read_text(encoding="utf-8"))
               if not str(r.get("href", "")).startswith("pages/")]
    n = 0
    for kind, label in KIND_LABEL.items():
        for qmd in sorted((PAGES / kind).glob("*.qmd")):
            fm = front_matter(qmd)
            if not fm.get("title"):
                continue
            href = f"pages/{kind}/{qmd.stem}.html"
            # The description repeats the title first ("Casanovo: algorithm ...");
            # drop that so the result reads as a subtitle, not an echo.
            desc = plain(fm.get("description") or fm.get("subtitle") or "")
            title = plain(fm["title"])
            if desc.startswith(title + ":"):
                desc = desc[len(title) + 1:].strip()
            text = desc[:200]
            if kind == "datasets":
                # A reader searching for "PXD003868" wants the dataset that lives
                # there. The accessions are on the page, not in its description.
                accs = sorted(set(ACCESSION.findall(qmd.read_text(encoding="utf-8"))))
                if accs:
                    text += " · " + " ".join(accs[:12])
            records.append({"objectID": href, "href": href, "title": title,
                            "section": label, "text": text})
            n += 1
    SEARCH.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")
    print(f"postrender: search.json += {n} catalog pages ({SEARCH.stat().st_size // 1024} KB)")


def main() -> int:
    add_entity_search()
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

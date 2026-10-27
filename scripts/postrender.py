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

import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
              "families": "Architecture family", "subdomains": "Application area",
              "analyses": "Analysis", "patents": "Patent"}
# Quarto never indexes anything under pages/ itself (search: false), so a
# record whose href starts there is one of ours, and a rerun replaces them.


def site_url() -> str:
    """Read site-url from _quarto.yml so the two cannot drift apart."""
    text = (ROOT / "_quarto.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*site-url:\s*(\S+)\s*$", text, re.M)
    if not m:
        raise SystemExit("postrender: no site-url in _quarto.yml")
    return m.group(1).rstrip("/") + "/"

AKA = re.compile(r"^\| Also known as \| (.+?) \|$", re.M)

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
            if kind == "algorithms":
                # So "PandaNovo" or "CasanovoTL" finds the method filed under
                # its other name. The page's metadata table carries the aliases.
                aka = AKA.search(qmd.read_text(encoding="utf-8"))
                if aka:
                    text += " · also known as " + plain(aka.group(1))
            records.append({"objectID": href, "href": href, "title": title,
                            "section": label, "text": text})
            n += 1
    SEARCH.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")
    print(f"postrender: search.json += {n} catalog pages ({SEARCH.stat().st_size // 1024} KB)")


# Quarto writes every OJS cell's source into the page a second time, as a
# highlighted <pre> inside `.sourceCode.cell-code.hidden`, even under
# `echo: false`: it is hidden by CSS, not left out. The runtime never reads
# it (it evaluates the `ojs-module-contents` script), so it was 1.6 MB of the
# 10 MB home page doing nothing except reaching search engines, which indexed
# `${first_year}` and `viewof` definitions as the page's text. A cell's
# source is escaped HTML, so `</pre>` cannot occur inside it and the first
# one closes the block.
HIDDEN_SOURCE = re.compile(
    r'<div class="code-copy-outer-scaffold"><div class="sourceCode cell-code hidden"'
    r".*?</pre></div>(?:<button[^>]*class=\"code-copy-button\"[^>]*>.*?</button>)?</div>",
    re.S)
HOME = ROOT / "_site" / "index.html"


def section_text(page: str, anchor: str) -> str | None:
    """The plain text of `<section id=anchor>`, nested sections included."""
    m = re.search(rf'<section id="{re.escape(anchor)}"[^>]*>', page)
    if not m:
        return None
    depth, pos = 1, m.end()
    for tag in re.finditer(r"<(/?)section\b[^>]*>", page[pos:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            body = page[pos:pos + tag.start()]
            break
    else:
        return None
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S)
    return plain(body)


def strip_hidden_source() -> None:
    """Drop the hidden cell source from the home page and from its search records.

    Quarto built those records from the page BEFORE this step, so each still
    carries the source; it is re-read from the stripped page, section by
    section, which leaves only what a reader sees.
    """
    if not HOME.exists():
        return
    page = HOME.read_text(encoding="utf-8")
    page, n = HIDDEN_SOURCE.subn("", page)
    if n:
        HOME.write_text(page, encoding="utf-8")
    if "cell-code hidden" in page:
        raise SystemExit("postrender: a hidden cell-source block did not match")
    if SEARCH.exists():
        records = json.loads(SEARCH.read_text(encoding="utf-8"))
        kept = []
        for r in records:
            href = str(r.get("href", ""))
            if href.startswith("index.html#"):
                text = section_text(page, href.split("#", 1)[1])
                # Quarto merges search.json on a single-file render, so a
                # section that was removed keeps its record, pointing nowhere.
                if text is None:
                    continue
                r["text"] = text
            elif href == "index.html":
                # The record for the text above the first section.
                first = page.find("<section id=")
                start = page.find('<main class="content"')
                if 0 <= start < first:
                    r["text"] = plain(re.sub(r"<(script|style)\b.*?</\1>", " ",
                                             page[start:first], flags=re.S))
            kept.append(r)
        SEARCH.write_text(json.dumps(kept, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")
    print(f"postrender: index.html -{n} hidden cell-source blocks "
          f"({HOME.stat().st_size // 1024} KB)")


# Quarto puts ten classic <script src> tags in the <head> (navbar, search,
# clipboard, tooltips, anchors, Bootstrap), and each one blocks the first
# paint: mobile Lighthouse estimated 1.1 s of render-blocking time on the home
# page, on a simulated slow 4G link. `defer` loads them in parallel with the
# HTML and runs them in document order once it is parsed, still before
# DOMContentLoaded, which is where Quarto does all its own work: every inline
# use of them, quarto-html-after-body included, sits in a DOMContentLoaded
# handler, and quarto.js and tabsets.js are modules, deferred already. The one
# order that changes is theirs relative to these, and neither touches them at
# load. Only the home page: it is the one that is measured, and the generated
# pages are lighter.
HEAD_SCRIPT = re.compile(r'<script src="(site_libs/[^"]+\.js)"(?=[^>]*>)(?![^>]*\b(?:defer|async|type)\b)')


def defer_head_scripts() -> None:
    if not HOME.exists():
        return
    page = HOME.read_text(encoding="utf-8")
    head_end = page.find("</head>")
    head, n = HEAD_SCRIPT.subn(r'<script defer src="\1"', page[:head_end])
    if n:
        HOME.write_text(head + page[head_end:], encoding="utf-8")
    print(f"postrender: index.html deferred {n} head scripts")


# The OJS source (base64, about 430 KB) and the inline OJS values (about 130
# KB of JSON) were 85% of the home page's HTML, 195 KB gzipped, and Lighthouse
# paints nothing until the document has arrived: on its slow 4G link that was
# about a second of first paint and LCP spent on bytes the first screen never
# reads. The runtime only reads them once it starts, from
# script[type='ojs-module-contents'] and script[type='ojs-define'], so they
# move to files and the bootstrap Quarto writes (an inline module script ending
# in interpretFromScriptTags) fetches them and puts the same elements back
# just before that call.
#
# Each file is named by its content hash. GitHub Pages caches a page for ten
# minutes, and the inline values change with every daily metrics refresh, so
# fixed names would let a cached page meet newer data or the reverse. A hashed
# name ties each page to its own files, and OJS_KEEP earlier generations stay
# so that a page still cached from the last publish or two finds its own.
OJS_DIR = ROOT / "_site" / "ojs"
OJS_KEEP = 4
OJS_BOOT = "window._ojs.runtime.interpretFromScriptTags();"


def externalize_ojs() -> None:
    if not HOME.exists():
        return
    page = HOME.read_text(encoding="utf-8")
    if OJS_BOOT not in page:
        raise SystemExit("postrender: the OJS bootstrap call was not found")
    if 'fetch("ojs/' in page:
        return  # already done on this file
    OJS_DIR.mkdir(exist_ok=True)
    fetched = []
    # Quarto writes more than one element of a kind (two module-contents, one
    # of them nearly empty); the runtime reads them in document order, so each
    # kind is stored as the list of its bodies and put back in that order.
    for kind in ("ojs-module-contents", "ojs-define"):
        found = list(re.finditer(rf'<script type="{kind}">(.*?)</script>\n?', page, re.S))
        if not found:
            raise SystemExit(f"postrender: no {kind} element in index.html")
        body = json.dumps([m.group(1) for m in found])
        name = f"{kind}.{hashlib.sha256(body.encode()).hexdigest()[:12]}.json"
        (OJS_DIR / name).write_text(body, encoding="utf-8")
        for m in reversed(found):
            page = page[:m.start()] + page[m.end():]
        fetched.append((kind, name))
    boot = ("await Promise.all(" + json.dumps(fetched) + ".map(async ([type, file]) => {\n"
            "  for (const text of await (await fetch(\"ojs/\" + file)).json()) {\n"
            "    const el = document.createElement(\"script\"); el.type = type; el.text = text;\n"
            "    document.body.append(el);\n"
            "  }\n"
            "}));\n" + OJS_BOOT)
    page = page.replace(OJS_BOOT, boot, 1)
    HOME.write_text(page, encoding="utf-8")
    # Keep this generation and the OJS_KEEP before it, per kind, by the order
    # they were first written; the manifest survives publishes in gh-pages.
    manifest_path = OJS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest = [n for n in manifest if n not in {f for _, f in fetched}] + [f for _, f in fetched]
    keep = set()
    for kind, _ in fetched:
        keep.update([n for n in manifest if n.startswith(kind + ".")][-(OJS_KEEP + 1):])
    for f in OJS_DIR.iterdir():
        if f.name != "manifest.json" and f.name not in keep:
            f.unlink()
    manifest_path.write_text(json.dumps([n for n in manifest if n in keep], indent=0))
    sizes = ", ".join(f"{k} {(OJS_DIR / f).stat().st_size // 1024} KB" for k, f in fetched)
    print(f"postrender: index.html {HOME.stat().st_size // 1024} KB, OJS moved out ({sizes})")


def main() -> int:
    strip_hidden_source()
    defer_head_scripts()
    externalize_ojs()
    add_entity_search()
    # IndexNow's ownership proof, a file named after the key. Written on every
    # run because a full render empties _site, and only gh-pages would
    # otherwise carry it from one publish to the next.
    import indexnow
    indexnow.write_key()
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

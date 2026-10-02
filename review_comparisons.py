#!/usr/bin/env python3
"""Build a LOCAL review page: every mined table beside a picture of the printed one.

Spot-checking an extraction by reading a CSV is hopeless, because the thing you
need to check is whether the grid matches the page. This renders each table's
region out of the PDF and puts the crop next to the parse, side by side, so a
wrong column or a mis-assigned metric is visible at a glance.

It also renders the tables the miner REFUSED, with the reason, which is the
half worth looking at: a rejection is either a correct refusal or a gap, and
only a person reading the printed table can say which.

    uv run --with pdfplumber python3 review_comparisons.py              # all
    uv run --with pdfplumber python3 review_comparisons.py --ids 49,58   # some
    uv run --with pdfplumber python3 review_comparisons.py --rejected-only

**IT WRITES OUTSIDE THE REPOSITORY AND IS NEVER PUBLISHED.** The output is a
folder of PNGs and one HTML file under the PDF library, the same place
`build_pdf_library.py` keeps `pdf_status.csv`: it quotes paper figures, so it
is a private reading aid and not something to put on a public site. Nothing it
produces is committed, and it must never run in CI, which has neither a PDF
library nor poppler.

Rendering is `pdftoppm` plus a PIL crop rather than pdfplumber's `to_image()`,
because poppler is already a dependency of this project's PDF handling and
pdfplumber's image path pulls in a separate toolchain.
"""
from __future__ import annotations

import argparse
import collections
import html
import pathlib
import subprocess
import sys
import json
import re
import urllib.parse

import build_pdf_library as bpl
import build_paper_comparisons as B

OUT = bpl.DEFAULT_DIR / "comparison-review"
CACHE = OUT / ".pages"
# Which tables a person has read and signed off. Lives beside the crops,
# outside the repository, because it is this reader's review state and not
# catalog data. Each entry keeps a FINGERPRINT as well as the id, because an id
# encodes the block's order on its page and that order can shift when the
# miner improves; a stale approval is then reported rather than silently
# carried over.
# Two states, both meaning "a person has read this and is done with it":
# APPROVED for a parse confirmed correct, DISMISSED for a refusal confirmed to
# be the right refusal. Both collapse, because the page is a worklist and the
# point is to shrink it.
APPROVED = OUT / "approved.json"
# What each crop actually shows, written for the cross-checker. Without it
# build_table_vlm.py had to guess from the audit CSV and compared a crop
# against whichever table came first for that publication, which made every
# verdict on a multi-table paper meaningless.
MANIFEST = OUT / "crops.json"
DPI = 150


# One page often carries several tables, so the rendered page is cached: the
# first version re-ran pdftoppm per table and rendered DiffuNovo's page seven
# times.
_PAGE_CACHE: dict[tuple[str, int], pathlib.Path | None] = {}


def page_png(pdf_path: pathlib.Path, page_no: int) -> pathlib.Path | None:
    key = (str(pdf_path), page_no)
    if key in _PAGE_CACHE:
        return _PAGE_CACHE[key]
    tmp = CACHE / f"pg-{abs(hash(key)) % (10 ** 12)}"
    try:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-f", str(page_no),
                        "-l", str(page_no), "-png", str(pdf_path), str(tmp)],
                       check=True, capture_output=True, timeout=180)
        made = sorted(tmp.parent.glob(tmp.name + "-*.png"))
        _PAGE_CACHE[key] = made[0] if made else None
    except Exception:
        _PAGE_CACHE[key] = None
    return _PAGE_CACHE[key]


def render(pdf_path: pathlib.Path, page_no: int, bbox, dest: pathlib.Path) -> bool:
    """Crop one table out of one page. Returns False if nothing could be made."""
    if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return False
    from PIL import Image
    src = page_png(pdf_path, page_no)
    if not src:
        return False
    with Image.open(src) as im:
        k = DPI / 72.0
        box = (max(0, int(bbox[0] * k) - 4), max(0, int(bbox[1] * k) - 4),
               min(im.width, int(bbox[2] * k) + 4),
               min(im.height, int(bbox[3] * k) + 4))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return False
        im.crop(box).save(dest)
    return True


def unsquash_name(text: str) -> str:
    """A printed method label, with the spaces a PDF dropped put back.

    'PeaksNovo(Maetal.,2003)' -> 'Peaks Novo (Ma et al., 2003)'. Display only:
    what the miner RESOLVES is the catalog name, and what a row would store is
    the label exactly as printed. This is so a reviewer can read it.
    """
    t = (text or "").strip()
    # Only the CITATION is re-spaced. The method name's own camelCase is left
    # alone, because splitting it gives 'Ada Novo' and 'Diffu Novo', which is
    # worse than leaving it glued; the resolved catalog name is shown beneath
    # it anyway.
    t = re.sub(r"(?<=[^\s(])(?=\()", " ", t)                 # space before '('
    t = re.sub(r"(?i)(?<=[a-z])et\s*al\s*\.?", " et al.", t)  # 'Maetal.' -> 'Ma et al.'
    t = re.sub(r",(?=\S)", ", ", t)                           # space after a comma
    t = re.sub(r";(?=\S)", "; ", t)
    return re.sub(r"\s+", " ", t).strip()


def unsquash(text: str) -> str:
    """'ApisMellifera' -> 'Apis Mellifera', for display.

    The same camelCase split the miner applies to a subset before storing it.
    A label squashed with no case change, such as 'Clambacteria' for 'Clam
    bacteria', cannot be recovered and is shown as the page has it.
    """
    text = text or ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)).strip()


def grid_html(rec: dict) -> str:
    """The parse, as a table laid out the way the paper lays it out."""
    ncol = len(rec["col_head"])
    axis, maxis = rec["axis"], rec["metric_axis"]
    out = ["<table class='grid'>"]

    def mcell(j):
        return f"{html.escape(rec['metrics'][j])}<br><small>{html.escape(rec['levels'][j])}</small>"

    def mname(j):
        """The label AS PRINTED, with the resolved catalog name beneath it.

        Reconstructing it from name plus version produced 'DiffuNovo vLogits'
        where the paper writes 'DiffuNovo (Logits)'. The printed form is what a
        reviewer compares against the picture, so it leads; the resolved name
        is what the row would be filed under, so it is shown too when the two
        differ.
        """
        m = rec["methods"].get(j)
        if not m:
            return "&mdash;"
        name, ver, printed = (m + ("",))[:3] if len(m) < 3 else m
        shown = unsquash_name(printed) or (f"{name} {ver}" if ver else name)
        out = html.escape(shown)
        if html.escape(name) != out:
            out += f"<br><small class='dim'>{html.escape(name)}"
            out += f" &middot; {html.escape(ver)}</small>" if ver else "</small>"
        return out

    if axis == "columns":
        out.append("<tr><th></th>" + "".join(
            f"<th>{mname(k)}</th>" for k in range(ncol)) + "</tr>")
        if maxis == "columns":
            # The metric belongs to the column, so it gets a row of its own.
            out.append("<tr><th class='dim'>metric</th>" + "".join(
                f"<td class='dim'>{mcell(k)}</td>" for k in range(ncol)) + "</tr>")
        for i, r in enumerate(rec["body"]):
            # WHEN THE METRIC RUNS DOWN THE ROWS, a single metric row printed
            # row 0's metric under every column, which reads as though the
            # whole table were peptide accuracy. RT-GCTnovo's TABLE I is
            # peptide accuracy on one row and amino-acid accuracy on the next,
            # so the metric belongs beside the ROW.
            label = html.escape(unsquash(r["label"]) or "-")
            if maxis == "rows":
                label += f"<br><small class='dim'>{mcell(i)}</small>"
            out.append(f"<tr><th>{label}</th>" + "".join(
                f"<td>{html.escape(r['cells'].get(k, ''))}</td>" for k in range(ncol))
                + "</tr>")
    else:
        out.append("<tr><th>method</th>"
                   + ("<th class='dim'>metric</th>" if maxis == "rows" else "")
                   + "".join(f"<th>{html.escape(rec['subsets'].get(k) or rec['col_head'][k])}"
                             + (f"<br><small class='dim'>{mcell(k)}</small>"
                                if maxis == "columns" else "")
                             + "</th>" for k in range(ncol)) + "</tr>")
        for i, r in enumerate(rec["body"]):
            label = (mname(i) if rec["methods"].get(i)
                     else html.escape(unsquash(r["label"]) or "-"))
            out.append(f"<tr><th>{label}</th>"
                       + (f"<td class='dim'>{mcell(i)}</td>" if maxis == "rows" else "")
                       + "".join(f"<td>{html.escape(r['cells'].get(k, ''))}</td>"
                                 for k in range(ncol)) + "</tr>")
    out.append("</table>")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated publication ids")
    ap.add_argument("--rejected-only", action="store_true")
    ap.add_argument("--accepted-only", action="store_true")
    ap.add_argument("--approve", help="comma-separated ids: the parse is correct")
    ap.add_argument("--dismiss", help="comma-separated ids: the REFUSAL is correct")
    ap.add_argument("--unapprove", help="comma-separated ids to un-mark either way")
    ap.add_argument("--keep-crops", action="store_true",
                    help="reuse the PNGs already rendered, for an HTML-only change")
    args = ap.parse_args()

    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        import pdfplumber
    except ModuleNotFoundError:
        print("needs pdfplumber: uv run --with pdfplumber python3 "
              "review_comparisons.py", file=sys.stderr)
        return 2

    import sqlite3
    con = sqlite3.connect(B.DB)
    con.row_factory = sqlite3.Row
    pubs = bpl.load_publications(con)
    cov = bpl.coverage(pubs, B.LIBRARY)
    rx = B.locator(con)
    index = B.algorithm_index(con)
    want = {int(i) for i in args.ids.split(",")} if args.ids else None

    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    approved: dict = {}
    if APPROVED.exists():
        try:
            approved = json.loads(APPROVED.read_text())
        except Exception:
            print(f"  could not read {APPROVED.name}; starting empty")
    today = __import__("datetime").date.today().isoformat()
    for tid in (args.approve or "").replace(" ", "").split(","):
        if tid:
            approved.setdefault(tid, {}).update({"state": "approved", "on": today})
    for tid in (args.dismiss or "").replace(" ", "").split(","):
        if tid:
            approved.setdefault(tid, {}).update({"state": "dismissed", "on": today})
    for tid in (args.unapprove or "").replace(" ", "").split(","):
        approved.pop(tid, None)
    # CROPS ARE REPLACED IN PLACE, NOT DELETED UP FRONT. Deleting them first
    # left the EXISTING page pointing at files that no longer existed for the
    # whole of a re-render, which takes minutes: open it in that window and
    # every screenshot is a broken image. Each crop is overwritten as it is
    # made, and anything no longer referenced is removed at the end.

    rows = con.execute("""
        SELECT DISTINCT p.id, p.title, p.publication_date FROM publication p
          JOIN publication_algorithm pa ON pa.publication_id = p.id AND pa.role='describes'
          JOIN algorithm a ON a.id = pa.algorithm_id
         WHERE a.kind = 'algorithm' ORDER BY p.id""").fetchall()

    items: list[dict] = []
    tally: collections.Counter = collections.Counter()
    for pub in rows:
        if want and pub["id"] not in want:
            continue
        paths = cov.get(pub["id"]) or []
        if not paths:
            continue
        path = paths[0]
        try:
            pdf = pdfplumber.open(path)
        except Exception:
            continue
        with pdf:
            pages_text = [(pg.extract_text() or "") for pg in pdf.pages]
            cands = B.candidate_pages(pages_text, rx)
            if not cands:
                continue
            whole = "\n".join(pages_text)
            vocab = B.paper_vocabulary(whole, con)
            subject = con.execute("""
                SELECT a.id, a.name FROM algorithm a
                  JOIN publication_algorithm pa ON pa.algorithm_id = a.id
                 WHERE pa.publication_id = ? AND pa.role='describes'
                 ORDER BY a.id LIMIT 1""", (pub["id"],)).fetchone()
            for pno in cands:
                page = pdf.pages[pno]
                near = "\n".join(pages_text[max(0, pno - 1):pno + 2])
                tables, vetoed, _ = B.extract(page)
                found: list[dict] = []
                for v in vetoed:
                    found.append({**v, "verdict": "rejected"})
                for tb in tables:
                    collected: list[dict] = []
                    base = {c: "" for c in B.AUDIT_COLUMNS}
                    base.update({"publication_id": pub["id"], "pdf_page": pno + 1})
                    try:
                        B.emit(con, base, tb, vocab, index, subject, near, whole,
                               [], collections.Counter(), False, collect=collected)
                    except B.Reject as exc:
                        collected.append({"verdict": "rejected", "reason": str(exc),
                                          "table_label": tb["table_label"],
                                          "caption": tb["caption"],
                                          "bbox": tb.get("bbox"),
                                          "page": tb.get("page")})
                    found.extend(collected)
                for n, rec in enumerate(found):
                    if args.rejected_only and rec["verdict"] != "rejected":
                        continue
                    if args.accepted_only and rec["verdict"] != "accepted":
                        continue
                    # A STABLE IDENTIFIER PER TABLE, so a person can say which
                    # one they mean. It is the crop's own stem, which already
                    # encodes publication, page and the block's order on that
                    # page, and it is reproduced as the HTML anchor so the chip
                    # links to itself and the URL can be copied.
                    tid = f"p{pub['id']}-pg{pno + 1}-{n}"
                    name = f"{tid}.png"
                    dest = OUT / name
                    ok = (True if args.keep_crops and dest.exists()
                          else render(path, rec.get("page") or (pno + 1),
                                      rec.get("bbox"), dest) or dest.exists())
                    # A link straight to the PDF this crop came from, at the
                    # page it came from. Browsers' built-in viewers honour
                    # '#page=N', and the path needs quoting because the library
                    # folder has spaces in its name.
                    rec.update({"pdf_url": "file://" + urllib.parse.quote(str(path)),
                                "pdf_name": path.name,
                                "tid": tid, "pub": pub["id"], "title": pub["title"],
                                "year": str(pub["publication_date"] or "")[:4],
                                "img": name if ok else None,
                                "pdf_page": rec.get("page") or (pno + 1)})
                    seen_before = approved.get(tid)
                    if seen_before:
                        # An entry with no state predates the dismissed state
                        # and meant approved.
                        # NOT named `want`: that is the publication-id filter
                        # in this same function, and shadowing it made every
                        # paper fail an `int in str` test.
                        state = seen_before.get("state", "approved")
                        if state == "approved" and rec["verdict"] == "accepted":
                            rec["verdict"] = "approved"
                        elif state == "dismissed" and rec["verdict"] == "rejected":
                            rec["verdict"] = "dismissed"
                    items.append(rec)
                    tally[rec["verdict"]] += 1
        print(f"  p{pub['id']:<4} {len([i for i in items if i['pub']==pub['id']]):>3} "
              f"table(s)  {pub['title'][:52]}", flush=True)

    for f in CACHE.glob("*.png"):
        f.unlink()
    if not args.keep_crops:
        keep = {it["img"] for it in items if it.get("img")}
        for old in OUT.glob("*.png"):
            if old.name not in keep:
                old.unlink()
    # Record the fingerprint of everything approved, so a later run can tell
    # whether the id still points at the same table.
    for it in items:
        if it["tid"] in approved:
            approved[it["tid"]].update(
                {"label": it.get("table_label") or "", "pub": it["pub"],
                 "cells": it.get("n_cells") or len(it.get("body") or [])})
    APPROVED.write_text(json.dumps(approved, indent=1, sort_keys=True))
    MANIFEST.write_text(json.dumps(
        {it["tid"]: {"publication_id": it["pub"], "pdf_page": it["pdf_page"],
                     "table_label": it.get("table_label") or "",
                     "verdict": it["verdict"],
                     "values": [c for r in (it.get("body") or [])
                                for c in r["cells"].values()]}
         for it in items}, indent=1, sort_keys=True))
    write_html(items, tally, approved)
    print(f"\n  {tally['accepted']} accepted, {tally['approved']} approved, "
          f"{tally['rejected']} rejected, {tally['dismissed']} dismissed")
    stale = [t for t in approved if t not in {i['tid'] for i in items}]
    if stale:
        print(f"  {len(stale)} approved id(s) no longer present: {', '.join(sorted(stale))}")
        print("  the block order on a page can shift as the miner changes; "
              "re-approve under the new id")
    print(f"  {OUT / 'index.html'}")
    return 0


def write_html(items: list[dict], tally, approved: dict | None = None) -> None:
    by_pub: dict = {}
    for it in items:
        by_pub.setdefault((it["pub"], it["title"], it["year"]), []).append(it)
    H = ["""<!doctype html><html><head><meta charset="utf-8">
<title>Comparison-table review</title><style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--line:#d8d8d2;--ok:#1a7f4b;--no:#a8321e;--dim:#6b6b63}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
 padding:14px 24px;z-index:5}
h1{margin:0;font-size:17px}
.sub{color:var(--dim);font-size:13px;margin-top:3px}
main{padding:8px 24px 60px}
h2{font-size:15px;margin:34px 0 4px;padding-top:10px;border-top:1px solid var(--line)}
h2 small{color:var(--dim);font-weight:400}
.item{margin:18px 0 26px}
.hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}
.tag{font:600 11px/1.6 ui-monospace,monospace;padding:1px 7px;border-radius:9px;color:#fff}
.tag.accepted{background:var(--ok)} .tag.rejected{background:var(--no)}
.tag.approved{background:#1d4ed8} .tag.dismissed{background:#6b6b63}
details.appr.dismissed{border-color:#cfcfc6;background:#f6f6f3}
details.appr.dismissed>summary{color:#5d5d55}
details.appr{border:1px dashed #bcd0a8;background:#f4f9f0;border-radius:6px;
 padding:8px 12px;margin:14px 0}
details.appr>summary{cursor:pointer;font-size:13px;color:#3f6b2c}
.tid{font:600 11.5px/1.6 ui-monospace,monospace;color:#2a4d8f;background:#eaf0fb;
 border:1px solid #cfdcf2;padding:1px 7px;border-radius:9px;cursor:copy;
 -webkit-user-select:all;user-select:all}
.tid:hover{background:#dde8fa}
.tid.copied{background:#d8f0dd;border-color:#9fd3ad;color:#14612f}
.anch{color:#9db4da;text-decoration:none;font:600 12px/1 ui-monospace,monospace}
.anch:hover{color:#2a4d8f}
.pdf{font-size:12px;color:#2a4d8f;text-decoration:none;border-bottom:1px dotted #9db4da}
.pdf:hover{border-bottom-style:solid}
.item:target{outline:3px solid #f0c000;outline-offset:6px;border-radius:4px}
.toc{columns:3;font:12.5px/1.8 ui-monospace,monospace;margin:10px 0 0}
.toc a{color:#2a4d8f;text-decoration:none} .toc a:hover{text-decoration:underline}
.toc .r{color:var(--no)} .toc .a{color:var(--ok)} .toc .ok{color:#1d4ed8;font-weight:700} .toc .dm{color:#8a8a80}
.lbl{font-weight:600}
.meta{color:var(--dim);font-size:12.5px}
.cap{color:var(--dim);font-size:12.5px;margin:2px 0 8px;max-width:150ch}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
@media(max-width:1100px){.pair{grid-template-columns:1fr}}
.panel{border:1px solid var(--line);background:#fff;border-radius:6px;padding:10px;overflow:auto}
.panel h3{margin:0 0 8px;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--dim)}
.panel img{max-width:100%;display:block}
table.grid{border-collapse:collapse;font:12px/1.4 ui-monospace,monospace}
table.grid th,table.grid td{border:1px solid var(--line);padding:3px 6px;text-align:right;white-space:nowrap}
table.grid th{background:#f4f4f0;text-align:left;font-weight:600}
table.grid .dim{color:var(--dim);font-weight:400;background:#fafaf7}
.why{color:var(--no);font:12.5px/1.5 ui-monospace,monospace;white-space:pre-wrap}
.nope{color:var(--dim);font-style:italic}
</style></head><body>
<script>
// Copy on click where the browser allows it. A file:// page is not always a
// secure context, so navigator.clipboard can be absent or refuse; the
// execCommand path and, failing both, the CSS selection, cover that.
document.addEventListener('click', function (e) {
  var el = e.target.closest ? e.target.closest('.tid') : null;
  if (!el) return;
  var id = el.dataset.id || el.textContent;
  var done = function () {
    el.classList.add('copied');
    setTimeout(function () { el.classList.remove('copied'); }, 900);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(id).then(done, function () { legacy(); });
  } else { legacy(); }
  function legacy() {
    try {
      var t = document.createElement('textarea');
      t.value = id; t.setAttribute('readonly', '');
      t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.appendChild(t); t.select();
      document.execCommand('copy'); document.body.removeChild(t); done();
    } catch (err) { /* the CSS selection is the fallback */ }
  }
});
</script>"""]
    H.append(f"<header><h1>Comparison-table review</h1><div class='sub'>"
             f"{tally['accepted']} accepted and {tally['rejected']} refused, each beside the "
             f"printed table it came from. Local file, not published.</div></header><main>")
    H.append("<details class='panel' style='margin-bottom:18px'>"
             "<summary><strong>Index of every table id</strong> "
             "&mdash; green accepted, red refused</summary><div class='toc'>")
    for it in sorted(items, key=lambda i: (i["pub"], i["pdf_page"], i["tid"])):
        cls = {"accepted": "a", "approved": "ok", "dismissed": "dm"}.get(
            it["verdict"], "r")
        H.append(f"<a class='{cls}' href='#{it['tid']}'>{it['tid']}</a> "
                 f"<span class='meta'>{html.escape((it.get('table_label') or '?')[:12])}</span><br>")
    H.append("</div></details>")
    for (pid, title, year), group in sorted(by_pub.items()):
        H.append(f"<h2>p{pid} &middot; {html.escape(title)} <small>({year})</small></h2>")
        group.sort(key=lambda i: (i["pdf_page"], i["verdict"] != "accepted"))
        # APPROVED TABLES COLLAPSE. They have been read and signed off, so they
        # are kept for reference and folded away rather than occupying the page
        # a reviewer is working down.
        for state, word in (("approved", "approved"),
                            ("dismissed", "confirmed as correctly refused")):
            done = [i for i in group if i["verdict"] == state]
            group = [i for i in group if i["verdict"] != state]
            if done:
                H.append(f"<details class='appr {state}'><summary>{len(done)} "
                         f"{word} on this paper &mdash; click to show</summary>")
                for it in done:
                    H.append(item_html(it))
                H.append("</details>")
        for it in group:
            H.append(item_html(it))
    H.append("</main></body></html>")
    (OUT / "index.html").write_text("".join(H), encoding="utf-8")


def item_html(it: dict) -> str:
    """One table: its id, its badge, the crop, and the parse or the reason."""
    v = it["verdict"]
    H = [f"<div class='item' id='{it['tid']}'><div class='hd'>"
         # `user-select:all` means ONE CLICK selects the whole id, so Ctrl+C
         # works with no scripting at all. The click handler also puts it on
         # the clipboard where the browser allows it, which a file:// page
         # sometimes does not, hence the selection as the real mechanism and
         # the copy as a convenience.
         f"<span class='tid' data-id='{it['tid']}' "
         f"title='click to copy'>{it['tid']}</span>"
         f"<a class='anch' href='#{it['tid']}' title='link to this table'>#</a>"
         f"<span class='tag {v}'>{v}</span>"
         f"<span class='lbl'>{html.escape(it.get('table_label') or '?')}</span>"
         f"<span class='meta'>page {it['pdf_page']}</span>"
         + (f"<a class='pdf' href=\"{it['pdf_url']}#page={it['pdf_page']}\""
            f" target='_blank' title='{html.escape(it['pdf_name'])}'>"
            f"open the PDF &#8599;</a>" if it.get("pdf_url") else "")]
    if v in ("accepted", "approved"):
        H.append(f"<span class='meta'>{html.escape(it.get('dataset') or '?')}"
                 + (f" &middot; version {it['dataset_version_id']}"
                    if it.get("dataset_version_id") else " &middot; version not stated")
                 + f" &middot; methods in {it['axis']}, metric in {it['metric_axis']}"
                 + f" &middot; unit {it.get('unit')}</span>")
    H.append("</div>")
    if it.get("caption"):
        H.append(f"<div class='cap'>{html.escape(it['caption'][:400])}</div>")
    H.append("<div class='pair'><div class='panel'><h3>Printed in the paper</h3>")
    H.append(f"<img src='{it['img']}' alt='table crop'>" if it.get("img")
             else "<div class='nope'>no crop could be rendered</div>")
    H.append("</div><div class='panel'>")
    if v in ("accepted", "approved"):
        H.append("<h3>What the miner read</h3>" + grid_html(it))
        if it.get("basis"):
            b = ", ".join(f"{html.escape(k)}: {html.escape(x)}"
                          for k, x in it["basis"].items())
            H.append(f"<div class='cap' style='margin-top:8px'>basis &mdash; {b}</div>")
    else:
        H.append("<h3>Why it was refused</h3>"
                 f"<div class='why'>{html.escape(it.get('reason') or '')}</div>")
    H.append("</div></div></div>")
    return "".join(H)


if __name__ == "__main__":
    raise SystemExit(main())

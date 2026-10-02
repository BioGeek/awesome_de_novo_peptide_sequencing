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

import build_pdf_library as bpl
import build_paper_comparisons as B

OUT = bpl.DEFAULT_DIR / "comparison-review"
CACHE = OUT / ".pages"
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


def grid_html(rec: dict) -> str:
    """The parse, as a table laid out the way the paper lays it out."""
    ncol = len(rec["col_head"])
    axis, maxis = rec["axis"], rec["metric_axis"]
    out = ["<table class='grid'>"]

    def mcell(j):
        return f"{html.escape(rec['metrics'][j])}<br><small>{html.escape(rec['levels'][j])}</small>"

    if axis == "columns":
        out.append("<tr><th></th>" + "".join(
            f"<th>{html.escape(rec['methods'][k][0])}"
            + (f"<br><small>v{html.escape(rec['methods'][k][1])}</small>"
               if rec["methods"].get(k) and rec["methods"][k][1] else "")
            + "</th>" for k in range(ncol)) + "</tr>")
        out.append("<tr><th class='dim'>metric</th>" + "".join(
            f"<td class='dim'>{mcell(k if maxis == 'columns' else 0)}</td>"
            for k in range(ncol)) + "</tr>")
        for r in rec["body"]:
            out.append(f"<tr><th>{html.escape(r['label'] or '-')}</th>" + "".join(
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
            m = rec["methods"].get(i)
            name = (f"{m[0]}" + (f" v{m[1]}" if m[1] else "")) if m else (r["label"] or "-")
            out.append(f"<tr><th>{html.escape(name)}</th>"
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
    for old in OUT.glob("*.png"):
        old.unlink()

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
                    name = f"p{pub['id']}-pg{pno + 1}-{n}.png"
                    ok = render(path, rec.get("page") or (pno + 1),
                                rec.get("bbox"), OUT / name)
                    rec.update({"pub": pub["id"], "title": pub["title"],
                                "year": str(pub["publication_date"] or "")[:4],
                                "img": name if ok else None,
                                "pdf_page": rec.get("page") or (pno + 1)})
                    items.append(rec)
                    tally[rec["verdict"]] += 1
        print(f"  p{pub['id']:<4} {len([i for i in items if i['pub']==pub['id']]):>3} "
              f"table(s)  {pub['title'][:52]}", flush=True)

    for f in CACHE.glob("*.png"):
        f.unlink()
    write_html(items, tally)
    print(f"\n  {tally['accepted']} accepted, {tally['rejected']} rejected")
    print(f"  {OUT / 'index.html'}")
    return 0


def write_html(items: list[dict], tally) -> None:
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
</style></head><body>"""]
    H.append(f"<header><h1>Comparison-table review</h1><div class='sub'>"
             f"{tally['accepted']} accepted and {tally['rejected']} refused, each beside the "
             f"printed table it came from. Local file, not published.</div></header><main>")
    for (pid, title, year), group in sorted(by_pub.items()):
        H.append(f"<h2>p{pid} &middot; {html.escape(title)} <small>({year})</small></h2>")
        group.sort(key=lambda i: (i["pdf_page"], i["verdict"] != "accepted"))
        for it in group:
            v = it["verdict"]
            H.append("<div class='item'><div class='hd'>"
                     f"<span class='tag {v}'>{v}</span>"
                     f"<span class='lbl'>{html.escape(it.get('table_label') or '?')}</span>"
                     f"<span class='meta'>page {it['pdf_page']}</span>")
            if v == "accepted":
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
            if v == "accepted":
                H.append("<h3>What the miner read</h3>" + grid_html(it))
                if it.get("basis"):
                    b = ", ".join(f"{html.escape(k)}: {html.escape(x)}"
                                  for k, x in it["basis"].items())
                    H.append(f"<div class='cap' style='margin-top:8px'>basis &mdash; {b}</div>")
            else:
                H.append("<h3>Why it was refused</h3>"
                         f"<div class='why'>{html.escape(it.get('reason') or '')}</div>")
            H.append("</div></div></div>")
    H.append("</main></body></html>")
    (OUT / "index.html").write_text("".join(H), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

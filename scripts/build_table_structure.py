#!/usr/bin/env python3
"""Read a table's column grouping out of its LaTeX source, where the source exists.

A PDF states where the ink is, not what governs what. When a method name spans
several columns, the only unambiguous statement of the span is a booktabs
`\\cmidrule`, and plenty of papers print none: measured on publication 49,
nearest-centre and label-edge-midpoints BOTH map the wrong columns to
Casanovo, because one column's centre falls 3 points from a boundary.

LaTeX says it exactly. `\\multicolumn{3}{c}{Casanovo}` is the grouping, with no
geometry and no guessing, so where a paper ships its source this is the right
place to get it.

    uv run python3 scripts/build_table_structure.py                 # every arXiv paper
    uv run python3 scripts/build_table_structure.py --ids 16,17

**REPORT ONLY.** It writes `table_structure_candidates.csv` and prints
proposed `SPANNER_OVERRIDE` entries for review. It inserts nothing and edits
no Python: deciding that a LaTeX table is the one behind a given PDF table is a
judgement, and the override registry is curated data. Paste what survives
review into `build_paper_comparisons.py`.

**Never in CI.** It needs the network and the PDF library.

Fetching is `curl`, for the reason recorded under 'The local PDF library' in
CLAUDE.md: HTTPS here is intercepted by a gateway whose certificates Python
rejects and curl accepts. Sources are cached under `.cache/arxiv-src/`.

The matching problem is real and is why this reports rather than writes. A
paper has several tabulars and the PDF has several tables, and the link between
them is the caption. Captions are compared with the same prefix-against-prefix
rule `build_pdf_library.title_score` uses, cut to the shorter string, because a
PDF caption arrives with its spaces mangled and sometimes truncated.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

import build_pdf_library as bpl
import build_paper_comparisons as B

HERE = pathlib.Path(__file__).resolve().parent.parent
CACHE = HERE / ".cache" / "arxiv-src"
OUT = HERE / "table_structure_candidates.csv"
UA = "awesome-de-novo (mailto:j.vangoey@instadeep.com)"


def arxiv_id(doi: str | None, url: str | None) -> str | None:
    """The bare id. `10.48550/arXiv.2402.11363` -> `2402.11363`.

    The same trap build_abstracts.py records: leaving the prefix on makes the
    API answer with an empty feed rather than an error.
    """
    for text in (doi or "", url or ""):
        m = re.search(r"(?i)arxiv[.:/]\s*(\d{4}\.\d{4,5})(v\d+)?", text)
        if m:
            return m.group(1)
    return None


def fetch_source(aid: str) -> pathlib.Path | None:
    """Download and unpack one arXiv source tarball. Cached."""
    dest = CACHE / aid
    if dest.exists():
        return dest
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        blob = pathlib.Path(td) / "src"
        r = subprocess.run(["curl", "-sL", "--max-time", "120", "-A", UA,
                            "-o", str(blob), f"https://arxiv.org/e-print/{aid}"],
                           capture_output=True)
        if r.returncode != 0 or not blob.exists() or blob.stat().st_size < 400:
            return None
        work = pathlib.Path(td) / "x"
        work.mkdir()
        try:
            with tarfile.open(blob) as tf:
                tf.extractall(work, filter="data")
        except tarfile.ReadError:
            # A single-file submission is gzipped .tex rather than a tarball.
            try:
                (work / "main.tex").write_bytes(gzip.decompress(blob.read_bytes()))
            except Exception:
                return None
        except Exception:
            return None
        dest.mkdir(parents=True, exist_ok=True)
        for f in work.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".tex", ".ltx"):
                shutil.copy2(f, dest / f"{abs(hash(str(f))) % 10**8}-{f.name}")
    return dest if any(dest.iterdir()) else None


def balanced(text: str, start: int) -> tuple[str, int]:
    """The brace group beginning at `start`, and the index after it.

    A regex cannot do this: `\\multicolumn{2}{c}{\\textbf{Casanovo}}` nests, and
    a greedy or lazy pattern takes either too much or too little.
    """
    assert text[start] == "{"
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif text[i] == "}" and text[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], len(text)


def strip_tex(s: str) -> str:
    """Plain text out of a LaTeX fragment, keeping what a header prints."""
    s = re.sub(r"\\(?:textbf|textit|emph|texttt|mathrm|text|bf|it|small|footnotesize)\b", "", s)
    s = re.sub(r"\\(?:cite|citep|citet|ref|label)\s*(\[[^\]]*\])?\{[^}]*\}", "", s)
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    s = s.replace("\\pi", "\u03c0").replace("~", " ").replace("\\&", "&")
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def cells(row: str) -> list[tuple[str, int]]:
    """One (text, span) per cell of a header row, in order."""
    out: list[tuple[str, int]] = []
    for raw in re.split(r"(?<!\\)&", row):
        raw = raw.strip()
        m = re.search(r"\\multicolumn\s*", raw)
        if m:
            i = raw.find("{", m.end() - 1)
            if i >= 0:
                n, j = balanced(raw, i)
                _spec, j = balanced(raw, raw.find("{", j))
                body, _ = balanced(raw, raw.find("{", j))
                try:
                    out.append((strip_tex(body), int(strip_tex(n))))
                    continue
                except ValueError:
                    pass
        out.append((strip_tex(raw), 1))
    return out


def tables(tex: str) -> list[dict]:
    """Every table environment: its caption and its header rows, expanded."""
    found = []
    for m in re.finditer(r"\\begin\{table\*?\}(.*?)\\end\{table\*?\}", tex, re.S):
        body = m.group(1)
        cap = ""
        cm = re.search(r"\\caption\s*", body)
        if cm:
            i = body.find("{", cm.end() - 1)
            if i >= 0:
                cap = strip_tex(balanced(body, i)[0])
        tm = re.search(r"\\begin\{(tabular\*?|tabularx)\}(.*?)\\end\{\1\}", body, re.S)
        if not tm:
            continue
        inner = tm.group(2)
        # Drop the column spec, then take the rows before the first numeric row.
        inner = re.sub(r"^\s*(\{[^{}]*\}|\[[^\]]*\])+", "", inner, count=1)
        rows = [r for r in re.split(r"\\\\", inner)]
        header_rows, data_cols = [], 0
        for r in rows:
            plain = strip_tex(r)
            if re.search(r"\d+\.\d+", plain):
                data_cols = max(data_cols, len(re.split(r"(?<!\\)&", r)))
                break
            if plain and not re.fullmatch(r"(?:\\?[a-z]+rule.*)?", plain, re.I):
                header_rows.append(r)
        expanded = []
        for r in header_rows:
            flat: list[str] = []
            for text, span in cells(r):
                flat.extend([text] * span)
            if flat:
                expanded.append(flat)
        if expanded:
            found.append({"caption": cap, "rows": expanded,
                          "n_cols": max(len(r) for r in expanded),
                          "data_cols": data_cols,
                          "cmidrules": re.findall(r"\\cmidrule\s*(?:\([^)]*\))?\s*\{(\d+)-(\d+)\}", body)})
    return found


def norm_cap(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated publication ids")
    args = ap.parse_args()

    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        import pdfplumber
        from rapidfuzz import fuzz
    except ModuleNotFoundError:
        print("needs pdfplumber: uv run --with pdfplumber python3 "
              "build_table_structure.py", file=sys.stderr)
        return 2

    con = sqlite3.connect(B.DB)
    con.row_factory = sqlite3.Row
    pubs = bpl.load_publications(con)
    cov = bpl.coverage(pubs, B.LIBRARY)
    rx = B.locator(con)
    index = B.algorithm_index(con)
    want = {int(i) for i in args.ids.split(",")} if args.ids else None

    rows = con.execute("""
        SELECT DISTINCT p.id, p.title, p.doi, p.url FROM publication p
          JOIN publication_algorithm pa ON pa.publication_id = p.id AND pa.role='describes'
          JOIN algorithm a ON a.id = pa.algorithm_id
         WHERE a.kind = 'algorithm' ORDER BY p.id""").fetchall()

    out: list[dict] = []
    tally: collections.Counter = collections.Counter()
    for pub in rows:
        if want and pub["id"] not in want:
            continue
        aid = arxiv_id(pub["doi"], pub["url"])
        if not aid:
            tally["no arXiv id"] += 1
            continue
        paths = cov.get(pub["id"]) or []
        if not paths:
            tally["no local PDF to match against"] += 1
            continue
        src = fetch_source(aid)
        if not src:
            tally["source not available from arXiv"] += 1
            continue
        tex = "\n".join(f.read_text(errors="replace") for f in src.glob("*.tex"))
        latex_tables = tables(tex)
        if not latex_tables:
            tally["no table environment found in source"] += 1
            continue
        tally["source parsed"] += 1

        # The PDF side: every block the miner sees, with its caption.
        pdf_tables = []
        with pdfplumber.open(paths[0]) as pdf:
            pages_text = [(pg.extract_text() or "") for pg in pdf.pages]
            vocab = B.paper_vocabulary("\n".join(pages_text), con)
            for pno in B.candidate_pages(pages_text, rx):
                got, vetoed, _ = B.extract(pdf.pages[pno])
                for tb in got:
                    pdf_tables.append((pno + 1, tb["table_label"], tb["caption"],
                                       len(tb["edges"])))
                for v in vetoed:
                    pdf_tables.append((pno + 1, v["table_label"], v["caption"], None))

        for page, label, caption, ncols in pdf_tables:
            best, score = None, 0
            a = norm_cap(caption)[:160]
            for lt in latex_tables:
                b = norm_cap(lt["caption"])[:160]
                if len(a) < 30 or len(b) < 30:
                    continue
                k = min(len(a), len(b))
                sc = fuzz.ratio(a[:k], b[:k])
                if sc > score:
                    best, score = lt, sc
            if not best or score < 85:
                tally["no LaTeX table matched this caption"] += 1
                continue
            # Which header row names the methods?
            method_row, resolved = None, None
            for r in best["rows"]:
                names = []
                for text in r:
                    try:
                        names.append(B.resolve_method(text, vocab, index)[1] if text else None)
                    except B.Reject:
                        names.append(None)
                if len({n for n in names if n}) >= 2:
                    method_row, resolved = r, names
                    break
            if not method_row:
                tally["matched, but no header row names >=2 methods"] += 1
                continue
            # Trim the leading label columns so the count matches the PDF's.
            printed = method_row
            if ncols and len(printed) > ncols:
                printed = printed[len(printed) - ncols:]
            tally["PROPOSED an override"] += 1
            out.append({"publication_id": pub["id"], "title": pub["title"][:90],
                        "table_label": label, "pdf_page": page,
                        "pdf_cols": ncols or "", "latex_cols": best["n_cols"],
                        "caption_match": score,
                        "latex_caption": best["caption"][:160],
                        "cmidrules": " ".join(f"{a}-{b}" for a, b in best["cmidrules"]),
                        "proposed": " | ".join(printed),
                        "fits": "yes" if ncols and len(printed) == ncols else "CHECK"})
        print(f"  p{pub['id']:<4} arXiv {aid:<12} {len(latex_tables):>2} latex table(s)  "
              f"{pub['title'][:46]}", flush=True)

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()) if out else
                           ["publication_id", "table_label", "proposed"])
        w.writeheader()
        for r in out:
            w.writerow(r)

    print()
    for k, v in tally.most_common():
        print(f"      {v:>4}  {k}")
    print(f"\n  {len(out)} proposal(s) -> {OUT.name}")
    print("\nPROPOSED SPANNER_OVERRIDE entries, for review before pasting:")
    for r in out:
        flag = "" if r["fits"] == "yes" else "   # COLUMN COUNT DISAGREES, CHECK"
        names = [n.strip() for n in r["proposed"].split("|")]
        print(f"    ({r['publication_id']}, {r['table_label']!r}): {names},{flag}")
    print("\nReport only: nothing was written to denovo.db or to any .py file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

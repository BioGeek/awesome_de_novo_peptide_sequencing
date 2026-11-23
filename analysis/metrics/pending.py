"""List the papers the metrics page has not read yet, with where their full text is.

    python3 analysis/metrics/pending.py              # a table, for a person
    python3 analysis/metrics/pending.py --json       # the same, for an agent or a script

A row of papers.jsonl that carries a `skipped_reason` is a paper the page counts as unread:
either check_metrics.py added it when the paper entered the catalog ("not yet read for this
page"), or its full text was not available when the page was built. For each, this finds the
full text the same two ways the page's first pass did: the denovo_wiki extraction
(raw/*.md, which names its publication_id), then a PDF in the local library whose file name
carries the paper's title. A paper with neither needs its PDF first (build_pdf_library.py).

Reading the paper and writing its row is not automated: README.md, "Adding a paper", says
what a row holds. Local only (it reads the PDF library and the wiki), never in CI. Stdlib only.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIBRARY = Path.home() / "Documents" / "de_novo_peptide_sequencing"
WIKI_RAW = Path.home() / "code" / "denovo_wiki" / "raw"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def wiki_texts():
    out = {}
    if WIKI_RAW.is_dir():
        for f in WIKI_RAW.glob("*.md"):
            head = f.read_text(encoding="utf-8", errors="replace")[:2000]
            m = re.search(r"^publication_id:\s*(\d+)", head, re.M)
            if m:
                out[int(m.group(1))] = f
    return out


def library_pdfs():
    """{normalised title prefix: path}, from the library's 'Author et al. - year - Title.pdf' names."""
    out = {}
    d = LIBRARY / "pdfs"
    if d.is_dir():
        for f in d.glob("*.pdf"):
            out[norm(f.stem.split(" - ", 2)[-1])] = f
    return out


def find_pdf(title, pdfs):
    t = norm(title)[:60]
    for k, f in pdfs.items():
        if k.startswith(t) or (len(k) >= 30 and t.startswith(k[:60])):
            return f
    return None


def main():
    rows = [json.loads(x) for x in (HERE / "papers.jsonl").read_text(encoding="utf-8").splitlines()]
    pending = [r for r in rows if r.get("skipped_reason")]
    wiki, pdfs = wiki_texts(), library_pdfs()
    out = []
    for r in pending:
        pid = r["pub_id"]
        text = wiki.get(pid)
        pdf = find_pdf(r["title"], pdfs)
        out.append({"pub_id": pid, "title": r["title"], "reason": r["skipped_reason"],
                    "wiki_text": str(text) if text else None, "pdf": str(pdf) if pdf else None})
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    if not out:
        print("pending: every paper in papers.jsonl has been read.")
        return 0
    ready = [o for o in out if o["wiki_text"] or o["pdf"]]
    print(f"pending: {len(out)} paper(s) not read for the metrics page, {len(ready)} with full text at hand.\n")
    for o in out:
        where = o["wiki_text"] or o["pdf"] or "NO FULL TEXT: fetch the PDF first (scripts/build_pdf_library.py)"
        print(f"  {o['pub_id']:>5}  {o['title'][:70]}\n         {o['reason']}\n         {where}\n")
    print("To add one: read it, replace its row in papers.jsonl as README.md 'Adding a paper' describes,\n"
          "then run analysis/metrics/run_all.sh (or just commit: the pre-commit hook rebuilds).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

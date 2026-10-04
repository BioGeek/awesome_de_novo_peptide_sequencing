#!/usr/bin/env python3
"""Pull an abstract out of a paper's own PDF, for the rows no API can serve.

Every candidate is a publication we hold whose `abstract` is empty: theses,
ML-conference papers and a few journal articles Europe PMC, OpenAlex and
Crossref all have nothing for.

The text is taken between an "Abstract" heading and whatever ends it, then
de-hyphenated and re-flowed. Conservative on purpose: anything that does not
start with a capital, runs shorter than 200 characters or longer than 4000, or
still carries column debris is REJECTED rather than written, because CLAUDE.md's
rule about build_abstracts.py applies here too -- a fragment presented as an
abstract is worse than no abstract.
"""
import argparse, re, sqlite3, subprocess, sys
from pathlib import Path

START = re.compile(r"^\s*(?:A\s?B\s?S\s?T\s?R\s?A\s?C\s?T|Abstract)\b[\s:.—-]*", re.M)
STOP = re.compile(
    r"^\s*(?:1[\s.]*Introduction|I[\s.]*Introduction|Introduction\b|Keywords?\b"
    r"|KEYWORDS\b|Key ?words\b|Index Terms|1\s+INTRODUCTION|Declaration"
    r"|Acknowledge?ments?\b|Table of Contents|CONTENTS|■\s*INTRODUCTION)", re.M)


def clean(t: str) -> str:
    # De-hyphenate BOTH shapes. `-\n` is a plain line break; `- ` with a space
    # is what -layout leaves when it joins two columns, and it produced
    # "Data-Independent Acquisi- tion" and "the pep- tides". Guarded to
    # lowercase-hyphen-lowercase, so a real compound -- "non-autoregressive",
    # which never has a space after its hyphen -- is left alone.
    t = re.sub(r"([a-z])-\n\s*([a-z])", r"\1\2", t)
    t = re.sub(r"([a-z])-[ \t]+([a-z])", r"\1\2", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    # ICLR/ICML margin line numbers survive -layout INSIDE the line as
    # zero-padded triples: "characterizing novel peptides and 013 proteins".
    t = re.sub(r"\s0\d\d(?=\s)", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"^\s*[\u25a0\u2022]\s*", "", t)
    return t.strip()


def looks_like_body(t: str) -> str | None:
    """Reasons to think this is article text rather than an abstract.

    Publication 254 is why: its two-column PDF puts "Abstract" and
    "1. Introduction" on the SAME physical line, so the heading search lands in
    the wrong column and returns the introduction -- which read plausibly and
    carried five citations.
    """
    cites = len(re.findall(r"\([A-Z][A-Za-z'-]+(?: et al\.)?,? (?:19|20)\d\d", t))
    if cites >= 3:
        return f"{cites} inline citations: reads as an introduction"
    if re.search(r"\b1\.?\s+Introduction\b", t):
        return "contains an Introduction heading"
    if re.search(r"\b0\d\d\b.*\b0\d\d\b", t):
        return "margin line numbers still present"
    # THE END is where this goes wrong invisibly. Length and first-character
    # checks pass happily on text that overran the abstract: publication 20 ran
    # into Chinese margin annotations, 55 stopped mid-word at "backed by high-",
    # 258 carried on into the introduction, and 318 finished on the page number
    # ("a special de ii"). An abstract ends in a terminator and contains no CJK.
    if not re.search(r"[.!?][\"')\]]?$", t):
        return f"does not end in a sentence: ...{t[-40:]!r}"
    if re.search(r"[\u3000-\u9fff]", t):
        return "contains CJK text: ran past the abstract into annotations"
    return None


def extract(pdf: Path, pages: int = 3) -> tuple[str | None, str]:
    for layout in (["-layout"], []):
        raw = subprocess.run(["pdftotext", *layout, "-f", "1", "-l", str(pages),
                              str(pdf), "-"], capture_output=True, text=True).stdout
        raw = re.sub(r"^\s*\d{1,3}\s*$", "", raw, flags=re.M)   # line numbers
        m = START.search(raw)
        if not m:
            continue
        tail = raw[m.end():]
        stop = STOP.search(tail)
        body = clean(tail[:stop.start()] if stop else tail[:3000])
        if not body:
            continue
        if not body[:1].isupper():
            return None, f"starts lowercase: {body[:48]!r}"
        if len(body) < 200:
            return None, f"too short ({len(body)}): {body[:48]!r}"
        why = looks_like_body(body)
        if why:
            return None, why
        if len(body) > 4000:
            body = body[:4000]
        return body, "layout" if layout else "raw"
    return None, "no Abstract heading found in the first pages"


ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--ids")
a = ap.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "lib", "/home/j-vangoey/code/awesome_de_novo_peptide_sequencing/build_pdf_library.py")
lib = importlib.util.module_from_spec(spec); spec.loader.exec_module(lib)
BASE = lib.DEFAULT_DIR
conn = sqlite3.connect("/home/j-vangoey/code/awesome_de_novo_peptide_sequencing/denovo.db")
pubs = lib.load_publications(conn)
have = lib.coverage(pubs, BASE)
want = {int(i) for i in a.ids.split(",")} if a.ids else None

ok = bad = 0
for p in pubs:
    if want and p["id"] not in want:
        continue
    if p["id"] not in have:
        continue
    if conn.execute("SELECT COALESCE(abstract,'') FROM publication WHERE id=?",
                    (p["id"],)).fetchone()[0]:
        continue
    body, how = extract(have[p["id"]][0])
    if body is None:
        bad += 1
        print(f"\n  REJECTED {p['id']:>4} {p['title'][:60]}\n           {how}")
        continue
    ok += 1
    print(f"\n  {p['id']:>4} [{how}, {len(body)} chars] {p['title'][:58]}")
    print(f"       {body[:300]}...")
    if a.apply:
        conn.execute("UPDATE publication SET abstract=?, abstract_source='pdf' "
                     "WHERE id=? AND COALESCE(abstract,'')=''", (body, p["id"]))
if a.apply:
    conn.commit()
print(f"\n{ok} extracted, {bad} rejected" + ("" if a.apply else "   (dry run)"))

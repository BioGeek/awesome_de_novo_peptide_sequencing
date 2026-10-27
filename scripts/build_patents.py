#!/usr/bin/env python3
"""Patents on *de novo* peptide sequencing: fetch, refresh and find more.

A patent is not a publication here. It has inventors rather than authors, an
assignee rather than an affiliation, a family of national filings rather than
a preprint and a version of record, and a citation list an examiner adds to.
Folding patents into `publication` would have counted them as papers in every
chart and on every author's page, which is the inflation CLAUDE.md warns about
for abstracts. So they have their own four tables, which this script creates
if they are missing:

  patent            one row per patent FAMILY, keyed by Google Patents' family
                    id, represented by one publication (`number`): the granted
                    US patent where there is one, otherwise the filing a person
                    chose. Its other publications are in `members`.
  patent_inventor   the inventors in printed order, each with an `author_id`
                    only where the same person is demonstrably in the catalog
  patent_algorithm  the catalog method a patent claims, with the evidence
  patent_citation   what a patent cites that the catalog holds: another
                    catalogued patent family, or a catalogued paper matched
                    from its non-patent literature

WHICH patents belong is a judgement, made by hand like every inclusion here:
`add` inserts one, and the rest of its row is this script's to fill. The bar
is the catalog's own: the claims are about *de novo* sequencing of peptides or
proteins by mass spectrometry, or about something done with its result.
`REJECTED` records the near misses with the reason, so `search` does not
propose them again.

    uv run python scripts/build_patents.py add US11573239B2 --algorithm DeepNovo
    uv run python scripts/build_patents.py refresh             # every patent row
    uv run python scripts/build_patents.py refresh --ids 3,4
    uv run python scripts/build_patents.py search              # patent_candidates.csv

`refresh` fills the bibliographic columns from the Google Patents page,
rebuilds the inventor rows and both citation sets. `search` is REPORT-ONLY,
like build_candidates.py: it runs the `SEARCHES` queries and walks the
citations of every catalogued patent in both directions, keeps the titles that
read as MS-based *de novo* sequencing, and writes patent_candidates.csv.

**An inventor is linked to an author on evidence, never on a name alone.**
"Ming Li" is several people. A name that matches exactly one author is linked
only if that author wrote a describing paper of a method the patent claims, or
is affiliated with the patent's assignee; `INVENTOR_AUTHOR` pins the rest.

**Literature citations are matched with build_citations.py's own matcher**
(`match_reference`, as a free-text citation), so a patent's prior art resolves
to a catalog paper by exactly the rules the paper citation graph uses: a DOI
the catalog does not hold is a different paper, a short title matches only
exactly, and a long one must appear as one span that starts a title.

Transport is curl, for the reason recorded under 'The local PDF library' in
CLAUDE.md. Google Patents answers a burst of requests with HTTP 503 and a
"Sorry..." page; that is never cached, and the script backs off and retries.
Pages and query results are cached in .cache/patents/. Never in CI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as H
import json
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_citations import match_reference, normalize_title  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
DB = HERE / "denovo.db"
CACHE = HERE / ".cache" / "patents"
CANDIDATES = HERE / "patent_candidates.csv"
UA = "Mozilla/5.0"
PAGE_GAP_S = 5.0     # Google Patents answers a faster burst with 503
QUERY_GAP_S = 8.0

DDL = """
CREATE TABLE IF NOT EXISTS patent (
    id                INTEGER PRIMARY KEY,
    number            TEXT NOT NULL UNIQUE,  -- representative publication, as Google Patents prints it
    family_id         TEXT UNIQUE,           -- Google Patents' family id (DOCDB family)
    title             TEXT,
    abstract          TEXT,
    assignee          TEXT,                  -- original assignee(s), as printed, '; '-joined
    assignee_current  TEXT,
    affiliation_id    INTEGER REFERENCES affiliation(id),  -- curated: the assignee's catalog row
    priority_date     TEXT,
    filing_date       TEXT,
    publication_date  TEXT,                  -- of the representative publication
    granted           INTEGER,               -- 1 when the representative is a grant (kind B or C)
    status            TEXT,                  -- Google's: Active, Pending, Expired, Abandoned, Ceased ...
    members           TEXT,                  -- the family's other publications, space-separated
    cpc               TEXT,                  -- CPC codes, space-separated
    cited_by          INTEGER,               -- publications citing this one, any applicant
    cited_by_families INTEGER,               -- families citing this family, any applicant
    n_npl             INTEGER,               -- non-patent literature citations printed
    url               TEXT,
    fetched_on        TEXT,
    notes             TEXT                   -- curated: why it is in scope
);
CREATE TABLE IF NOT EXISTS patent_inventor (
    patent_id      INTEGER NOT NULL REFERENCES patent(id) ON DELETE CASCADE,
    inventor_order INTEGER NOT NULL,
    name           TEXT NOT NULL,            -- as printed
    author_id      INTEGER REFERENCES author(id),
    PRIMARY KEY (patent_id, inventor_order)
);
CREATE TABLE IF NOT EXISTS patent_algorithm (
    patent_id    INTEGER NOT NULL REFERENCES patent(id) ON DELETE CASCADE,
    algorithm_id INTEGER NOT NULL REFERENCES algorithm(id),
    role         TEXT NOT NULL DEFAULT 'describes' CHECK (role IN ('describes', 'uses')),
    evidence     TEXT NOT NULL,
    PRIMARY KEY (patent_id, algorithm_id)
);
CREATE TABLE IF NOT EXISTS patent_citation (
    citing_patent_id     INTEGER NOT NULL REFERENCES patent(id) ON DELETE CASCADE,
    cited_patent_id      INTEGER REFERENCES patent(id) ON DELETE CASCADE,
    cited_publication_id INTEGER REFERENCES publication(id) ON DELETE CASCADE,
    via                  TEXT NOT NULL CHECK (via IN ('patent', 'family', 'npl')),
    examiner             INTEGER NOT NULL DEFAULT 0,  -- the examiner, not the applicant, added it
    reference            TEXT,                        -- the cited number, or the citation as printed
    CHECK ((cited_patent_id IS NULL) <> (cited_publication_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_patent_citation
    ON patent_citation (citing_patent_id, IFNULL(cited_patent_id, 0), IFNULL(cited_publication_id, 0));
CREATE INDEX IF NOT EXISTS ix_patent_algorithm_algorithm ON patent_algorithm (algorithm_id);
CREATE INDEX IF NOT EXISTS ix_patent_inventor_author ON patent_inventor (author_id);
"""

# Queries for `search`, in Google Patents' own syntax. TI= is the title; a
# full-text "de novo" matches thousands of protein-design and genome-assembly
# patents that merely mention a sequencing step, so the queries lean on titles.
SEARCHES = {
    "title: de novo + sequencing": 'TI=("de novo" sequencing)',
    "title: de novo + peptide/protein, mass spec": 'TI=("de novo" AND (peptide OR polypeptide OR protein OR antibody)) AND (mass spectrometry OR spectra OR spectrum)',
    "title: peptide sequencing + mass spectra": 'TI=(peptide AND sequencing AND (mass OR spectra OR spectrum OR spectrometry))',
    "title: amino acid sequence + mass spectra": 'TI=("amino acid sequence" AND (mass OR spectra OR spectrum OR spectrometry))',
    "title: antibody sequencing + mass spec": 'TI=(antibody AND sequencing) AND "mass spectrometry" AND "de novo"',
    # The field's companies, whose patents need not say "de novo" in a title.
    # A dict passes Google's own parameters beside the query.
    "assignee: Rapid Novor": {"q": "peptide OR protein OR antibody", "assignee": "Rapid Novor"},
    "assignee: Bioinformatics Solutions": {"q": "peptide OR protein", "assignee": "Bioinformatics Solutions"},
    "assignee: InstaDeep": {"q": "peptide OR protein OR spectrum", "assignee": "InstaDeep"},
    "assignee: Abterra": {"q": "peptide OR protein OR antibody", "assignee": "Abterra"},
}

# A candidate title that reads as MS-based de novo sequencing of a peptide or
# protein. A proposer's filter, so it leans permissive; a person decides.
IN_SCOPE = re.compile(r"de[- ]?novo|sequenc|spectr|peptide identification", re.I)
ANALYTE = re.compile(r"peptide|polypeptide|protein|antibod|amino acid|immunoglobulin|proteom|epitope|neoantigen", re.I)
OFF_SCOPE = re.compile(r"\bdesign|assembl|nucleic|\bDNA\b|\bRNA\b|genom|nucleotide|synthes|transcript|nanopore"
                       r"|single[- ]molecule|lipogenesis|phage display|binding (?:domain|protein)|vaccin", re.I)

# Patents (any family member's number) looked at and left out, with why, so
# `search` does not propose them again. Same contract as REJECTED in
# build_repository_search.py.
REJECTED: dict[str, str] = {}

# (patent number, inventor as printed) -> author id, or None to refuse a link
# the rules would otherwise make. For people the evidence rules cannot see.
INVENTOR_AUTHOR: dict[tuple[str, str], int | None] = {}


# ── transport ────────────────────────────────────────────────────────────────

def curl(url: str) -> tuple[int, str]:
    r = subprocess.run(["curl", "-s", "-L", "-A", UA, "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    return int(code or 0), body


def polite_get(url: str, gap: float) -> str | None:
    """GET with backoff on Google's 503 'Sorry' page; None if it never clears."""
    for wait in (0, 60, 300, 900):
        if wait:
            print(f"    throttled; waiting {wait}s", flush=True)
            time.sleep(wait)
        code, body = curl(url)
        time.sleep(gap)
        if code == 200 and "<title>Sorry...</title>" not in body[:2000]:
            return body
        if code == 404:
            return None
    print(f"    gave up on {url}", file=sys.stderr)
    return None


def page(number: str, force: bool = False) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{number}.html"
    if p.exists() and not force:
        return p.read_text(encoding="utf-8", errors="replace")
    body = polite_get(f"https://patents.google.com/patent/{number}/en", PAGE_GAP_S)
    if body:
        p.write_text(body, encoding="utf-8")
    return body


def query(q: str | dict, page_no: int = 0, num: int = 100) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    params = dict(q) if isinstance(q, dict) else {"q": q}
    inner = urllib.parse.urlencode({**params, "num": num, "page": page_no})
    key = hashlib.sha1(inner.encode()).hexdigest()[:16]
    p = CACHE / f"query-{key}.json"
    if p.exists():
        return json.loads(p.read_text())
    body = polite_get("https://patents.google.com/xhr/query?url="
                      + urllib.parse.quote(inner, safe="") + "&exp=", QUERY_GAP_S)
    if not body:
        return None
    try:
        res = json.loads(body)["results"]
    except (ValueError, KeyError):
        print(f"    query refused: {q!r}: {body[:120]}", file=sys.stderr)
        return None
    p.write_text(json.dumps(res))
    return res


# ── parsing a Google Patents page ───────────────────────────────────────────

def _text(s: str) -> str:
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _rows(doc: str, prop: str) -> list[dict]:
    out = []
    for m in re.finditer(rf'<tr itemprop="{prop}" itemscope repeat>(.*?)</tr>', doc, re.S):
        r = m.group(1)

        def g(k: str) -> str:
            hit = re.search(rf'itemprop="{k}"[^>]*>(.*?)</(?:span|td)>', r, re.S)
            return _text(hit.group(1)) if hit else ""
        out.append({"number": g("publicationNumber"), "priority": g("priorityDate"),
                    "published": g("publicationDate"), "assignee": g("assigneeOriginal"),
                    "title": g("title"), "examiner": 'itemprop="examinerCited"' in r})
    return out


def parse(doc: str) -> dict:
    meta = lambda n: [H.unescape(x) for x in re.findall(rf'<meta name="{n}" content="([^"]*)"', doc)]
    dd = lambda p: [_text(x) for x in re.findall(rf'<dd itemprop="{p}"[^>]*>(.*?)</dd>', doc, re.S)]

    def first(prop: str) -> str:
        hit = re.search(rf'itemprop="{prop}"[^>]*>([^<]*)<', doc)
        return hit.group(1).strip() if hit else ""
    ab = re.search(r'<section itemprop="abstract".*?</section>', doc, re.S)
    fam = re.search(r"<h2>ID=(\d+)</h2>", doc)
    status = re.search(r'itemprop="legalStatusIfi"[^>]*>(.*?)</', doc, re.S)
    number = (re.search(r'<dd itemprop="publicationNumber">([^<]+)<', doc) or [None, ""])[1]
    return {
        "number": number.strip(),
        "title": (meta("DC.title") or [""])[0].strip(),
        "abstract": re.sub(r"^Abstract\s+", "", _text(ab.group(0))) if ab else "",
        "inventors": dd("inventor"),
        "assignee": dd("assigneeOriginal"),
        "assignee_current": dd("assigneeCurrent"),
        "priority_date": first("priorityDate"),
        "filing_date": first("filingDate"),
        "publication_date": first("publicationDate"),
        "family_id": fam.group(1) if fam else "",
        "status": _text(status.group(1)) if status else "",
        "members": sorted({_text(x) for x in re.findall(
            r'<tr itemprop="docdbFamily".*?itemprop="publicationNumber">([^<]*)<', doc, re.S)}),
        "cites": _rows(doc, "backwardReferencesOrig"),
        "cites_family": _rows(doc, "backwardReferencesFamily"),
        "cited_by": _rows(doc, "forwardReferencesOrig"),
        "cited_by_family": _rows(doc, "forwardReferencesFamily"),
        # (citation as printed, added by the examiner)
        "npl": [(_text(t), 'itemprop="examinerCited"' in rest) for t, rest in re.findall(
            r'<tr itemprop="detailedNonPatentLiterature"[^>]*>.*?<span itemprop="title">(.*?)</span>(.*?)</tr>',
            doc, re.S)],
        "cpc": sorted({c for c in re.findall(r'<span itemprop="Code">([A-Z]\d\d[A-Z]\d+/\d+)</span>', doc)}),
    }


# ── names ───────────────────────────────────────────────────────────────────

def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z ]", " ", s.lower()).split())


def name_keys(name: str) -> set[str]:
    """A name and the same name with its middle initials dropped."""
    k = name_key(name)
    parts = k.split()
    out = {k}
    if len(parts) > 2:
        out.add(" ".join(p for p in parts if len(p) > 1))
    return out


# ── the database ────────────────────────────────────────────────────────────

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(DDL)
    return con


def family_index(con) -> dict[str, int]:
    """Every publication number of every catalogued family -> its patent id."""
    idx = {}
    for pid, number, members in con.execute("SELECT id, number, members FROM patent"):
        for n in [number, *(members or "").split()]:
            idx[n] = pid
    return idx


def add(con, number: str, algorithms: list[str], affiliation: int | None, note: str | None) -> int:
    doc = page(number)
    if not doc:
        sys.exit(f"could not fetch {number}")
    d = parse(doc)
    hit = con.execute("SELECT id, number FROM patent WHERE family_id = ?", (d["family_id"],)).fetchone()
    if hit:
        sys.exit(f"{number} is in family {d['family_id']}, already catalogued as {hit[1]} (patent {hit[0]})")
    cur = con.execute("INSERT INTO patent (number, family_id, affiliation_id, notes) VALUES (?, ?, ?, ?)",
                      (number, d["family_id"], affiliation, note))
    pid = cur.lastrowid
    for spec in algorithms:
        name, _, role = spec.partition(":")
        aid = con.execute("SELECT id FROM algorithm WHERE name = ?", (name,)).fetchone()
        if not aid:
            sys.exit(f"no algorithm called {name!r}")
        con.execute("INSERT INTO patent_algorithm (patent_id, algorithm_id, role, evidence) VALUES (?, ?, ?, ?)",
                    (pid, aid[0], role or "describes", "added by hand; see the patent's notes"))
    return pid


def refresh(con, ids: list[int] | None, force: bool) -> None:
    rows = con.execute("SELECT id, number FROM patent ORDER BY id").fetchall()
    if ids:
        rows = [r for r in rows if r[0] in ids]
    pubs = con.execute("SELECT id, title, doi, publication_date FROM publication").fetchall()
    by_doi = {(d or "").lower(): i for i, _, d, _ in pubs if d}
    by_title = {normalize_title(t): i for i, t, _, _ in pubs if t}
    title_year = {normalize_title(t): int(d[:4]) for _, t, _, d in pubs if t and d}
    titles = list(by_title)
    pub_date = {i: d for i, _, _, d in pubs}
    authors: dict[str, list[int]] = {}
    for aid, name in con.execute("SELECT id, name FROM author"):
        for k in name_keys(name):
            authors.setdefault(k, []).append(aid)
    today = date.today().isoformat()
    parsed: dict[int, dict] = {}

    for pid, number in rows:
        doc = page(number, force=force)
        if not doc:
            print(f"  {number}: no page, left as it was")
            continue
        d = parse(doc)
        parsed[pid] = d
        granted = 1 if re.search(r"[BC]\d?$", number) else 0
        members = [m for m in d["members"] if m != number]
        con.execute("""UPDATE patent SET family_id = ?, title = ?, abstract = ?, assignee = ?,
                       assignee_current = ?, priority_date = ?, filing_date = ?, publication_date = ?,
                       granted = ?, status = ?, members = ?, cpc = ?, cited_by = ?, cited_by_families = ?,
                       n_npl = ?, url = ?, fetched_on = ? WHERE id = ?""",
                    (d["family_id"], d["title"], d["abstract"], "; ".join(d["assignee"]),
                     "; ".join(d["assignee_current"]), d["priority_date"], d["filing_date"],
                     d["publication_date"], granted, d["status"], " ".join(members), " ".join(d["cpc"]),
                     len(d["cited_by"]), len(d["cited_by_family"]), len(d["npl"]),
                     f"https://patents.google.com/patent/{number}/en", today, pid))

        # Inventors: who among them is demonstrably a catalog author.
        describers = {a for (a,) in con.execute(
            """SELECT DISTINCT pa.author_id FROM patent_algorithm x
                 JOIN publication_algorithm pl ON pl.algorithm_id = x.algorithm_id AND pl.role = 'describes'
                 JOIN publication_author pa ON pa.publication_id = pl.publication_id
                WHERE x.patent_id = ?""", (pid,))}
        at_assignee = {a for (a,) in con.execute(
            """SELECT aa.author_id FROM patent p JOIN author_affiliation aa ON aa.affiliation_id = p.affiliation_id
                WHERE p.id = ?""", (pid,))}
        con.execute("DELETE FROM patent_inventor WHERE patent_id = ?", (pid,))
        for i, name in enumerate(d["inventors"], 1):
            if (number, name) in INVENTOR_AUTHOR:
                aid = INVENTOR_AUTHOR[(number, name)]
            else:
                cands = {a for k in name_keys(name) for a in authors.get(k, [])}
                ok = cands & (describers | at_assignee)
                aid = next(iter(ok)) if len(ok) == 1 else None
                if cands and aid is None:
                    print(f"  {number}: inventor {name!r} shares a name with author(s) "
                          f"{sorted(cands)}, no evidence; not linked")
            con.execute("INSERT INTO patent_inventor (patent_id, inventor_order, name, author_id) VALUES (?, ?, ?, ?)",
                        (pid, i, name, aid))

        # Literature this patent cites that the catalog holds.
        con.execute("DELETE FROM patent_citation WHERE citing_patent_id = ? AND via = 'npl'", (pid,))
        limit = (d["publication_date"] or today)
        for ref, examiner in d["npl"]:
            doi = re.search(r"\b10\.\d{4,9}/[^\s,;]+", ref)
            doi = doi.group(0).rstrip(".)").lower() if doi else ""
            years = re.findall(r"\b(?:19|20)\d\d\b", ref)
            cited = match_reference(doi, ref, True, by_doi, by_title, titles, [], [], 0,
                                    int(years[-1]) if years else None, title_year)
            if cited and (pub_date.get(cited) or "") <= limit:
                con.execute("""INSERT OR IGNORE INTO patent_citation
                               (citing_patent_id, cited_publication_id, via, examiner, reference)
                               VALUES (?, ?, 'npl', ?, ?)""", (pid, cited, int(examiner), ref[:500]))

    # Patent-to-patent edges need every family's members first.
    fam = family_index(con)
    for pid, d in parsed.items():
        con.execute("DELETE FROM patent_citation WHERE citing_patent_id = ? AND via IN ('patent', 'family')", (pid,))
        for via, refs in (("patent", d["cites"]), ("family", d["cites_family"])):
            for r in refs:
                tgt = fam.get(r["number"])
                if tgt and tgt != pid:
                    con.execute("""INSERT OR IGNORE INTO patent_citation
                                   (citing_patent_id, cited_patent_id, via, examiner, reference)
                                   VALUES (?, ?, ?, ?, ?)""", (pid, tgt, via, int(r["examiner"]), r["number"]))
    con.commit()
    n_pub, n_pat = con.execute("""SELECT COUNT(cited_publication_id), COUNT(cited_patent_id)
                                  FROM patent_citation""").fetchone()
    print(f"refreshed {len(parsed)} patent(s); {n_pub} citations of catalog papers, "
          f"{n_pat} of catalog patents")


def search(con) -> None:
    fam = family_index(con)
    rejected = set(REJECTED)
    cands: dict[str, dict] = {}

    def consider(number: str, title: str, assignee: str, priority: str, found: str) -> None:
        if number in fam or number in rejected:
            return
        if not (IN_SCOPE.search(title) and ANALYTE.search(title)) or OFF_SCOPE.search(title):
            return
        c = cands.setdefault(number, {"number": number, "priority": priority, "assignee": assignee,
                                      "title": title, "found_by": set()})
        c["found_by"].add(found)

    for label, q in SEARCHES.items():
        n = 0
        for page_no in range(10):
            res = query(q, page_no)
            if not res:
                break
            hits = [x["patent"] for c in res.get("cluster", []) for x in c["result"]]
            for p in hits:
                consider(p["publication_number"], _text(p["title"]).replace("…", ""),
                         _text(p.get("assignee", "")), p.get("priority_date", ""), f"search: {label}")
            n += len(hits)
            if not hits or n >= (res.get("total_num_results") or 0):
                break
        print(f"  {label}: {n} hits")

    for pid, number in con.execute("SELECT id, number FROM patent"):
        doc = page(number)
        if not doc:
            continue
        d = parse(doc)
        for key in ("cites", "cites_family", "cited_by", "cited_by_family"):
            for r in d[key]:
                consider(r["number"], r["title"], r["assignee"], r["priority"],
                         f"{key.replace('_', ' ')} {number}")

    # Collapse to one row per family: fetch each candidate's page (cached).
    seen_fam: dict[str, dict] = {}
    for c in sorted(cands.values(), key=lambda c: c["priority"]):
        doc = page(c["number"])
        d = parse(doc) if doc else {}
        fid = d.get("family_id") or c["number"]
        if any(m in fam or m in rejected for m in d.get("members", [])):
            continue
        row = seen_fam.setdefault(fid, {**c, "family_id": fid, "members": " ".join(d.get("members", [])),
                                        "abstract": (d.get("abstract") or "")[:400], "found_by": set()})
        row["found_by"] |= c["found_by"]
    with CANDIDATES.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["number", "family_id", "priority", "assignee", "title",
                                           "n_found", "found_by", "members", "abstract"])
        w.writeheader()
        for r in sorted(seen_fam.values(), key=lambda r: (-len(r["found_by"]), r["priority"])):
            w.writerow({**{k: r[k] for k in ("number", "family_id", "priority", "assignee", "title",
                                             "members", "abstract")},
                        "n_found": len(r["found_by"]), "found_by": " | ".join(sorted(r["found_by"]))})
    print(f"{len(seen_fam)} candidate famil(ies) -> {CANDIDATES.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="catalogue a patent family by one of its publication numbers")
    a.add_argument("number")
    a.add_argument("--algorithm", action="append", default=[], metavar="NAME[:uses]")
    a.add_argument("--affiliation", type=int)
    a.add_argument("--note")
    r = sub.add_parser("refresh", help="refill every patent row from its page")
    r.add_argument("--ids", type=lambda s: [int(x) for x in s.split(",")])
    r.add_argument("--force", action="store_true", help="refetch pages rather than use the cache")
    sub.add_parser("search", help="propose patents the catalog is missing")
    args = ap.parse_args()
    con = connect()
    if args.cmd == "add":
        pid = add(con, args.number, args.algorithm, args.affiliation, args.note)
        refresh(con, [pid], force=False)
    elif args.cmd == "refresh":
        refresh(con, args.ids, args.force)
    else:
        search(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())

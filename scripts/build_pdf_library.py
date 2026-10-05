#!/usr/bin/env python3
"""Keep a local folder of paper PDFs in step with the catalog.

The catalog says which papers exist; this says which ones you have a copy of,
fetches the ones that are legally free, names every file the same way, and lists
what is left so a human can go and get it.

OFFLINE AND LOCAL, and the only script here that is both. It touches a folder
OUTSIDE the repository (`--dir`, default ~/Documents/de_novo_peptide_sequencing,
the PDFs themselves in its `pdfs/` subfolder),
fetches a few hundred URLs, and writes nothing into denovo.db. It must never run
in CI: there is no library there to update.

    python3 scripts/build_pdf_library.py report                 # what is missing, and why
    python3 scripts/build_pdf_library.py fetch                  # download what is free
    python3 scripts/build_pdf_library.py rename                 # re-derive every filename
    uv run --with pypdf python3 scripts/build_pdf_library.py \\
        ingest manual/ --map 978-3-031-94039-2.pdf=13:106-114

WHAT COUNTS AS FREE. Only a location that arXiv, bioRxiv, Europe PMC, OpenAlex
or the record's own landing page states is free to read. Nothing is proxied and
no paywall is circumvented; a paper with nothing free is REPORTED, not worked
around. PMC is deliberately not tried at all: every automated route into it is
closed by design (/articles/PMC.../pdf/ answers 200 with a bot-mitigation page
on both hosts, europepmc.org/articles/PMC...?pdf=render answers 403, and the OA
web service has moved off its documented oa.fcgi path), and PMC directs bulk
users to its FTP/cloud packages instead. That is a reasonable position, not one
to route around.

FETCH THROUGH CURL, NOT REQUESTS, and this is the single most useful thing in
this file. On the machine this was written for, HTTPS is intercepted by a
Perimeter81 Secure Web Gateway whose re-signed certificates carry no Authority
Key Identifier, so Python rejects them --

    [SSL: CERTIFICATE_VERIFY_FAILED] Missing Authority Key Identifier

-- while curl, against the SAME /etc/ssl/certs/ca-certificates.crt, accepts them
and returns 200. The first run read that as the publishers blocking us: it
downloaded 36 of 322 and filed 140 under "open access but the download failed",
including five theses hosted on this project's own domain. Switching the
transport to curl, with verification still on, took the same set from 36 to 101.
If a fetch fails everywhere at once, suspect the transport before the source.

TITLE MATCHING IS PREFIX-AGAINST-PREFIX, cut to the shorter of the two strings,
because a filename is a TRUNCATED title. The two obvious metrics are both wrong
here, in opposite directions, and each shipped a wrong answer before this was
settled:

  * `partial_ratio` scores a short title sitting INSIDE a long filename at 100,
    so DeepNovoV2's paper was filed under publication 57, "Peptide Sequencing
    with Deep Learning", whose whole title appears in its filename.
  * `token_set_ratio` ignores tokens the other side does not have, so
    publication 315, whose title is the two words "De novo Peptide Sequencing",
    scored 100 against 26 of 37 files.

Where two rows share a title -- a preprint and its version of record -- the
filename's year breaks the tie, and `--map` exists for the rest.

THE FIRST AUTHOR COMES FROM AN ORDERED SUBQUERY, never from GROUP_CONCAT with a
trailing ORDER BY. That clause orders the subquery's result rows, of which there
is one, and leaves the concatenation in scan order -- which is author.id order,
so whoever has the lowest id leads every paper they appear on. It named 52 of
107 downloads after the wrong author, and the same expression was in index.qmd,
where it fed the BibTeX export's author field and its citation key.
"""

from __future__ import annotations

import argparse
import collections
import glob
import csv
import datetime
import functools
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent.parent
DB_PATH = HERE / "denovo.db"
DEFAULT_DIR = Path.home() / "Documents" / "de_novo_peptide_sequencing"
# The PDFs live in their own subfolder, so the root holds only the working
# folders beside them (manual/, missing/, supplements/, comparison-review/,
# citation-sweep/) and pdf_status.csv. Every reader goes through pdfs() and
# every writer through pdf_dir(), so no script globs the root for PDFs.
PDF_SUBDIR = "pdfs"


def pdf_dir(root: Path) -> Path:
    d = root / PDF_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d
MAILTO = "j.vangoey@instadeep.com"          # same contact the other builders send
UA = ("awesome_de_novo_peptide_sequencing/1.0 "
      "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
      f"mailto:{MAILTO})")
# A desktop browser's UA, sent only when --browser-ua is passed. The papers it
# is for are ones a source reports as OPEN ACCESS and which a person can open in
# a browser and save; what blocks the scripted fetch is bot detection, not a
# paywall, and nothing here touches a paywall either way. It is opt-in because
# misrepresenting the client is a choice the person running this should make
# knowingly, and because a publisher's terms may still forbid bulk download
# however the request is labelled: it is for assembling your own reading copies,
# at one request per host per 1.5s, not for crawling.
# MEASURED: 0 of 27. ACS, Europe PMC's pdf=render and IEEE answered exactly as
# before -- 403, 403, 202 -- so on this network the block is not the user agent.
# The flag stays because another network may fare differently and because the
# alternative to trying it was guessing, but do not expect it to help.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
# Papers whose full text is UNDER EMBARGO: a date is published, the date is in
# the future, and no amount of retrying or re-routing changes that. These are
# not paywalled (nobody can buy them) and not blocked (nothing is refusing the
# request); they do not exist publicly yet. `fetch` skips them and `report`
# files them in their own bucket, so a known-unavailable paper stops being
# re-proposed every run -- the same feedback-loop fix the denovo-radar harvest
# needed for rejected papers.
#
# Recording the DATE rather than a flag means the entry expires by itself: once
# it passes, the paper returns to the normal buckets and gets fetched like any
# other.
EMBARGOED: dict[int, tuple[str, str]] = {
    # UNT's own record says so: "The contents of this dissertation are
    # unavailable for full viewing on this site. ... It will be made available
    # on this site on June 1, 2030." The DOI it points to instead resolves back
    # to the same embargoed record, so there is no second route.
    119: ("2030-06-01", "UNT Digital Library embargo, DiffNovo-DIA thesis"),
}


# Papers a PERSON has checked, so the scripts stop chasing them. Two verdicts:
#   no-pdf     no full text exists or ever will: a code deposit, a record that
#              is an abstract and nothing more
#   paywalled  the publisher sells it and no free copy was found by hand either
# Without this a paper the owner already looked at is re-tried on every fetch
# and reshuffled between buckets by whatever the APIs say that day, and the
# missing/ lists never shrink to the work that is actually left. `fetch` skips
# these and `report` files them before reading any stored verdict. Checked
# 2026-10-05 from the owner's list; publication 700 was on it as paywalled and
# turned out to have an author copy on HAL, so it is filed, not listed here.
CHECKED: dict[int, tuple[str, str]] = {
    216: ("no-pdf", "this catalog's own Zenodo record: a code deposit"),
    355: ("no-pdf", "JSSR record is an abstract only, no galley"),
    356: ("no-pdf", "JSSR record is an abstract only, no galley"),
    **{i: ("paywalled", "checked by hand 2026-10-05")
       for i in (262, 289, 357, 502, 527, 621, 647, 662, 762, 817, 855)},
}


def embargoed(pub_id: int, today: str | None = None) -> tuple[str, str] | None:
    """The embargo still in force for this publication, or None."""
    entry = EMBARGOED.get(pub_id)
    if not entry:
        return None
    now = today or datetime.date.today().isoformat()
    return entry if entry[0] > now else None


MIN_GAP = 1.5                                # seconds between hits on one host
_last: dict[str, float] = collections.defaultdict(float)
_ua = UA                                     # swapped by --browser-ua


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def fetch(url: str, want_json: bool = False, tries: int = 3):
    """GET through curl, paced per host. -> (ok, body_bytes | reason_str)."""
    host = re.match(r"https?://([^/]+)", url)
    host = host.group(1) if host else url
    for attempt in range(tries):
        wait = MIN_GAP - (time.time() - _last[host])
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        tmp = Path(tempfile.mkstemp(prefix="pdflib-")[1])
        try:
            cp = subprocess.run(
                ["curl", "-sS", "-L", "--max-time", "90", "-A", _ua,
                 "-H", "Accept: application/json" if want_json
                       else "Accept: application/pdf,*/*",
                 *(["-H", "Accept-Language: en-US,en;q=0.9"]
                   if _ua is BROWSER_UA else []),
                 "-o", str(tmp), "-w", "%{http_code}", url],
                capture_output=True, text=True, timeout=120)
            code, body = (cp.stdout or "").strip()[-3:], tmp.read_bytes()
        except subprocess.TimeoutExpired:
            code, body = "", b""
        finally:
            tmp.unlink(missing_ok=True)
        if not code or code in ("429", "500", "502", "503", "504"):
            if attempt == tries - 1:
                return False, f"HTTP {code}" if code else "curl timeout"
            time.sleep(2 ** attempt * 3)
            continue
        if code != "200":
            return False, f"HTTP {code}"        # 403 / 404: no point retrying
        return True, body
    return False, "exhausted"


def cached(cache: Path, key: str, fn):
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", key)[:180] + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    val = fn()
    # A transport failure or an API outage is not an answer, so it is not
    # cached: OpenAlex answered "Anonymous search is paused while the search
    # cluster recovers" for a whole run once, and caching that would have made
    # 20 papers permanently unresolvable.
    if not (isinstance(val, dict) and ("_fail" in val or "error" in val)):
        p.write_text(json.dumps(val))
    return val


def get_json(cache: Path, key: str, url: str):
    """Cached JSON. A transport failure is reported, never silently cached.

    It prints, because the alternative is what happened with Europe PMC: an
    HTTP 400 from a malformed URL looked exactly like "this paper has no
    record", and the script carried on deciding access from OpenAlex alone.
    """
    def go():
        ok, body = fetch(url, want_json=True)
        if not ok:
            print(f"    ! lookup failed ({body}) {url[:72]}", flush=True)
            return {"_fail": body}
        try:
            return json.loads(body)
        except ValueError:
            return {"_fail": "not JSON"}
    return cached(cache, key, go)


# --------------------------------------------------------------------------
# The catalog side
# --------------------------------------------------------------------------

def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def title_score(title_n: str, tail: str) -> float:
    """Prefix against prefix, cut to the shorter. See the module docstring."""
    n = min(len(title_n), len(tail))
    return fuzz.ratio(title_n[:n], tail[:n]) if n >= 20 else 0


def load_publications(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("""
        SELECT p.id, p.title, p.doi, p.url, p.journal, p.publication_type,
               p.publication_date,
               (SELECT a.name FROM publication_author pa
                  JOIN author a ON a.id = pa.author_id
                 WHERE pa.publication_id = p.id
                 ORDER BY pa.author_order LIMIT 1) AS first_author,
               (SELECT a.name FROM publication_author pa
                  JOIN author a ON a.id = pa.author_id
                 WHERE pa.publication_id = p.id
                 ORDER BY pa.author_order LIMIT 1 OFFSET 1) AS second_author,
               (SELECT COUNT(*) FROM publication_author
                 WHERE publication_id = p.id) AS n_authors
          FROM publication p ORDER BY p.id""")]
    for r in rows:
        r["n"] = norm(r["title"])
    # TWINS: a preprint and its version of record with the same title, year and
    # first author would get the SAME filename, and `fetch` wrote one over the
    # other -- LIPNovo's arXiv preprint was replaced by its ICML proceedings PDF.
    # The preprint side is marked, as the URL policy in slugs.py marks it.
    from collections import Counter
    key = lambda r: (r["n"], str(r["publication_date"])[:4], r["first_author"])
    seen = Counter(key(r) for r in rows)
    for r in rows:
        r["twin"] = seen[key(r)] > 1
    return rows


NAME_MAX = 255          # bytes, on ext4 and every filesystem this will meet


def zotero_name(p: dict) -> str:
    """"Surname et al. - 2024 - Full Title.pdf".

    The FULL title, in the catalog's own casing, with no length cut. Only the
    characters a filename cannot hold are touched: path separators, and
    punctuation outside the kept set, each collapsed to a space.

    There used to be a `[:95]` on the title, inherited from what Zotero happens
    to export. It cost information on 154 of 359 papers -- "Delineating the
    venom toxin arsenal of Malabar pit viper..." stopped mid-sentence -- for no
    reason that survives inspection: the longest full name this catalog can
    produce is 223 bytes, comfortably inside NAME_MAX. The guard below is
    therefore for a hypothetical future title, not for anything here, and it
    cuts on a word boundary so a trimmed name still reads as one.
    """
    first = (p["first_author"] or "Unknown").split()[-1]
    n = p["n_authors"] or 1
    if n > 2:
        tag = f"{first} et al."
    elif n == 2:
        tag = f"{first} and {(p['second_author'] or '').split()[-1]}"
    else:
        tag = first
    title = re.sub(r"[^\w áàâäéèêëíìîïóòôöúùûüçñšžπ'()+-]", " ", p["title"])
    title = re.sub(r"\s+", " ", title).strip()
    stem = f"{tag} - {str(p['publication_date'])[:4]} - "
    budget = NAME_MAX - len((stem + ".pdf").encode())
    while len(title.encode()) > budget:
        title = title[:title.rstrip().rfind(" ")].rstrip() if " " in title.strip() \
            else title[:-1]
    if p.get("twin") and p.get("publication_type") in ("preprint", "postprint"):
        title += f" ({p['publication_type']})"
    return f"{stem}{title}.pdf".replace("/", "-")


PREPRINT_BANNER = re.compile(
    r"bioRxiv preprint|medRxiv preprint|This is a preprint|chemrxiv|arXiv:\d{4}\.\d{4,5}v\d+ \["
    r"|Research Square|SSRN Electronic Journal", re.I)


@functools.lru_cache(maxsize=None)
def first_pages(path: str) -> str:
    try:
        return subprocess.run(["pdftotext", "-f", "1", "-l", "2", path, "-"],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ""


def identify(pubs: list[dict], stem: str, text: str = "",
             path: Path | None = None) -> tuple[dict | None, str]:
    """Which publication is this file? By DOI in its text, else by title."""
    by_doi = {(p["doi"] or "").lower(): p for p in pubs if p["doi"]}
    for d in (x.rstrip(".,);") for x in
              re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text)):
        if d.lower() in by_doi:
            return by_doi[d.lower()], f"doi {d}"
    # An arXiv PDF stamps "arXiv:2602.20209v1" down its margin and carries no
    # DOI anywhere, so look for the id and build the DOI arXiv mints from it.
    # The filename is checked too, since that is what an arXiv download is
    # called.
    for m in re.finditer(r"arxiv[:\s]*(\d{4}\.\d{4,5})", text + " " + stem, re.I):
        d = f"10.48550/arxiv.{m.group(1)}"
        if d in by_doi:
            return by_doi[d], f"arxiv {m.group(1)}"
    m = re.match(r"^(\d{4}\.\d{4,5})v\d+$", stem)
    if m and f"10.48550/arxiv.{m.group(1)}" in by_doi:
        return by_doi[f"10.48550/arxiv.{m.group(1)}"], f"arxiv {m.group(1)}"
    m = re.match(r"^(.*?) - ((?:19|20)\d{2}) - (.*)$", stem)
    year, tail = (m.group(2), norm(m.group(3))) if m else (None, norm(stem))
    scored = sorted(((title_score(p["n"], tail), p) for p in pubs),
                    key=lambda t: -t[0])
    top = [(s, p) for s, p in scored if s >= 90]
    if len(top) > 1 and any(p.get("twin") for _s, p in top):
        # A TWIN PAIR shares title, year and author, so only the filename's
        # marker can decide: '(preprint)' is the preprint, no marker the
        # version of record. See load_publications.
        marked = bool(re.search(r"\((preprint|postprint)\)\s*$", m.group(3) if m else stem))
        # No marker is not proof of a version of record: a preprint filed before
        # its journal twin existed carries none. Its first page says what it is
        # (bioRxiv's banner, arXiv's margin stamp), so ask the PDF.
        if not marked and path is not None:
            marked = bool(PREPRINT_BANNER.search(text or first_pages(str(path))))
        top = [(sc, p) for sc, p in top
               if (p.get("publication_type") in ("preprint", "postprint")) == marked] or top
    if year and len(top) > 1:
        # A preprint and its version of record share a title; the year decides.
        top = [(s, p) for s, p in top
               if str(p["publication_date"])[:4] == year] or top
    if len(top) > 1:
        # Still tied, which happens when two rows differ ONLY in the title's
        # capitalisation or punctuation and carry the same year: publication
        # 108's "NovoBoard: a comprehensive framework" against publication 80's
        # "A Comprehensive Framework", 29's "data-independent" against 103's
        # "data independent". title_score normalises both away, so the pick was
        # arbitrary and `rename` wanted to recase a file it had just accepted.
        #
        # Break it on the EXACT characters, which is the one piece of evidence
        # the filename still carries, then on the lower id. Deliberately not
        # "whichever candidate the current filename already matches": that was
        # the first fix, and it tied the answer to the name, so changing the
        # naming rule -- dropping the title truncation -- made four files start
        # matching the other row of their pair. A rule that reads only the
        # title text converges wherever it starts.
        raw = m.group(3) if m else stem
        def exactness(p):
            t = re.sub(r"\s+", " ",
                       re.sub(r"[^\w áàâäéèêëíìîïóòôöúùûüçñšžπ'()+-]", " ",
                              p["title"])).strip()
            return fuzz.ratio(t[:len(raw)], raw[:len(t)])
        top = sorted(top, key=lambda sp: (-exactness(sp[1]), sp[1]["id"]))
    return (top[0][1], f"title {top[0][0]:.0f}") if top else (None, "")


def pdfs(root: Path) -> list[Path]:
    """Every PDF in the library.

    The PDFs are ONE flat folder, `pdfs/` under the root. The root itself and
    `retrieved/` are still read so an older layout keeps working, but nothing
    is written to either: `retrieved/` existed to keep downloads away from the
    owner's curated filenames, and once the same generator named every file it
    only served to hide half the library from `rename` and `dedupe`, which
    looked in one folder each.
    """
    return sorted(list((root / PDF_SUBDIR).glob("*.pdf")) + list(root.glob("*.pdf"))
                  + list((root / "retrieved").glob("*.pdf")))


def coverage(pubs: list[dict], root: Path) -> dict[int, list[Path]]:
    """publication id -> the local files that are it. The FOLDER is the truth."""
    out: dict[int, list[Path]] = collections.defaultdict(list)
    for f in pdfs(root):
        pub, _how = identify(pubs, f.stem, path=f)
        if pub:
            out[pub["id"]].append(f)
    return out


# --------------------------------------------------------------------------
# Supplementary files
# --------------------------------------------------------------------------
#
# A paper's SUPPLEMENT is where its tables often are: Nature-family papers put
# their comparison tables in a Supplementary Information PDF and keep the main
# text to figures (DiffNovo-DIA, pi-PrimeNovo, PepNet, GraphNovo, InstaNovo).
#
# They live in a SUBFOLDER on purpose. pdfs() reads only the library root, so
# coverage(), rename and dedupe never see a supplement: a supplement filed in
# the root would be identified as its paper by title and then renamed onto, or
# deduplicated against, the main PDF. Each is named after its paper,
# '<zotero name> - Supplementary N.pdf', which is how supplement_files() finds
# them again.

SUPP_DIR = "supplements"


def supplement_files(pub: dict, root: Path) -> list[Path]:
    """The supplementary PDFs filed for this publication, in order."""
    stem = zotero_name(pub)[:-4]
    d = root / SUPP_DIR
    if not d.is_dir():
        return []
    return sorted(d.glob(glob.escape(stem) + " - Supplementary *.pdf"),
                  key=lambda f: int(re.search(r"(\d+)\.pdf$", f.name).group(1)))


def supplement_sheets(pub: dict, root: Path) -> list[Path]:
    """The supplementary spreadsheets filed for this publication, by label."""
    d = root / SUPP_DIR
    if not d.is_dir():
        return []
    stem = zotero_name(pub)[:-4]
    return sorted(d.glob(glob.escape(stem) + " - Supplementary Table *.xlsx"),
                  key=lambda f: int(re.search(r"(\d+)\.xlsx$", f.name).group(1)))


# What a supplement link is labelled on a Springer Nature article page. Only
# these are taken: the Reporting Summary, the peer-review file and Source Data
# are supplementary files too, and none of them holds a results table.
SUPP_LABEL = re.compile(r"(?i)^supplementary (information|results|tables?|data|notes?|materials?)\b")


def supplement_links(doi: str) -> tuple[list[tuple[str, str]], str]:
    """[(label, pdf_url)] for a paper's supplementary PDFs, and a note.

    Springer Nature article pages list each supplement as a
    data-test="supp-info-link" anchor with its own label, which is what makes
    choosing among them possible. PNAS and OUP answer a scripted request with
    403, so those are reported rather than worked around.
    """
    if not doi.lower().startswith("10.1038/"):
        return [], "publisher not supported (only Springer Nature pages list supplements)"
    ok, body = fetch(f"https://doi.org/{doi}")
    if not ok:
        return [], f"landing page: {body}"
    html_text = body.decode("utf-8", "replace")
    out = []
    for a in re.finditer(r'<a [^>]*data-test="supp-info-link"[^>]*>', html_text):
        tag = a.group(0)
        label = re.search(r'data-track-label="([^"]*)"', tag)
        href = re.search(r'href="([^"]*)"', tag)
        if not (label and href):
            continue
        url = href.group(1)
        lab = label.group(1)
        # A SPREADSHEET counts too when it is labelled a table: DeepNovo-DIA's
        # Supplementary Tables 1-6 are .xlsx files, not pages of its PDF.
        if (url.lower().endswith(".pdf") and SUPP_LABEL.match(lab)) or \
                (url.lower().endswith(".xlsx") and re.match(r"(?i)^supplementary tables? \d", lab)):
            out.append((lab, "https:" + url if url.startswith("//") else url))
    return out, "" if out else "no supplementary PDF listed"


def cmd_supplements(args, conn, pubs, root):
    by_id = {p["id"]: p for p in pubs}
    ids = [int(x) for x in args.ids.split(",")]
    d = root / SUPP_DIR
    d.mkdir(exist_ok=True)
    for pid in ids:
        pub = by_id.get(pid)
        if not pub or not pub["doi"]:
            print(f"  p{pid}: no DOI")
            continue
        links, note = supplement_links(pub["doi"])
        if not links:
            print(f"  p{pid}: {note}")
            continue
        stem = zotero_name(pub)[:-4]
        n = 0
        for label, url in links:
            if url.lower().endswith(".xlsx"):
                # Named by its LABEL, which is how the paper cites it.
                dest = d / f"{stem} - {label.title()}.xlsx"
                magic = b"PK"
            else:
                n += 1
                dest = d / f"{stem} - Supplementary {n}.pdf"
                magic = b"%PDF"
            if dest.exists():
                print(f"  p{pid}: have {dest.name}")
                continue
            ok, body = fetch(url)
            if not ok or not body.startswith(magic):
                print(f"  p{pid}: {label}: {body if not ok else 'not the expected file type'}")
                continue
            dest.write_bytes(body)
            print(f"  p{pid}: {label} -> {dest.name} ({len(body) // 1024} KB)")
    return 0


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def candidates(cache: Path, pub: dict) -> tuple[list[tuple[str, str]], bool]:
    """[(source, url)] a source says is free, plus whether any source says so."""
    doi = (pub["doi"] or "").strip().lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    free = False

    def add(src, url):
        if url and url not in seen:
            seen.add(url)
            out.append((src, url))

    if doi.startswith("10.48550/arxiv."):
        # export.arxiv.org is the mirror arXiv asks automated clients to use.
        add("arxiv", f"https://export.arxiv.org/pdf/{doi.split('arxiv.', 1)[1]}")
        free = True
    if doi.startswith("10.1101/") or doi.startswith("10.64898/"):
        # bioRxiv moved its DOI prefix from 10.1101 to 10.64898. Ask which
        # versions exist rather than guessing v1, which 404s after a revision.
        d = get_json(cache, f"bx_{doi}",
                     f"https://api.biorxiv.org/details/biorxiv/{doi}")
        vers = sorted({int(c["version"]) for c in (d.get("collection") or [])
                       if c.get("version")}, reverse=True)
        for v in vers[:2] or [1]:
            add("biorxiv", f"https://www.biorxiv.org/content/{doi}v{v}.full.pdf")
        free = True

    oa: dict = {}
    # Europe PMC's verdict, when it has one, OVERRIDES OpenAlex below. It
    # reports whether the full text is available; OpenAlex reports whether a
    # deposit exists, which is not the same thing and is wrong in a way that
    # matters. Publication 360 is the worked example: OpenAlex says is_oa true,
    # oa_status green, oa_url PMC13457817, while that PMC record is EMBARGOED
    # until 2027-08-10 and Europe PMC says isOpenAccess N, inPMC N, hasPDF N,
    # no pmcid, "Subscription required". Of 27 papers this script called "open
    # access but blocked", 22 were that shape -- free on OpenAlex's word alone.
    epmc_verdict: bool | None = None
    if doi:
        # quote(), because the query needs literal double quotes around the DOI
        # and an unencoded `"` makes Europe PMC answer HTTP 400. The first
        # version of this script passed params through requests, which encoded
        # them; switching the transport to curl moved the URL into an f-string
        # and broke every Europe PMC lookup from then on -- silently, since a
        # failure is not cached, so it simply retried and failed again. The
        # answers already on disk predate the switch, which is why the breakage
        # only showed up on a newly added paper.
        ep = get_json(cache, f"epmc_{doi}",
                      "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                      "?format=json&resultType=core&query="
                      + quote(f'DOI:"{doi}"', safe=""))
        for res in (ep.get("resultList", {}) or {}).get("result", [])[:1]:
            oa_pdf = False
            for loc in (res.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []):
                if loc.get("availability") in ("Open access", "Free") \
                        and loc.get("documentStyle") == "pdf":
                    add("europepmc", loc.get("url"))
                    oa_pdf = True
            epmc_verdict = bool(
                res.get("isOpenAccess") == "Y" or res.get("inPMC") == "Y"
                or res.get("inEPMC") == "Y" or oa_pdf)
            if epmc_verdict:
                free = True
        oa = get_json(cache, f"oa_{doi}",
                      f"https://api.openalex.org/works/doi:{doi}?mailto={MAILTO}")
    else:
        q = re.sub(r"[^A-Za-z0-9 ]+", " ", pub["title"])[:180].strip()
        res = get_json(cache, f"oatitle_{norm(pub['title'])[:120]}",
                       "https://api.openalex.org/works?per-page=1&mailto="
                       + MAILTO + "&filter=title.search:" + quote(q, safe=""))
        hits = res.get("results") or []
        oa = hits[0] if hits else {}
    # Note the key: OpenAlex puts is_oa under open_access, not at the top level,
    # so `oa["is_oa"]` reads None for every work. But it only gets a say where
    # Europe PMC had no opinion -- see epmc_verdict above.
    if epmc_verdict is None and (oa.get("open_access") or {}).get("is_oa"):
        free = True
    best = oa.get("best_oa_location") or {}
    if best.get("pdf_url"):
        add("openalex-best", best["pdf_url"])
    for loc in oa.get("locations") or []:
        if loc.get("is_oa") and loc.get("pdf_url"):
            add("openalex", loc["pdf_url"])

    url = (pub["url"] or "").strip()
    if url.startswith("http"):
        low = url.lower()
        m = re.search(r"openreview\.net/forum\?id=([\w-]+)", low)
        if m:
            add("openreview", f"https://openreview.net/pdf?id={m.group(1)}")
        m = re.match(r"https?://proceedings\.mlr\.press/(v\d+)/([\w-]+)\.html", low)
        if m:
            add("mlr", f"https://proceedings.mlr.press/{m.group(1)}/{m.group(2)}"
                       f"/{m.group(2)}.pdf")
        m = re.match(r"(https?://papers\.nips\.cc/paper_files/paper/\d+)/hash/"
                     r"(\w+)-Abstract\.html", low)
        if m:
            # The landing page builds its PDF link in JS; the path is conventional.
            add("neurips", f"{m.group(1)}/file/{m.group(2)}-Paper.pdf")
        m = re.match(r"(https?://www\.biorxiv\.org/content/[\w./-]+v\d+)/?$", low)
        if m:
            add("biorxiv-landing", m.group(1) + ".full.pdf")
        # Try the url itself whatever it looks like: a DSpace bitstream ends in
        # /content and a Digital Commons download in /viewcontent.cgi, so an
        # extension test rejects both. The %PDF check is the real filter. This
        # is NOT evidence that the paper is free -- a paywalled article has a
        # landing page too -- so it does not set `free`.
        add("own-url", url)
        add("citation_pdf_url", pdf_from_landing(url))

    # A doi.org URL is not a PDF location, whatever OpenAlex says: the resolver
    # only redirects. 31 publications had doi.org as their ONLY offered "PDF",
    # so follow the DOI to wherever it lands and read that page's declaration.
    # Done last and only when nothing better turned up, because it costs an
    # extra request per paper.
    if doi and not any(src not in ("own-url",) and "doi.org" not in u
                       for src, u in out):
        add("doi-landing", pdf_from_landing(f"https://doi.org/{doi}"))
    return out, free


def pdf_from_landing(url: str) -> str | None:
    """The PDF a landing page declares, via citation_pdf_url.

    That tag is what Google Scholar requires, so DSpace, Digital Commons,
    figshare and most publishers emit it. Digital Commons prefixes it with
    bepress_, which is why scholarcommons.sc.edu looked tagless.
    """
    ok, body = fetch(url)
    if not ok or body.startswith(b"%PDF"):
        return None
    m = (re.search(rb'name="(?:bepress_)?citation_pdf_url"[^>]*content="([^"]+)"', body)
         or re.search(rb'content="([^"]+)"[^>]*name="(?:bepress_)?citation_pdf_url"', body))
    if not m:
        return None
    cand = m.group(1).decode("utf-8", "replace").replace("&amp;", "&")
    if cand.startswith("/"):
        base = re.match(r"(https?://[^/]+)", url)
        cand = (base.group(1) if base else "") + cand
    return cand


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_fetch(args, conn, pubs, root):
    cache = HERE / ".cache" / "pdfs"
    out = pdf_dir(root)
    have = coverage(pubs, root)
    todo = [p for p in pubs if p["id"] not in have]
    if args.ids:
        want = {int(i) for i in args.ids.split(",")}
        todo = [p for p in pubs if p["id"] in want]
    held = [p for p in todo if embargoed(p["id"])]
    if held:
        todo = [p for p in todo if not embargoed(p["id"])]
        for p in held:
            until, why = EMBARGOED[p["id"]]
            print(f"  skipping {p['id']}: embargoed until {until} ({why})")
    checked = [p for p in todo if p["id"] in CHECKED]
    if checked:
        todo = [p for p in todo if p["id"] not in CHECKED]
        print(f"  skipping {len(checked)} checked by hand: "
              + ", ".join(f"{p['id']} ({CHECKED[p['id']][0]})" for p in checked))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(have)} of {len(pubs)} already local, {len(todo)} to try", flush=True)
    rows = []
    for i, p in enumerate(todo, 1):
        cands, free = candidates(cache, p)
        status, src, detail, fname = "paywalled", "", "", ""
        if not cands:
            status = "no-record" if not (p["doi"] or "").strip() else "paywalled"
            detail = "nothing free reported"
        for source, url in cands:
            ok, body = fetch(url)
            if ok and body.startswith(b"%PDF"):
                dest = out / zotero_name(p)
                # NEVER OVERWRITE. A file of this name already belongs to some
                # publication; writing over it lost LIPNovo's preprint once.
                if dest.exists():
                    status, src, detail, fname = ("have", source, "name already taken; "
                                                  "not overwritten", dest.name)
                    break
                dest.write_bytes(body)
                status, src, detail, fname = ("have", source,
                                              f"{len(body)//1024} KB", dest.name)
                break
            why = body if not ok else f"not a PDF ({len(body)} B)"
            status = "blocked" if free else "paywalled"
            src, detail = source, f"{url} -> {why}"
        rows.append({"id": p["id"], "verdict": status, "year": str(p["publication_date"])[:4],
                     "type": p["publication_type"], "journal": p["journal"] or "",
                     "title": p["title"], "source": src, "doi": p["doi"] or "",
                     "url": p["url"] or "", "detail": detail, "file": fname})
        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  ({sum(r['verdict']=='have' for r in rows)} fetched)",
                  flush=True)
    merge_status(root, rows)
    for k, v in collections.Counter(r["verdict"] for r in rows).most_common():
        print(f"  {k:12s} {v}")


def merge_status(root: Path, rows: list[dict]) -> list[dict]:
    """Fold this run's outcomes into pdf_status.csv, newest wins.

    The column set is the UNION of what is on disk and what this run produced,
    with absent values blank. An earlier version wrote the new rows' keys as the
    header and then handed DictWriter the old rows too, which raised "dict
    contains fields not in fieldnames: 'source'" after the CSV's columns were
    revised -- losing a whole run's bookkeeping while the PDFs it had already
    downloaded stayed on disk.
    """
    path = root / "pdf_status.csv"
    have = {r["id"]: dict(r) for r in csv.DictReader(open(path))} \
        if path.exists() else {}
    for r in rows:
        have[str(r["id"])] = {k: str(v) for k, v in r.items()}
    fields: list[str] = []
    for r in have.values():
        for k in r:
            if k not in fields:
                fields.append(k)
    merged = sorted(have.values(), key=lambda r: (r.get("verdict", ""), int(r["id"])))
    # Written to a sibling temp file and moved into place. Writing in "w" mode
    # truncates BEFORE the rows go out, so the earlier version of this function
    # destroyed 312 of 323 rows when DictWriter raised partway through -- the
    # file was already empty by the time the exception fired, and the next run
    # merged into the remains. A crash must not be able to take the history.
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(merged)
    tmp.replace(path)
    return merged


def cmd_rename(args, conn, pubs, root):
    n = same = 0
    for f in pdfs(root):
        pub, _how = identify(pubs, f.stem, path=f)
        if not pub:
            print(f"  UNMATCHED {f.name}")
            continue
        want = zotero_name(pub)
        if want == f.name:
            same += 1
            continue
        print(f"  {f.name}\n    -> {want}")
        n += 1
        if args.apply:
            dest = f.with_name(want)
            if dest.exists():
                print("       SKIPPED: target exists")
            else:
                f.rename(dest)
    print(f"\n{n} to rename, {same} already correct"
          + ("" if args.apply else "   (dry run; pass --apply)"))


def cmd_ingest(args, conn, pubs, root):
    """File hand-downloaded PDFs, slicing a chapter out of a volume if asked."""
    src = Path(args.source)
    if not src.is_absolute():
        src = root / src
    by_hand, slices = {}, {}
    for spec in args.map or []:
        name, _, rest = spec.partition("=")
        pid, _, pages = rest.partition(":")
        by_hand[name] = int(pid)
        if pages:
            a, _, b = pages.partition("-")
            slices[name] = (int(a), int(b))
    filed = {f.name for f in pdfs(root)}
    n = 0
    for f in sorted(src.glob("*.pdf")):
        pub = next((p for p in pubs if p["id"] == by_hand.get(f.name)), None)
        how = "--map"
        if pub is None:
            text = subprocess.run(["pdftotext", "-f", "1", "-l", "2", str(f), "-"],
                                  capture_output=True, text=True).stdout
            pub, how = identify(pubs, f.stem, text)
        if pub is None:
            print(f"  ?? UNMATCHED {f.name}  (identify it with --map NAME=ID)")
            continue
        want = zotero_name(pub)
        if want in filed:
            print(f"  = already have {want[:64]}   ({f.name[:26]})")
            continue
        rng = slices.get(f.name)
        print(f"  -> {want[:70]}\n       from {f.name[:44]}  [{how}]"
              + (f" pages {rng[0]}-{rng[1]}" if rng else ""))
        n += 1
        if args.apply:
            dest = pdf_dir(root) / want
            if rng:
                # pypdf, not pdfseparate+pdfunite: the latter copies the
                # volume's shared fonts onto every page and turned a 9-page
                # slice of a 31 MB book into 32.7 MB. This gives 0.3 MB.
                try:
                    from pypdf import PdfReader, PdfWriter
                except ImportError:
                    sys.exit("slicing needs pypdf: rerun with "
                             "`uv run --with pypdf python3 scripts/build_pdf_library.py ...`")
                r, w = PdfReader(str(f)), PdfWriter()
                for i in range(rng[0] - 1, rng[1]):
                    w.add_page(r.pages[i])
                w.compress_identical_objects()
                with open(dest, "wb") as fh:
                    w.write(fh)
            else:
                shutil.copy2(f, dest)
            filed.add(want)
    print(f"\n{n} to file" + ("" if args.apply else "   (dry run; pass --apply)"))


def cmd_dedupe(args, conn, pubs, root):
    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    seen: dict[str, Path] = {}
    freed = n = 0
    for p in pdfs(root):
        d = h(p)
        why = ("identical to " + seen[d].name) if d in seen else None
        if why:
            print(f"  {p.name[:60]}  ({why[:44]})")
            n += 1
            freed += p.stat().st_size
            if args.apply:
                p.unlink()
        else:
            seen[d] = p
    print(f"\n{n} byte-identical duplicate(s), {freed/1e6:.0f} MB"
          + ("" if args.apply else "   (dry run; pass --apply)"))

    # Byte equality is too strict for "the same paper twice". bioRxiv served the
    # same preprint to two fetches 26 bytes apart -- a timestamp inside the PDF
    # -- so two copies of Sanders 2024 and two of pi-PrimeNovo 2024 sat in the
    # library with different hashes, and `rename` could only report them as a
    # rename it could never apply, because each pair's other name was taken.
    # Grouping by the publication each file resolves to catches that. NOT
    # auto-deleted: a preprint and its version of record legitimately give one
    # paper two files under two rows, and deciding which of two copies of ONE
    # row to keep is a judgement a human should make.
    same_pub = collections.defaultdict(list)
    for pid, files in coverage(pubs, root).items():
        if len(files) > 1:
            same_pub[pid] = files
    if same_pub:
        print(f"\n{len(same_pub)} publication(s) with more than one file, "
              "same paper rather than same bytes:")
        for pid, files in sorted(same_pub.items()):
            print(f"  publication {pid}")
            for f in files:
                print(f"    {f.stat().st_size/1e6:7.2f} MB  {f.name[:88]}")


REPORT_BUCKETS = """\
paywalled.txt
    No source reports any free full text: neither OpenAlex nor Europe PMC.
    Includes the papers checked by hand and confirmed paywalled (CHECKED).

blocked-but-open-in-pmc.txt
    Open access with a PMC copy, listed as a PMC link. START HERE: every
    scripted route into PMC is shut by design, but the link opens normally and
    the PDF is one click away.

blocked-publisher.txt
    Reported free, and the publisher refuses a scripted request anyway. Bot
    protection, not a paywall; these open normally in a browser.

blocked-openreview.txt
    OpenReview answers 403 to some networks for both its PDF endpoint and its
    API, whatever user agent is used.

no-doi-and-not-indexed.txt
    No DOI and no index entry. Mostly records that are not papers at all.

no-pdf-exists.txt
    Checked by hand: no full text exists or ever will (a code deposit, an
    abstract-only record). Nothing to find. See CHECKED in build_pdf_library.py.

embargoed.txt
    NOT retrievable yet, by the publisher's own statement, with a release date
    in the future. Neither paywalled nor blocked: the full text is not public.
    `fetch` skips these, and the entry expires on its date by itself.

covered-by-other-version.txt
    NOT missing. One half of a preprint / version-of-record pair whose single
    PDF is filed under the other half.
"""


def cmd_report(args, conn, pubs, root):
    have = coverage(pubs, root)
    status = {int(r["id"]): r for r in
              csv.DictReader(open(root / "pdf_status.csv"))} \
        if (root / "pdf_status.csv").exists() else {}
    versions = {}
    for a, b in conn.execute("SELECT preprint_id, published_id FROM publication_version"):
        versions[a] = b
        versions[b] = a
    out = root / "missing"
    out.mkdir(exist_ok=True)

    def link(p):
        return (f"https://doi.org/{p['doi']}" if (p["doi"] or "").strip()
                else (p["url"] or "").strip() or None)

    cache = HERE / ".cache" / "pdfs"

    def pmcid(p):
        """This paper's PMC id, from any cached answer that names one.

        Read out of the RAW cached JSON rather than from a single field. The
        first version of this asked Europe PMC for `pmcid` and required
        `isOpenAccess == "Y"`, and missed publications 86, 91, 95 and 120 --
        all four of which Europe PMC had already handed us an "Open access"
        PDF location for, with the PMC id sitting in the URL. They were filed
        as merely publisher-blocked, which is the harder bucket, when a PMC
        link would have opened.

        Only called for rows a source already reported as free, so a PMC id
        here means a readable copy rather than just a record.
        """
        doi = (p["doi"] or "").strip().lower()
        blobs = []
        for key in ((f"epmc_{doi}", f"oa_{doi}") if doi
                    else (f"oatitle_{norm(p['title'])[:120]}",)):
            f = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", key)[:180] + ".json")
            if f.exists():
                blobs.append(f.read_text())
        # A PMC id is not a readable copy. Publication 360's embargoed deposit
        # has one, and grepping the raw JSON for it put the paper on the
        # "open in PMC" list a year before it opens. Require Europe PMC to say
        # the full text is actually there.
        for blob in blobs:
            try:
                d = json.loads(blob)
            except ValueError:
                continue
            for res in (d.get("resultList", {}) or {}).get("result", [])[:1]:
                if res.get("pmcid") and (res.get("inPMC") == "Y"
                                         or res.get("inEPMC") == "Y"
                                         or res.get("isOpenAccess") == "Y"):
                    return res["pmcid"]
                for loc in (res.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []):
                    if loc.get("availability") in ("Open access", "Free") \
                            and loc.get("documentStyle") == "pdf":
                        hit = re.search(r"PMC\d{4,}", loc.get("url") or "")
                        if hit:
                            return hit.group(0)
        return None

    def pmc_url(p):
        pid = pmcid(p)
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pid}/" if pid else None

    buckets = collections.defaultdict(list)
    for p in pubs:
        if p["id"] in have:
            continue
        r = status.get(p["id"])
        host = re.match(r"https?://([^/]+)", (r or {}).get("detail") or "")
        host = host.group(1) if host else "-"
        if p["id"] in CHECKED:
            # A person's verdict outranks any stored or inferred one.
            key = {"no-pdf": "no-pdf-exists", "paywalled": "paywalled"}[CHECKED[p["id"]][0]]
        elif embargoed(p["id"]):
            # Checked BEFORE the stored verdict, because a previous run
            # recorded this as paywalled, which is the wrong word: the text is
            # not for sale either.
            key = "embargoed"
        elif r is None or r["verdict"] == "have" or versions.get(p["id"]) in have:
            key = "covered-by-other-version"
        elif "openreview" in host:
            key = "blocked-openreview"
        elif r["verdict"] == "no-record":
            key = "no-doi-and-not-indexed"
        elif r["verdict"] == "paywalled":
            key = "paywalled"
        elif pmcid(p):
            # An open-access PMC copy exists, and a PMC link is one click from
            # the PDF in a browser even though every scripted route in is shut.
            key = "blocked-but-open-in-pmc"
        else:
            key = "blocked-publisher"
        # NOT bucketed on the last host tried. That produced a
        # "blocked-doi-resolver" list of 31 which this file then recommended as
        # "the largest recoverable group", on the theory that OpenAlex had
        # offered doi.org as their PDF. It had not: doi.org was simply the
        # record's own url, tried LAST after the real open-access candidates
        # failed. Following the DOI and reading the landing page's
        # citation_pdf_url recovered 0 of the 31 -- ACS, OUP and MDPI answer
        # 403 at the landing page itself, and Wiley and Elsevier's linkinghub
        # answer 200 with no such tag.
        buckets[key].append((p, pmc_url(p) or link(p)))

    for f in out.glob("*.txt"):
        f.unlink()
    total = 0
    counts = []
    for name, items in sorted(buckets.items()):
        items.sort(key=lambda t: (t[0]["journal"] or "", t[0]["id"]))
        # One URL per line and nothing else, so a file pastes straight into a
        # browser, a download manager or `xargs -n1 curl -LO`.
        (out / f"{name}.txt").write_text(
            "\n".join(u for _p, u in items if u) + "\n")
        total += len(items)
        counts.append(f"{len(items):5d}  {name}.txt")
    (out / "README.txt").write_text(
        "Papers in the catalog with no PDF in this folder\n"
        "================================================\n\n"
        "One URL per line, nothing else. A line is https://doi.org/<doi> where\n"
        "the record has a DOI, and the record's own landing page where it does\n"
        "not. Per-paper detail is in ../pdf_status.csv.\n\n"
        f"{len(have)} of {len(pubs)} publications have a PDF here; the {total} "
        "lines below are the rest.\n\n" + "\n".join(counts) + "\n\n"
        + REPORT_BUCKETS)
    print(f"{len(have)} of {len(pubs)} covered ({len(have)/len(pubs):.0%})")
    print("\n".join(counts))
    print(f"{total:5d}  total")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                    help=f"library root (default {DEFAULT_DIR})")
    ap.add_argument("--browser-ua", action="store_true",
                    help="send a desktop browser User-Agent. For open-access "
                         "papers whose publisher refuses the default agent; it "
                         "gets past bot detection, never a paywall")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
            ("report", cmd_report, "write missing/*.txt and print coverage"),
            ("fetch", cmd_fetch, "download everything a source says is free"),
            ("rename", cmd_rename, "re-derive every filename from the catalog"),
            ("dedupe", cmd_dedupe, "drop byte-identical copies"),
            ("ingest", cmd_ingest, "file hand-downloaded PDFs from a folder"),
            ("supplements", cmd_supplements,
             "fetch the supplementary PDFs a publisher lists for these papers")):
        p = sub.add_parser(name, help=helptext)
        p.set_defaults(fn=fn)
        if name in ("rename", "dedupe", "ingest"):
            p.add_argument("--apply", action="store_true",
                           help="actually change files (default: dry run)")
        if name == "supplements":
            p.add_argument("--ids", required=True, help="comma-separated publication ids")
        if name == "fetch":
            p.add_argument("--ids", help="comma-separated publication ids")
            p.add_argument("--limit", type=int)
        if name == "ingest":
            p.add_argument("source", help="folder of hand-downloaded PDFs")
            p.add_argument("--map", action="append", metavar="FILE=ID[:FIRST-LAST]",
                           help="identify a file by hand, optionally slicing "
                                "those PDF pages out of a larger volume")
    args = ap.parse_args()
    global _ua
    if args.browser_ua:
        _ua = BROWSER_UA
    root = args.dir
    if not root.is_dir():
        sys.exit(f"no library at {root}")
    conn = sqlite3.connect(DB_PATH)
    pubs = load_publications(conn)
    rc = args.fn(args, conn, pubs, root)
    conn.close()
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())

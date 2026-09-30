#!/usr/bin/env python3
"""Keep a local folder of paper PDFs in step with the catalog.

The catalog says which papers exist; this says which ones you have a copy of,
fetches the ones that are legally free, names every file the same way, and lists
what is left so a human can go and get it.

OFFLINE AND LOCAL, and the only script here that is both. It touches a folder
OUTSIDE the repository (`--dir`, default ~/Documents/De novo peptide sequencing),
fetches a few hundred URLs, and writes nothing into denovo.db. It must never run
in CI: there is no library there to update.

    python3 build_pdf_library.py report                 # what is missing, and why
    python3 build_pdf_library.py fetch                  # download what is free
    python3 build_pdf_library.py rename                 # re-derive every filename
    uv run --with pypdf python3 build_pdf_library.py \\
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
import csv
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

from rapidfuzz import fuzz

HERE = Path(__file__).parent
DB_PATH = HERE / "denovo.db"
DEFAULT_DIR = Path.home() / "Documents" / "De novo peptide sequencing"
MAILTO = "j.vangoey@instadeep.com"          # same contact the other builders send
UA = ("awesome_de_novo_peptide_sequencing/1.0 "
      "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
      f"mailto:{MAILTO})")
MIN_GAP = 1.5                                # seconds between hits on one host
_last: dict[str, float] = collections.defaultdict(float)


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
                ["curl", "-sS", "-L", "--max-time", "90", "-A", UA,
                 "-H", "Accept: application/json" if want_json
                       else "Accept: application/pdf,*/*",
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
    def go():
        ok, body = fetch(url, want_json=True)
        if not ok:
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
    return rows


def zotero_name(p: dict) -> str:
    """"Surname et al. - 2024 - Title.pdf", the convention the folder already uses."""
    first = (p["first_author"] or "Unknown").split()[-1]
    n = p["n_authors"] or 1
    if n > 2:
        tag = f"{first} et al."
    elif n == 2:
        tag = f"{first} and {(p['second_author'] or '').split()[-1]}"
    else:
        tag = first
    title = re.sub(r"[^\w áàâäéèêëíìîïóòôöúùûüçñšžπ'()+-]", " ", p["title"])
    title = re.sub(r"\s+", " ", title).strip()[:95]
    return f"{tag} - {str(p['publication_date'])[:4]} - {title}.pdf".replace("/", "-")


def identify(pubs: list[dict], stem: str, text: str = "") -> tuple[dict | None, str]:
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
    if year and len(top) > 1:
        # A preprint and its version of record share a title; the year decides.
        top = [(s, p) for s, p in top
               if str(p["publication_date"])[:4] == year] or top
    if len(top) > 1:
        # Still tied, which happens when the two rows differ ONLY in the
        # title's capitalisation and carry the same year: publication 108's
        # "NovoBoard: a comprehensive framework" against publication 80's
        # "A Comprehensive Framework", or 14 against 122. Normalising for the
        # comparison erases exactly the difference, so the pick was arbitrary
        # and `rename` then wanted to recase a file it had just accepted,
        # every run, forever. A file already named for one of the candidates
        # IS that candidate: prefer it, which makes rename idempotent.
        named = [(s, p) for s, p in top if zotero_name(p) == stem + ".pdf"]
        top = named or top
    return (top[0][1], f"title {top[0][0]:.0f}") if top else (None, "")


def coverage(pubs: list[dict], root: Path) -> dict[int, list[Path]]:
    """publication id -> the local files that are it. The FOLDER is the truth."""
    out: dict[int, list[Path]] = collections.defaultdict(list)
    for f in sorted(list(root.glob("*.pdf")) + list((root / "retrieved").glob("*.pdf"))):
        pub, _how = identify(pubs, f.stem)
        if pub:
            out[pub["id"]].append(f)
    return out


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
    if doi:
        ep = get_json(cache, f"epmc_{doi}",
                      "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                      f'query=DOI:"{doi}"&format=json&resultType=core')
        for res in (ep.get("resultList", {}) or {}).get("result", [])[:1]:
            if res.get("isOpenAccess") == "Y":
                free = True
            for loc in (res.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []):
                if loc.get("availability") in ("Open access", "Free") \
                        and loc.get("documentStyle") == "pdf":
                    add("europepmc", loc.get("url"))
                    free = True
        oa = get_json(cache, f"oa_{doi}",
                      f"https://api.openalex.org/works/doi:{doi}?mailto={MAILTO}")
    else:
        q = re.sub(r"[^A-Za-z0-9 ]+", " ", pub["title"])[:180].strip()
        res = get_json(cache, f"oatitle_{norm(pub['title'])[:120]}",
                       "https://api.openalex.org/works?per-page=1&mailto="
                       + MAILTO + "&filter=title.search:"
                       + subprocess.list2cmdline([q]).strip('"').replace(" ", "%20"))
        hits = res.get("results") or []
        oa = hits[0] if hits else {}
    if oa.get("is_oa"):
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
    out = root / "retrieved"
    out.mkdir(parents=True, exist_ok=True)
    have = coverage(pubs, root)
    todo = [p for p in pubs if p["id"] not in have]
    if args.ids:
        want = {int(i) for i in args.ids.split(",")}
        todo = [p for p in pubs if p["id"] in want]
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
    for f in sorted((root / "retrieved").glob("*.pdf")):
        pub, _how = identify(pubs, f.stem)
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
    filed = {f.name for f in (root / "retrieved").glob("*.pdf")} \
        | {f.name for f in root.glob("*.pdf")}
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
            dest = root / "retrieved" / want
            if rng:
                # pypdf, not pdfseparate+pdfunite: the latter copies the
                # volume's shared fonts onto every page and turned a 9-page
                # slice of a 31 MB book into 32.7 MB. This gives 0.3 MB.
                try:
                    from pypdf import PdfReader, PdfWriter
                except ImportError:
                    sys.exit("slicing needs pypdf: rerun with "
                             "`uv run --with pypdf python3 build_pdf_library.py ...`")
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
    upstairs = {h(p) for p in root.glob("*.pdf")}
    seen: dict[str, Path] = {}
    freed = n = 0
    for p in sorted((root / "retrieved").glob("*.pdf")):
        d = h(p)
        why = ("identical to " + seen[d].name) if d in seen else \
              ("already in the parent folder" if d in upstairs else None)
        if why:
            print(f"  {p.name[:60]}  ({why[:44]})")
            n += 1
            freed += p.stat().st_size
            if args.apply:
                p.unlink()
        else:
            seen[d] = p
    print(f"\n{n} duplicate(s), {freed/1e6:.0f} MB"
          + ("" if args.apply else "   (dry run; pass --apply)"))


REPORT_BUCKETS = """\
paywalled.txt
    No source reports any free full text: neither OpenAlex nor Europe PMC.

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
        """An open-access PMC id for this paper, from the cached Europe PMC answer."""
        doi = (p["doi"] or "").strip().lower()
        if not doi:
            return None
        f = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", f"epmc_{doi}")[:180] + ".json")
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
        except ValueError:
            return None
        for res in (d.get("resultList", {}) or {}).get("result", [])[:1]:
            if res.get("pmcid") and res.get("isOpenAccess") == "Y":
                return res["pmcid"]
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
        if r is None or r["verdict"] == "have" or versions.get(p["id"]) in have:
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
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
            ("report", cmd_report, "write missing/*.txt and print coverage"),
            ("fetch", cmd_fetch, "download everything a source says is free"),
            ("rename", cmd_rename, "re-derive every filename from the catalog"),
            ("dedupe", cmd_dedupe, "drop byte-identical copies"),
            ("ingest", cmd_ingest, "file hand-downloaded PDFs from a folder")):
        p = sub.add_parser(name, help=helptext)
        p.set_defaults(fn=fn)
        if name in ("rename", "dedupe", "ingest"):
            p.add_argument("--apply", action="store_true",
                           help="actually change files (default: dry run)")
        if name == "fetch":
            p.add_argument("--ids", help="comma-separated publication ids")
            p.add_argument("--limit", type=int)
        if name == "ingest":
            p.add_argument("source", help="folder of hand-downloaded PDFs")
            p.add_argument("--map", action="append", metavar="FILE=ID[:FIRST-LAST]",
                           help="identify a file by hand, optionally slicing "
                                "those PDF pages out of a larger volume")
    args = ap.parse_args()
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

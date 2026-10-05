#!/usr/bin/env python3
"""Search GitHub and GitLab for the code of methods that have no repository.

build_repository_candidates.py reads the URLs a method's own PDF prints, which
is the strongest evidence there is but reaches only the methods with a local
PDF that bothered to print one. This asks the forges directly, for every
method still lacking an `algorithm_repository` row:

  GitHub   repository search by the method's name (and aliases), and by the
           title of the paper that describes it, in READMEs
  GitLab   project search by the method's name

A hit is NEVER accepted on its name. "ReNovo" or "DeepNovo" is a name anyone
can give a repository, and a forge search returns forks, course projects and
reimplementations by the dozen. So every hit's README is fetched and scored
on evidence that ties it to the PAPER:

  doi       the README cites a DOI of a paper that describes the method
  arxiv     ... or its arXiv id
  title     ... or the paper's title (first 60 normalised characters)
  author    the repository owner's login or display name contains BOTH the
            surname and the given name (or, for a surname of five letters
            or more, the initial) of one of the describing paper's authors.
            A surname alone is no evidence with Yang, Li and Wang this common
  name      the repository is named after the method
  domain    the README talks about peptides and mass spectrometry

and given a verdict:

  strong    the README cites the paper (doi, arxiv or title) AND the
            repository is named after the method or owned by an author
  probable  named after the method, in the domain, and owned by an author
  cites     cites the paper and nothing more: a reimplementation, a course
            project, a reading list
  weak      anything else, or a fork, or an aggregator (a README citing five
            or more papers, or a name like awesome-* or *-papers): written to
            the CSV for completeness, never trusted

The aggregator rule exists because the first trial run called two "strong"
on a citation alone: an Awesome list and a paper-breakdown blog, each of
which cites hundreds of papers by DOI and title.

REPORT-ONLY, like every proposer here: it writes repository_search.csv and
never touches denovo.db. Accepting a repository is a judgement, and a forked
copy of the right code is still the wrong URL to record.

    uv run python scripts/build_repository_search.py
    uv run python scripts/build_repository_search.py --ids 512,513
    uv run python scripts/build_repository_search.py --kinds algorithm,post-processor

Transport is the `gh` CLI for GitHub (authenticated, 30 searches a minute) and
curl for GitLab, for the same reason build_pdf_library.py uses curl: Python's
TLS stack rejects this machine's gateway certificates. Responses are cached in
.cache/reposearch/, so a re-run costs nothing for methods already searched.
Never in CI: ~700 searches pace out to about half an hour.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import unicodedata
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "repository_search.csv"
CACHE = HERE / ".cache" / "reposearch"
DEFAULT_KINDS = "algorithm,post-processor,benchmark,adjacent"
PER_QUERY = 5          # hits examined per search
SEARCH_GAP_S = 2.2     # GitHub allows 30 searches a minute

AGGREGATOR_NAME = re.compile(r"awesome|papers?\b|paper-list|reading|survey|review|breakdown", re.I)
DOI_ANY = re.compile(r"10\.\d{4,9}/[^\s)\]>\"']+")
ARXIV_ANY = re.compile(r"\b\d{4}\.\d{4,5}\b")
# Hits a person read and turned down, keyed by repository, with the reason.
# They are dropped from the report so a re-run proposes only what is new; a
# method that GAINS a repository drops out by itself, because only methods
# without one are searched. Add to this, the way TOOL_ALIASES in
# build_benchmarks.py records an identification: one line of reasoning each.
REJECTED = {
    "github.com/jmchilton/pepnovo": "a third party's local changes to PepNovo, not its authors' code",
    "github.com/anonms2/plmnovo": "anonymous review mirror; navid-naderi/PLMNovo is the authors' copy",
    "github.com/jumpsuite/jumpt": "JUMPt is the Peng lab's protein-turnover tool, not the JUMP sequencer",
    "github.com/surendhar-bioinformatics/jumpt": "a copy of JUMPt, not JUMP",
    "github.com/abhijitju06/jumpt-version-1.1.0": "a JUMPt release, not JUMP",
    "github.com/abhijitju06/jumpt-version-1.1.1-": "a JUMPt release, not JUMP",
    "github.com/abhijitju06/jumpt-version-1.2.0-python-edition-": "a JUMPt release, not JUMP",
    "github.com/yangshu729/buaa-biatnovo": "the same author's development repository; biatNovo-DDA is the release",
    "github.com/biocc/sp-megd_fusion": "XA-Novo's repository; nothing ties it to the Fusion assembler paper",
}
DOMAIN = re.compile(r"\b(peptide|proteom|mass spectr|ms/ms|tandem|spectra|"
                    r"de novo|denovo|mgf|mzml)", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").replace("π", "pi")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def cached(key: str, fetch):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if path.exists():
        return json.loads(path.read_text())
    value = fetch()
    if value is not None:   # a failure is not cached, so a re-run retries it
        path.write_text(json.dumps(value))
    return value


def gh(path: str, raw: bool = False):
    cmd = ["gh", "api", path]
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw"]
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout if raw else json.loads(r.stdout)
        if "Not Found" in r.stderr or "404" in r.stderr:
            return "" if raw else {}
        if "rate limit" in r.stderr.lower() or "403" in r.stderr:
            time.sleep(30 * (attempt + 1))
            continue
        print(f"  gh api {path}: {r.stderr.strip()[:120]}")
        return None
    return None


_last_search = [0.0]


def gh_search(q: str) -> list[dict]:
    def fetch():
        wait = SEARCH_GAP_S - (time.time() - _last_search[0])
        if wait > 0:
            time.sleep(wait)
        _last_search[0] = time.time()
        res = gh(f"search/repositories?q={urllib.parse.quote(q)}&per_page={PER_QUERY}")
        if res is None:
            return None
        return [{"url": it["html_url"], "full_name": it["full_name"], "name": it["name"],
                 "owner": it["owner"]["login"], "fork": it["fork"],
                 "description": it.get("description") or "", "stars": it["stargazers_count"]}
                for it in res.get("items", [])]
    return cached("gh-search:" + q, fetch) or []


def gh_readme(full_name: str) -> str:
    return cached("gh-readme:" + full_name, lambda: gh(f"repos/{full_name}/readme", raw=True)) or ""


def gh_owner_name(login: str) -> str:
    res = cached("gh-user:" + login, lambda: gh(f"users/{login}"))
    return (res or {}).get("name") or ""


def curl_json(url: str):
    r = subprocess.run(["curl", "-s", "-f", "--max-time", "30", url], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  curl {url[:90]}: exit {r.returncode}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def gitlab_search(name: str) -> list[dict]:
    def fetch():
        res = curl_json("https://gitlab.com/api/v4/projects?search="
                        f"{urllib.parse.quote(name)}&per_page={PER_QUERY}&order_by=star_count")
        if res is None:
            return None
        return [{"url": p["web_url"], "full_name": p["path_with_namespace"], "name": p["path"],
                 "owner": p["namespace"]["path"], "fork": bool(p.get("forked_from_project")),
                 "description": p.get("description") or "", "stars": p.get("star_count", 0),
                 "id": p["id"], "branch": p.get("default_branch") or "main"}
                for p in res]
    return cached("gl-search:" + name, fetch) or []


def gitlab_readme(hit: dict) -> str:
    def fetch():
        for fname in ("README.md", "README.rst", "README", "readme.md"):
            r = subprocess.run(
                ["curl", "-s", "-f", "--max-time", "30",
                 f"https://gitlab.com/api/v4/projects/{hit['id']}/repository/files/"
                 f"{urllib.parse.quote(fname, safe='')}/raw?ref={urllib.parse.quote(hit['branch'])}"],
                capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout
        return ""
    return cached("gl-readme:" + hit["full_name"], fetch) or ""


def evidence(hit: dict, readme: str, method: dict) -> tuple[list[str], str]:
    text = (readme or "") + " " + hit["description"]
    low = text.lower()
    ntext = norm(text)
    ev = []
    for doi in method["dois"]:
        if doi.lower() in low:
            ev.append("doi")
            break
    for ax in method["arxiv"]:
        if ax in low:
            ev.append("arxiv")
            break
    for t in method["titles"]:
        nt = norm(t)[:60]
        if len(nt) >= 30 and nt in ntext:
            ev.append("title")
            break
    owner = norm(hit["owner"] + " " + method["owner_names"].get(hit["owner"], ""))
    if any(sur in owner and (giv in owner or (len(sur) >= 5 and owner.startswith(giv[:1]) )
                             if giv else False)
           for giv, sur in method["authors"] if len(sur) >= 2):
        ev.append("author")
    if any(len(n) >= 3 and (norm(hit["name"]) == n or norm(hit["name"]).startswith(n))
           for n in method["names"]):
        ev.append("name")
    if DOMAIN.search(text):
        ev.append("domain")
    cites = bool({"doi", "arxiv", "title"} & set(ev))
    n_cited = len(set(DOI_ANY.findall(low))) + len(set(ARXIV_ANY.findall(low)))
    if n_cited >= 5 or AGGREGATOR_NAME.search(hit["name"]):
        ev.append(f"aggregator({n_cited} cited)")
        return ev, "weak"
    if hit["fork"]:
        ev.append("fork")   # a fork of the right code is still the wrong URL
        return ev, "weak"
    if cites and ({"name", "author"} & set(ev)):
        verdict = "strong"
    elif {"name", "domain", "author"} <= set(ev):
        verdict = "probable"
    elif cites:
        verdict = "cites"
    else:
        verdict = "weak"
    return ev, verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated algorithm ids to restrict to")
    ap.add_argument("--kinds", default=DEFAULT_KINDS,
                    help=f"algorithm kinds to search (default {DEFAULT_KINDS})")
    args = ap.parse_args()

    con = sqlite3.connect(HERE / "denovo.db")
    con.row_factory = sqlite3.Row
    kinds = [k.strip() for k in args.kinds.split(",")]
    owned = {re.sub(r"^https?://(www\.)?", "", r[0].lower()).rstrip("/"): r[1] for r in con.execute(
        "SELECT r.url, a.name FROM algorithm_repository r JOIN algorithm a ON a.id = r.algorithm_id")}
    algs = con.execute(
        f"SELECT a.id, a.name, a.aliases, a.kind FROM algorithm a "
        f"WHERE NOT EXISTS (SELECT 1 FROM algorithm_repository r WHERE r.algorithm_id = a.id) "
        f"AND a.kind IN ({','.join('?' * len(kinds))}) ORDER BY a.id", kinds).fetchall()
    if args.ids:
        want = {int(i) for i in args.ids.split(",")}
        algs = [a for a in algs if a["id"] in want]

    rows = []
    for i, a in enumerate(algs, 1):
        pubs = con.execute(
            "SELECT p.id, p.title, p.doi, p.url FROM publication p "
            "JOIN publication_algorithm pa ON pa.publication_id = p.id "
            "WHERE pa.algorithm_id = ? AND pa.role = 'describes'", (a["id"],)).fetchall()
        authors = {(norm(r[0].split()[0]), norm(r[0].split()[-1])) for r in con.execute(
            "SELECT au.name FROM author au JOIN publication_author pa ON pa.author_id = au.id "
            "JOIN publication_algorithm pl ON pl.publication_id = pa.publication_id "
            "WHERE pl.algorithm_id = ? AND pl.role = 'describes'", (a["id"],)) if r[0].split()}
        names = [a["name"]] + [x.strip() for x in (a["aliases"] or "").split(",") if x.strip()]
        method = {
            "names": {norm(n) for n in names if norm(n)},
            "dois": [p["doi"] for p in pubs if p["doi"]],
            "arxiv": [m.group(1) for p in pubs
                      for m in [re.search(r"(\d{4}\.\d{4,5})", (p["doi"] or "") + " " + (p["url"] or ""))] if m],
            "titles": [re.sub(r"<[^>]+>", "", p["title"]) for p in pubs],
            "authors": {a_ for a_ in authors if a_[0] != a_[1]},
            "owner_names": {},
        }
        print(f"[{i}/{len(algs)}] {a['name']}", flush=True)
        hits: dict[str, dict] = {}
        # A one- or two-letter name searches everything; only the title query
        # is worth running for those.
        queries = [f'"{n}" in:name,description,readme' for n in names if len(norm(n)) >= 4]
        for t in method["titles"][:1]:
            words = re.sub(r"[^\w\s-]", " ", t).split()
            if len(words) >= 4:
                queries.append(f'"{" ".join(words[:8])}" in:readme')
        for q in queries:
            for h in gh_search(q):
                hits.setdefault(h["full_name"].lower(), {**h, "host": "github", "query": q})
        for n in names:
            if len(norm(n)) >= 4:
                for h in gitlab_search(n):
                    hits.setdefault("gitlab:" + h["full_name"].lower(), {**h, "host": "gitlab", "query": n})
        for key, h in hits.items():
            if h["host"] == "github":
                readme = gh_readme(h["full_name"])
                method["owner_names"][h["owner"]] = gh_owner_name(h["owner"])
            else:
                readme = gitlab_readme(h)
            ev, verdict = evidence(h, readme, method)
            url_key = re.sub(r"^https?://(www\.)?", "", h["url"].lower()).rstrip("/")
            if url_key in REJECTED:
                continue
            rows.append(dict(
                alg_id=a["id"], method=a["name"], kind=a["kind"], verdict=verdict,
                evidence=" ".join(ev), url=h["url"], host=h["host"], stars=h["stars"],
                already_repo_of=owned.get(url_key, ""), description=h["description"][:200],
                query=h["query"]))

    order = {"strong": 0, "probable": 1, "cites": 2, "weak": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], r["alg_id"], -int(r["stars"] or 0)))
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["alg_id"])
        w.writeheader()
        w.writerows(rows)
    by = {v: {r["alg_id"] for r in rows if r["verdict"] == v} for v in order}
    print(f"{len(algs)} methods searched, {len(rows)} repositories examined")
    print(f"strong: {len(by['strong'])} methods; probable: {len(by['probable'] - by['strong'])} more; "
          f"cites only: {len(by['cites'] - by['strong'] - by['probable'])}")
    print(f"-> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

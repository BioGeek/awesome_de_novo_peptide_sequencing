#!/usr/bin/env python3
"""Build the publication_citation graph from Crossref + Semantic Scholar.

Run offline (not in CI). Re-execute when new papers are added or to refresh
citation counts. Idempotent: INSERT OR IGNORE keeps existing edges.

Outputs:
  - rows inserted into `publication_citation` in denovo.db
  - citation_audit.csv next to this script: every fuzzy-matched edge with the
    title pair and similarity score, for human review.

References resolved by DOI (primary, exact) or title (secondary), under the
rules in titles_match() plus a date check (YEAR_SLACK). An edge this run's own
reading REFUTES -- the token-set prefilter proposed it and the verdict rejects
it, and no other reference supports it -- is deleted and
logged to citation_refused.csv. An edge merely not re-found (a flaky API) is
kept, so a bad network day cannot thin the graph.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

DB_PATH    = Path(__file__).resolve().parent.parent / "denovo.db"
AUDIT_PATH = Path(__file__).resolve().parent.parent / "citation_audit.csv"
REFUSED_PATH = Path(__file__).resolve().parent.parent / "citation_refused.csv"

USER_AGENT     = "awesome-de-novo-peptide-sequencing/0.1 (https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; mailto:j.vangoey@instadeep.com)"
CROSSREF_BASE  = "https://api.crossref.org/works"
SS_BASE        = "https://api.semanticscholar.org/graph/v1/paper"

FUZZY_THRESHOLD = 92  # token-set ratio: the candidate PREFILTER, never the verdict
MIN_FUZZY_WORDS = 5   # a shorter catalog title matches only exactly
# A title match to a paper dated more than this many years after the year the
# reference itself prints is refused: "De novo sequencing of peptides by mass
# spectrometry" (Artemenko 2006) scores 94 against a 2009 encyclopedia entry
# titled "...using mass spectrometry", and no word-level rule tells that
# substitution from SPIDER's references saying "containing" for "with". Two
# years absorbs online-first dates and a preprint cited before its journal row.
YEAR_SLACK = 2
# A notice about a paper ("Correction to: Introducing pi-HelixNovo...") names it.
NOTICE_RE = re.compile(r"^(?:correction|erratum|corrigendum|retraction)(?: notice)?(?: to| of)? ")
CROSSREF_DELAY  = 0.2  # seconds between requests; "polite pool" is ~50 req/sec
SS_DELAY        = 1.5  # seconds; unauthenticated public-API limit ≈ 100/5min

# Titles whose Semantic Scholar search returned a different paper (see below).
WRONG_SS_HIT: set[str] = set()

WORD_RE = re.compile(r"[^\W_]+")


# Markup that reference strings carry into a title: JATS/HTML tags and
# entities ("robust &lt;em&gt;de novo&lt;/em&gt;"), LaTeX commands ("Robust
# \\emph{De Novo}"), and one exporter's mangled braces ("\\textbackslash
# emph\u0142brace De Novo\\rbrace"). Left in, they split the title so that no
# contiguous match is possible.
MARKUP_RE = re.compile(r"<[^>]+>|\\[A-Za-z]+|\b(?:textbackslash|emph|textit|textbf)(?=[\s{\u0142]|$)"
                       r"|[\u0142l]brace|rbrace|[{}]")


def tokens(s: str) -> list[tuple[str, bool]]:
    """The title's words in their original case, each with whether punctuation
    follows it. normalize_title() is these words, lower-cased and joined."""
    if not s:
        return []
    # NFKC so a ligature ("classi\ufb01cation") and a full-width colon read as text.
    s = MARKUP_RE.sub(" ", unicodedata.normalize("NFKC", html.unescape(s)))
    out = []
    for m in WORD_RE.finditer(s):
        nxt = WORD_RE.search(s, m.end())
        gap = s[m.end():nxt.start()] if nxt else ""
        out.append((m.group(0), bool(gap.strip())))
    return out


def normalize_title(s: str) -> str:
    return " ".join(w for w, _ in tokens(s)).lower()


def ref_year(value) -> int | None:
    """A reference's printed year: Crossref gives "2006" or "2006a", S2 an int."""
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return int(m.group(1)) if m else None


def parse_publication_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def is_chronological_citation(
    citing_id: int,
    cited_id: int,
    pub_dates: dict[int, str | None],
) -> bool:
    """Return False when a citation points to a future publication."""
    citing_date = parse_publication_date(pub_dates.get(citing_id))
    cited_date = parse_publication_date(pub_dates.get(cited_id))
    if citing_date is None or cited_date is None:
        return True
    return citing_date >= cited_date


def fetch_crossref_refs(doi: str) -> list[dict]:
    """Return the list of references for a DOI, or [] on any failure."""
    try:
        r = requests.get(
            f"{CROSSREF_BASE}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("reference", []) or []
    except requests.RequestException:
        return []


def fetch_semantic_scholar_refs(doi: str | None, title: str) -> list[dict]:
    """Return Semantic Scholar references for a paper.

    Tries DOI lookup first, falls back to title search if no DOI. Returns [] on
    any failure (network, 404, rate-limit).
    """
    paper_id = f"DOI:{doi}" if doi else None
    if not paper_id and title:
        # Search by title
        try:
            r = requests.get(
                f"{SS_BASE}/search",
                params={"query": title, "limit": 1, "fields": "paperId,title"},
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                return []
            data = body.get("data") or []
            if not data:
                return []
            # Confirm the search hit looks like our paper before pulling refs.
            hit_title = (data[0].get("title") or "").strip()
            hit, ours = normalize_title(hit_title), normalize_title(title)
            if not titles_match(hit, ours, citation=False, toks=tokens(hit_title)):
                # The old check passed this, so earlier runs pulled ANOTHER
                # paper's references for ours; main() deletes what they left.
                if fuzz.token_set_ratio(hit, ours) >= FUZZY_THRESHOLD:
                    WRONG_SS_HIT.add(title)
                return []
            paper_id = data[0]["paperId"]
            time.sleep(SS_DELAY)
        except (requests.RequestException, ValueError):
            return []

    if not paper_id:
        return []

    try:
        r = requests.get(
            f"{SS_BASE}/{paper_id}/references",
            params={"fields": "title,externalIds,year", "limit": 1000},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            return []
        data = body.get("data") or []
        return [item.get("citedPaper") or {} for item in data]
    except (requests.RequestException, ValueError):
        return []


def already_processed_ids(cur: sqlite3.Cursor) -> set[int]:
    """Pub ids we've already queried, derived from existing build artifacts."""
    seen: set[int] = set()
    # Anything with at least one resolved outgoing edge is definitely processed.
    for (cid,) in cur.execute("SELECT DISTINCT citing_id FROM publication_citation"):
        seen.add(int(cid))
    # Anything in the audit CSV is also processed (fuzzy-matched references logged).
    if AUDIT_PATH.exists():
        with AUDIT_PATH.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    seen.add(int(row["citing_pub_id"]))
                except (KeyError, ValueError):
                    pass
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-processed", action="store_true",
                    help="Skip pubs already in publication_citation or citation_audit.csv. "
                         "Useful for incremental refreshes after adding new papers.")
    ap.add_argument("--only", type=str, default=None,
                    help="Comma-separated publication ids to process exclusively "
                         "(overrides --skip-processed).")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur  = conn.cursor()

    all_pubs = cur.execute(
        "SELECT id, title, doi, publication_date FROM publication ORDER BY id"
    ).fetchall()
    total = len(all_pubs)

    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        pubs = [p for p in all_pubs if p[0] in wanted]
        print(f"--only filter: {len(pubs)}/{total} publications selected.")
    elif args.skip_processed:
        seen = already_processed_ids(cur)
        pubs = [p for p in all_pubs if p[0] not in seen]
        print(f"--skip-processed: {len(pubs)}/{total} new publications to process "
              f"({len(seen)} already queried).")
    else:
        pubs = all_pubs
        print(f"Loaded {len(pubs)} publications from the DB.")

    # Lookup tables: DOI → pub_id, normalized_title → pub_id. Built from the
    # *full* catalog so references can resolve back to any paper, not just the
    # filtered subset currently being processed.
    by_doi   = {(d or "").lower(): pid for pid, _, d, _ in all_pubs if d}
    by_title = {normalize_title(t): pid for pid, t, _, _ in all_pubs if t}
    pub_dates = {pid: pub_date for pid, _, _, pub_date in all_pubs}
    title_year = {normalize_title(t): int(d[:4]) for _, t, _, d in all_pubs if t and d}
    all_titles_norm = list(by_title.keys())

    audit_rows: list[dict] = []
    refused_rows: list[dict] = []
    edges: dict[tuple[int, int], set[str]] = {}  # (citing, cited) → {sources}

    for idx, (pid, title, doi, _) in enumerate(pubs, 1):
        print(f"[{idx}/{len(pubs)}] pub {pid}: {title[:60]}")

        # 1) Crossref by DOI
        crossref_refs: list[dict] = []
        if doi:
            crossref_refs = fetch_crossref_refs(doi)
            time.sleep(CROSSREF_DELAY)

        # 2) Semantic Scholar (works with or without DOI; uses title search as fallback)
        ss_refs = fetch_semantic_scholar_refs(doi, title)
        time.sleep(SS_DELAY)

        # ---- Match Crossref references back to our DB ----
        for ref in crossref_refs:
            ref_doi = (ref.get("DOI") or "").lower().strip()
            ref_title = ref.get("article-title") or ref.get("series-title") or ""
            citation = not ref_title
            if citation:
                ref_title = ref.get("unstructured") or ""
            cited = match_reference(ref_doi, ref_title, citation, by_doi, by_title,
                                    all_titles_norm, audit_rows, refused_rows, pid,
                                    ref_year(ref.get("year")), title_year)
            if cited and cited != pid and is_chronological_citation(pid, cited, pub_dates):
                edges.setdefault((pid, cited), set()).add("crossref")

        # ---- Match Semantic Scholar references ----
        for ref in ss_refs:
            ext = ref.get("externalIds") or {}
            ref_doi   = (ext.get("DOI") or "").lower().strip()
            ref_title = (ref.get("title") or "")
            cited = match_reference(ref_doi, ref_title, False, by_doi, by_title,
                                    all_titles_norm, audit_rows, refused_rows, pid,
                                    ref_year(ref.get("year")), title_year)
            if cited and cited != pid and is_chronological_citation(pid, cited, pub_dates):
                edges.setdefault((pid, cited), set()).add("semanticscholar")

    # ---- Write edges to DB ----
    print(f"\nResolved {len(edges)} unique citation edges.")
    for (citing, cited), srcs in sorted(edges.items()):
        source = "both" if len(srcs) == 2 else next(iter(srcs))
        cur.execute(
            "INSERT OR IGNORE INTO publication_citation (citing_id, cited_id, source) VALUES (?, ?, ?)",
            (citing, cited, source),
        )
    # Delete what this run REFUTED: an edge the old token-set rule proposed,
    # titles_match() rejected, and no reference in this run supports.
    refuted = {(r["citing_pub_id"], r["refused_pub_id"]) for r in refused_rows} - edges.keys()
    # And a Semantic-Scholar-only edge out of a paper whose title search found
    # some other paper: those were that other paper's references.
    wrong = [pid for pid, t, _, _ in pubs if t in WRONG_SS_HIT]
    for pid in wrong:
        for (cited,) in cur.execute("SELECT cited_id FROM publication_citation WHERE citing_id = ? "
                                    "AND source = 'semanticscholar'", (pid,)).fetchall():
            if (pid, cited) not in edges:
                refuted.add((pid, cited))
                refused_rows.append({"citing_pub_id": pid, "reference_title": "(wrong Semantic Scholar hit)",
                                     "citation_string": 0, "refused_pub_id": cited,
                                     "refused_title_normalized": ""})
    if wrong:
        print(f"Semantic Scholar's title search found a different paper for {len(wrong)}: {wrong}")
    deleted = set()
    for citing, cited in sorted(refuted):
        n = cur.execute("DELETE FROM publication_citation WHERE citing_id = ? AND cited_id = ?",
                        (citing, cited)).rowcount
        if n:
            deleted.add((citing, cited))
    print(f"Deleted {len(deleted)} refuted edges ({len(refuted)} refused proposals had no other support).")
    conn.commit()
    conn.close()

    # Overwritten each run: what THIS run refused, and which edges went.
    with REFUSED_PATH.open("w", newline="", encoding="utf-8") as fh:
        cols = ["citing_pub_id", "reference_title", "citation_string",
                "refused_pub_id", "refused_title_normalized", "edge_deleted"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in refused_rows:
            w.writerow({**r, "edge_deleted": int((r["citing_pub_id"], r["refused_pub_id"]) in deleted)})
    print(f"Wrote {len(refused_rows)} refused proposals to {REFUSED_PATH.name}.")

    # ---- Write audit CSV (append; preserve prior entries so --skip-processed
    # can still detect previously-queried pubs after re-runs).
    if audit_rows:
        new_file = not AUDIT_PATH.exists()
        with AUDIT_PATH.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(audit_rows[0].keys()))
            if new_file:
                w.writeheader()
            w.writerows(audit_rows)
        verb = "Wrote" if new_file else "Appended"
        print(f"{verb} {len(audit_rows)} fuzzy-match audit rows to {AUDIT_PATH}.")
    else:
        print("No new fuzzy matches in this run.")
    return 0


def titles_match(ref: str, cand: str, citation: bool,
                 toks: list[tuple[str, bool]] | None = None) -> bool:
    """Is the normalised reference `ref` the catalog title `cand`?

    token_set_ratio, the old verdict, ignores unmatched tokens in EITHER string,
    so any reference containing all of a title's words scored 100: "Foundation
    model enables interpretable open and error-tolerant searching for mass
    spectrometry-based proteomics" matched "Foundation model for mass
    spectrometry proteomics", and every reference with the four words of "De
    novo peptide sequencing" matched that chapter. A catalog title under
    MIN_FUZZY_WORDS matches only exactly. Otherwise:

    - the catalog title appears in the reference as one contiguous span that
      STARTS a title. That covers a free-text Crossref citation (`citation`),
      with authors and venue around the title, and a name in front of a bare
      title ("NovoHMM: A hidden Markov model for..."). A span starts a title
      at the beginning of the string, after punctuation ("Pevzner P. A. | De
      novo..."), or after a capitalised word ("Zhang K., In | SPIDER..."). A
      lower-case word in front means a longer title ends with ours: "A
      suboptimal algorithm for | de novo peptide sequencing via tandem mass
      spectrometry" is Lu & Chen, "Better de novo | peptide sequencing with
      deep learning" is DeepNovoV2;
    - or, for a bare title, the two agree nearly in full, or one is the other
      plus a subtitle.

    `toks` are `ref`'s words as tokens() returns them, case and punctuation
    kept, which is what decides where a title starts.
    """
    if ref == cand:
        return True
    if len(cand.split()) < MIN_FUZZY_WORDS:
        return False
    if len(cand) <= len(ref):
        al = fuzz.partial_ratio_alignment(cand, ref)
        if al is not None and al.score >= 95:
            # Snap the span to whole words before judging what precedes it.
            n = len(ref[:al.dest_start].split())
            if n and not ref[:al.dest_start].endswith(" "):
                n -= 1
            if n == 0 or toks is None:
                return True
            prev, punct = toks[n - 1]
            if punct or prev[0].isdigit() or prev[0].isupper() or prev == "al":
                return True
    if citation:
        return False
    if fuzz.ratio(ref, cand) >= FUZZY_THRESHOLD:
        return True
    short, long_ = (cand, ref) if len(cand) <= len(ref) else (ref, cand)
    if len(short.split()) < MIN_FUZZY_WORDS:
        return False
    return fuzz.ratio(short, long_[:len(short)]) >= 95


def match_reference(
    ref_doi: str,
    ref_title: str,
    citation: bool,
    by_doi: dict[str, int],
    by_title: dict[str, int],
    all_titles_norm: list[str],
    audit_rows: list[dict],
    refused_rows: list[dict],
    citing_id: int,
    ref_year: int | None = None,
    title_year: dict[str, int] | None = None,
) -> int | None:
    """Resolve a single reference to a publication id in our DB.

    Matching priority:
    1. DOI exact-match into the DB ⇒ that paper.
    2. DOI present but NOT in the DB ⇒ this reference is a different paper;
       skip it entirely. Fuzzy title matching here would produce false
       positives like Medzihradszky's "Lessons in de novo peptide
       sequencing" being matched to Sherenga's "De novo peptide sequencing
       via tandem mass spectrometry".
    3. No DOI ⇒ exact normalized title; otherwise the token-set prefilter
       proposes candidates and titles_match() decides. A fuzzy hit is logged
       to audit_rows; a proposal titles_match() rejects goes to refused_rows,
       and the edge it would have made is deleted if it exists.
    """
    if ref_doi:
        if ref_doi in by_doi:
            return by_doi[ref_doi]
        # DOI conclusively identifies a different paper, don't fuzzy-match.
        return None
    if not ref_title:
        return None
    toks = tokens(ref_title)
    # A notice ABOUT a paper names that paper; a bare title's trailing page
    # number ("...proteomic short reads 396") is not part of it.
    m = NOTICE_RE.match(" ".join(w for w, _ in toks).lower())
    if m:
        toks = toks[len(m.group(0).split()):]
    if not citation:
        while toks and toks[-1][0].isdigit():
            toks = toks[:-1]
    norm = " ".join(w for w, _ in toks).lower()
    if not norm:
        return None
    # Exact normalized-title match → strong, no audit row needed.
    if norm in by_title:
        return by_title[norm]
    proposed = process.extract(norm, all_titles_norm, scorer=fuzz.token_set_ratio,
                               score_cutoff=FUZZY_THRESHOLD, limit=None)
    if not proposed:
        return None
    def too_late(c: str) -> bool:
        return bool(ref_year and title_year and title_year.get(c, 0) > ref_year + YEAR_SLACK)
    accepted = [(c, sc) for c, sc, _ in proposed
                if titles_match(norm, c, citation, toks) and not too_late(c)]
    # Most specific wins: the longest accepted title, then the higher score.
    best = max(accepted, key=lambda cs: (len(cs[0]), cs[1]), default=None)
    # Every proposal the verdict rejects is refuted, not just the old favourite:
    # an earlier run under a looser rule may have taken any of them.
    for c, _, _ in proposed:
        if best is None or by_title[c] != by_title[best[0]]:
            refused_rows.append({
                "citing_pub_id": citing_id,
                "reference_title": ref_title.strip()[:200],
                "citation_string": int(citation),
                "refused_pub_id": by_title[c],
                "refused_title_normalized": c[:160],
            })
    if best is None:
        return None
    audit_rows.append({
        "citing_pub_id": citing_id,
        "reference_doi": ref_doi or "",
        "reference_title": ref_title.strip()[:160],
        "matched_pub_id": by_title[best[0]],
        "matched_title_normalized": best[0][:160],
        "score": best[1],
    })
    return by_title[best[0]]


if __name__ == "__main__":
    sys.exit(main())

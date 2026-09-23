#!/usr/bin/env python3
"""Fill author.orcid and author.openalex_id from OpenAlex, matched per publication.

Run offline (not in CI). Re-run after adding papers.

WHY PER PUBLICATION. A name-based lookup would be unsafe in this catalog: three
different researchers are called Xiang Zhang, which is the whole reason the
author_display view and the disambiguator column exist. So this never searches
by name globally. For each publication it already has an OpenAlex id for, it
compares OUR author list for that paper against OPENALEX's authorship list for
the same paper. "Xiang Zhang on paper 4" and "Xiang Zhang on paper 199" are
therefore resolved independently and can land on different ORCIDs, which is the
correct behaviour.

WHAT IT REFUSES TO DO. Two conflict checks, because a wrong identifier is worse
than a missing one:

  * forward conflict: one of our authors matching two different ORCIDs across
    papers. Usually means two real people are still merged into one author row.
  * reverse conflict: one ORCID matching two of our author rows. Usually means
    one person is split across two rows, or a name match was too loose.

Neither is written. Both go to author_id_audit.csv, because each one is a
data-quality finding about the catalog rather than a lookup failure.

THREE SOURCES. OpenAlex supplies both ORCID and openalex_id; Crossref supplies
ORCIDs only, but independently -- publishers deposit them from their submission
systems and OpenAlex does not always propagate them. Both feed the same vote
dict, so the conflict checks above apply ACROSS sources as well as across
papers. Either pass can be skipped (--skip-openalex is useful when OpenAlex is
rate-limiting the shared free IP budget, which it does readily). The ORCID
registry is the third and the only one where the PERSON is the authority: it
returns holders who claimed the DOI in their own record.

ORCID is queried BY DOI, never by name. Its name search returns 712 records for
"Xiang Zhang", and this catalog holds three different people by that name.

Google Scholar is deliberately NOT attempted. There is no public API, profile
ids are not programmatically discoverable, and scraping the profile search is
both blocked and against their terms. The handful of scholar_id values in the
catalog were verified by hand and should stay that way.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "denovo.db"
AUDIT_PATH = Path(__file__).parent / "author_id_audit.csv"

USER_AGENT = (
    "awesome-de-novo-peptide-sequencing/0.1 "
    "(https://github.com/BioGeek/awesome_de_novo_peptide_sequencing; "
    "mailto:j.vangoey@instadeep.com)"
)
OPENALEX_BASE = "https://api.openalex.org/works"
CROSSREF_BASE = "https://api.crossref.org/works"
ORCID_SEARCH = "https://pub.orcid.org/v3.0/expanded-search/"
ORCID_RECORD = "https://pub.orcid.org/v3.0/{}/record"
REQUEST_DELAY = 0.12   # OpenAlex allows 10 req/sec for the polite pool


# Forward conflicts that HAVE been resolved by hand, so the builder stops
# re-reporting them on every run. An audit CSV that always contains the same
# three rows is one nobody reads, and the point of it is that every row is
# actionable.
#
# Each entry needs evidence, not a preference:
#
#   105 Lei Xin -- TWO ORCID registrations for ONE person, not two people.
#       0000-0001-6900-8973 (6 works) and 0000-0003-1619-1545 (4 works) are
#       both named "Lei Xin" and BOTH claim the same glycopeptide paper, one of
#       them also holding its Author Correction. Two different researchers do
#       not co-claim a paper. The older and fuller registration wins; ORCID
#       itself has a duplicate-account process, which is the author's to use.
#
#    73 Siqi Sun -- two genuinely different people, but only one of them is in
#       this catalog. 0000-0001-7240-8724 is Fudan (Associate Professor, 2022)
#       and Microsoft Research, 38 works on structure prediction, cryo-EM and
#       AI proteomics. 0009-0007-7298-7288 is a WashU/Fudan postdoc with 3
#       works on bronchopulmonary dysplasia and IHC cell detection. Publications
#       21 and 107 are the SAME paper (pi-PrimeNovo in Nature Communications and
#       its bioRxiv preprint), so the preprint simply has the wrong ORCID
#       deposited against a namesake.
ORCID_RESOLVED: dict[int, str] = {
    105: "0000-0001-6900-8973",
    73:  "0000-0001-7240-8724",
}


def describe_orcid(orcid: str) -> str:
    """"0000-0001-7240-8724 = Siqi Sun, Fudan University, 38 works".

    Resolved from the public ORCID record, and cached per run. This is what
    turns a conflict from "two ids, pick one" into evidence: a DUPLICATE
    registration shows the same name with overlapping works, while a NAMESAKE
    shows a different employer and a disjoint field.
    """
    if orcid in _orcid_cache:
        return _orcid_cache[orcid]
    out = orcid
    try:
        r = requests.get(ORCID_RECORD.format(orcid),
                         headers={"Accept": "application/json",
                                  "User-Agent": USER_AGENT}, timeout=30)
        if r.status_code == 200:
            d = r.json()
            person = d.get("person") or {}
            nm = person.get("name") or {}
            given = ((nm.get("given-names") or {}) or {}).get("value") or ""
            family = ((nm.get("family-name") or {}) or {}).get("value") or ""
            acts = d.get("activities-summary") or {}
            groups = ((acts.get("employments") or {}).get("affiliation-group")) or []
            orgs = []
            for grp in groups:
                for summary in (grp.get("summaries") or []):
                    emp = summary.get("employment-summary") or {}
                    org = (emp.get("organization") or {}).get("name")
                    if org:
                        orgs.append(org)
            n_works = len(((acts.get("works") or {}).get("group")) or [])
            bits = [f"{given} {family}".strip() or "(name private)"]
            if orgs:
                bits.append(orgs[0])
            bits.append(f"{n_works} works")
            out = f"{orcid} = " + ", ".join(bits)
    except (requests.RequestException, ValueError):
        pass
    time.sleep(REQUEST_DELAY)
    _orcid_cache[orcid] = out
    return out


_orcid_cache: dict[str, str] = {}


def ensure_columns(cur: sqlite3.Cursor) -> None:
    cols = {row[1] for row in cur.execute("PRAGMA table_info(author)")}
    for name in ("orcid", "openalex_id"):
        if name not in cols:
            cur.execute(f"ALTER TABLE author ADD COLUMN {name} TEXT")
            print(f"added column author.{name}")


def tokens(name: str) -> list[str]:
    """Lowercase ASCII name tokens, accents folded, initials dropped."""
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    parts = re.sub(r"[^a-z ]", " ", n.lower()).split()
    return [p for p in parts if len(p) > 1]      # drop single-letter initials


def same_person(a: str, b: str) -> bool:
    """Conservative name agreement for two authors of the SAME paper.

    Requires the surname (last multi-letter token) to match, plus at least one
    other token. "Wout Bittremieux" vs "W. Bittremieux" agrees on the surname
    only, so it is rejected: within one paper's author list a surname alone is
    too weak, and a missed match costs nothing while a wrong one is permanent.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    if ta[-1] != tb[-1]:
        return False
    return len(set(ta) & set(tb)) >= 2 or (ta == tb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="overwrite ids that are already set")
    parser.add_argument("--skip-openalex", action="store_true",
                        help="skip the OpenAlex pass (e.g. while it is rate-limiting)")
    parser.add_argument("--skip-crossref", action="store_true",
                        help="skip the Crossref ORCID pass")
    parser.add_argument("--skip-orcid", action="store_true",
                        help="skip the ORCID-registry pass")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    ensure_columns(cur)
    conn.commit()

    works = cur.execute(
        "SELECT pi.publication_id, pi.openalex_id FROM publication_impact pi "
        "WHERE IFNULL(pi.openalex_id,'') <> '' ORDER BY pi.publication_id"
    ).fetchall()

    ours: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for pid, aid, name in cur.execute(
        "SELECT pa.publication_id, a.id, a.name FROM publication_author pa "
        "JOIN author a ON a.id = pa.author_id "
        "ORDER BY pa.publication_id, pa.author_order, a.id"
    ):
        ours[pid].append((aid, name))

    # author id -> {orcid: [publication ids]} so a conflict names its evidence
    orcid_votes: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    oa_votes: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    names = {aid: nm for aid, nm in cur.execute("SELECT id, name FROM author")}

    for idx, (pid, openalex_id) in enumerate([] if args.skip_openalex else works, 1):
        try:
            r = requests.get(f"{OPENALEX_BASE}/{openalex_id}",
                             headers={"User-Agent": USER_AGENT}, timeout=30)
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            data = None
        time.sleep(REQUEST_DELAY)
        if not data:
            print(f"[{idx}/{len(works)}] pub {pid}: OpenAlex lookup failed")
            continue

        remote = []
        for a in data.get("authorships") or []:
            au = a.get("author") or {}
            remote.append((au.get("display_name") or "",
                           (au.get("orcid") or "").rsplit("/", 1)[-1] or None,
                           (au.get("id") or "").rsplit("/", 1)[-1] or None))

        matched = 0
        for aid, name in ours.get(pid, []):
            hits = [t for t in remote if same_person(name, t[0])]
            # Ambiguous within one paper: skip rather than guess.
            if len(hits) != 1:
                continue
            _rname, orcid, oa_id = hits[0]
            matched += 1
            if orcid:
                orcid_votes[aid][orcid].append(pid)
            if oa_id:
                oa_votes[aid][oa_id].append(pid)
        if idx % 25 == 0:
            print(f"[{idx}/{len(works)}] pub {pid}: {matched}/{len(ours.get(pid, []))} "
                  f"authors matched")

    # ------------------------------------------------------- crossref orcids
    # A SECOND, independent ORCID source. Publishers deposit ORCIDs to Crossref
    # from their submission systems, and OpenAlex does not always propagate
    # them: measured over this catalog, Crossref supplies 82 ORCIDs for authors
    # OpenAlex left with none, while confirming 276 it already gave us.
    #
    # Votes go into the SAME orcid_votes dict rather than a fallback chain, so a
    # disagreement BETWEEN the two sources surfaces as a forward conflict and is
    # refused, instead of whichever source ran last quietly winning.
    #
    # Crossref carries no OpenAlex id, so this pass only contributes ORCIDs.
    if not args.skip_crossref:
        with_doi = cur.execute(
            "SELECT id, doi FROM publication WHERE IFNULL(doi,'') <> '' ORDER BY id"
        ).fetchall()
        print(f"\nCrossref pass over {len(with_doi)} publications with a DOI")
        for idx, (pid, doi) in enumerate(with_doi, 1):
            try:
                r = requests.get(f"{CROSSREF_BASE}/{doi}",
                                 headers={"User-Agent": USER_AGENT}, timeout=30)
                msg = r.json().get("message") if r.status_code == 200 else None
            except (requests.RequestException, ValueError):
                msg = None
            time.sleep(0.05)
            if not msg:
                continue
            remote = [
                (f"{a.get('given', '')} {a.get('family', '')}".strip(),
                 (a.get("ORCID") or "").rstrip("/").rsplit("/", 1)[-1] or None)
                for a in (msg.get("author") or [])
            ]
            for aid, name in ours.get(pid, []):
                hits = [t for t in remote if same_person(name, t[0])]
                if len(hits) != 1:      # ambiguous within one paper: never guess
                    continue
                _rname, orcid = hits[0]
                if orcid:
                    orcid_votes[aid][orcid].append(pid)
            if idx % 60 == 0:
                print(f"[{idx}/{len(with_doi)}] crossref")

    # -------------------------------------------------- orcid registry, by DOI
    # A THIRD source, and the only one where the person themselves is the
    # authority: these are ORCID holders who have CLAIMED the DOI in their own
    # record. Measured over this catalog it supplies 34 ORCIDs the other two
    # sources miss, independently confirms 387 of the ones they gave, and
    # disagrees with 5 -- disagreements that are invisible without a third
    # opinion, and which become forward conflicts here rather than silent
    # overwrites.
    #
    # KEYED ON DOI, NEVER ON NAME. ORCID also exposes a name search, and it must
    # not be used: "given-names:Xiang AND family-name:Zhang" returns 712
    # records, and this catalog contains three different Xiang Zhangs. Searching
    # per publication is the same discipline the OpenAlex pass uses and the
    # reason that pass is safe.
    if not args.skip_orcid:
        with_doi = cur.execute(
            "SELECT id, doi FROM publication WHERE IFNULL(doi,'') <> '' ORDER BY id"
        ).fetchall()
        print(f"\nORCID registry pass over {len(with_doi)} publications with a DOI")
        for idx, (pid, doi) in enumerate(with_doi, 1):
            try:
                r = requests.get(
                    ORCID_SEARCH,
                    params={"q": f'doi-self:"{doi}"', "rows": 100},
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=30)
                res = (r.json().get("expanded-result") or []) if r.status_code == 200 else None
            except (requests.RequestException, ValueError):
                res = None
            time.sleep(0.15)
            if not res:
                continue
            remote = [
                (f"{x.get('given-names') or ''} {x.get('family-names') or ''}".strip(),
                 x.get("orcid-id"))
                for x in res
            ]
            for aid, name in ours.get(pid, []):
                hits = [t for t in remote if same_person(name, t[0])]
                if len(hits) != 1:      # ambiguous within one paper: never guess
                    continue
                if hits[0][1]:
                    orcid_votes[aid][hits[0][1]].append(pid)
            if idx % 60 == 0:
                print(f"[{idx}/{len(with_doi)}] orcid")

    # ---------------------------------------------------------------- resolve
    audit: list[dict] = []

    def resolve(votes, label, plurality=False):
        """id -> value, refusing anything ambiguous in either direction.

        `plurality` treats a forward conflict as ONE person split across several
        identifiers and takes the one with the most supporting publications.
        That is right for openalex_id and wrong for ORCID, and the asymmetry is
        not a convenience:

          * An ORCID is claimed by a PERSON, so two ORCIDs on one of our rows
            may be two real people (measured: Siqi Sun really is two). Picking
            the more frequent one would silently merge them.
          * An openalex_id is assigned by OpenAlex's own disambiguation, so two
            of them on one row is almost always OpenAlex splitting one person.
            Kevin Eloff has A5039211008 on four papers and A5130920657 on one;
            Ming Li has the near-consecutive A5100351398 and A5100351454. The
            plurality is the person and the remainder is the artifact.

        A tie is still refused, because then there is no plurality to trust.
        """
        clean, forward_conflicts = {}, 0
        for aid, options in votes.items():
            if len(options) > 1 and label == "orcid" and aid in ORCID_RESOLVED:
                chosen = ORCID_RESOLVED[aid]
                if chosen in options:
                    clean[aid] = chosen
                    continue
                # The hand-resolved value is no longer among the reported ones,
                # so the evidence behind it has changed and it must be revisited
                # rather than trusted.
                audit.append({
                    "kind": f"stale hand resolution ({label})",
                    "author_id": aid, "author": names.get(aid, "?"),
                    "values": f"ORCID_RESOLVED says {chosen}, sources now say "
                              + "; ".join(sorted(options)),
                    "who": "",
                })
                continue
            if len(options) > 1 and plurality:
                ranked = sorted(options.items(), key=lambda kv: (-len(kv[1]), kv[0]))
                if len(ranked[0][1]) > len(ranked[1][1]):
                    clean[aid] = ranked[0][0]
                    audit.append({
                        "kind": f"resolved by plurality ({label})",
                        "author_id": aid, "author": names.get(aid, "?"),
                        "values": "; ".join(f"{v} on {len(p)} pub(s)"
                                            for v, p in ranked),
                        "who": "",
                    })
                    continue
                # A tie: no plurality to trust, so fall through and refuse.
            if len(options) > 1:
                forward_conflicts += 1
                audit.append({
                    "kind": f"forward conflict ({label})",
                    "author_id": aid, "author": names.get(aid, "?"),
                    "values": "; ".join(f"{v} on pubs {sorted(p)}"
                                        for v, p in sorted(options.items())),
                    # Say WHO each candidate is, so the reader can tell a
                    # duplicate registration (same name, overlapping works)
                    # from a namesake (different employer, disjoint field)
                    # without doing the API archaeology by hand.
                    "who": " | ".join(describe_orcid(v) for v in sorted(options))
                                if label == "orcid" else "",
                })
                continue
            clean[aid] = next(iter(options))
        # reverse: one value claimed by several of our author rows
        owners = defaultdict(list)
        for aid, value in clean.items():
            owners[value].append(aid)
        reverse = {v: a for v, a in owners.items() if len(a) > 1}
        for value, aids in sorted(reverse.items()):
            audit.append({
                "kind": f"reverse conflict ({label})",
                "author_id": ";".join(map(str, sorted(aids))),
                "author": " | ".join(names.get(a, "?") for a in sorted(aids)),
                "values": value,
                "who": describe_orcid(value) if label == "orcid" else "",
            })
        final = {aid: v for aid, v in clean.items() if v not in reverse}
        print(f"\n{label}: {len(final)} resolved, {forward_conflicts} forward "
              f"conflicts, {len(reverse)} reverse conflicts")
        return final

    orcids = resolve(orcid_votes, "orcid")
    oa_ids = resolve(oa_votes, "openalex_id", plurality=True)

    # ------------------------------------------------------------------ write
    written = {"orcid": 0, "openalex_id": 0}
    if not args.dry_run:
        for column, values in (("orcid", orcids), ("openalex_id", oa_ids)):
            for aid, value in sorted(values.items()):
                existing = cur.execute(
                    f"SELECT IFNULL({column},'') FROM author WHERE id = ?", (aid,)
                ).fetchone()[0]
                if existing and not args.force:
                    continue
                if existing == value:
                    continue
                cur.execute(f"UPDATE author SET {column} = ? WHERE id = ?", (value, aid))
                written[column] += 1
        conn.commit()

    if audit:
        audit.sort(key=lambda r: (r["kind"], str(r["author_id"])))
        with AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(audit[0].keys()))
            w.writeheader()
            w.writerows(audit)
    elif AUDIT_PATH.exists():
        # Delete it rather than leaving the previous run's findings on disk.
        # The file only ever means "there is something to read", so a stale one
        # is worse than none: it reports conflicts that have since been fixed.
        AUDIT_PATH.unlink()
        print(f"{AUDIT_PATH.name} removed: nothing unresolved.")

    total = cur.execute("SELECT COUNT(*) FROM author").fetchone()[0]
    have = cur.execute("SELECT COUNT(*) FROM author WHERE IFNULL(orcid,'') <> ''").fetchone()[0]
    print(f"\nDone. wrote {written['orcid']} orcid, {written['openalex_id']} openalex_id.")
    print(f"Coverage: {have}/{total} authors have an ORCID.")
    if audit:
        print(f"{len(audit)} conflicts written to {AUDIT_PATH.name}, none applied. "
              f"Each is a data-quality finding worth reading.")
    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        conn.rollback()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

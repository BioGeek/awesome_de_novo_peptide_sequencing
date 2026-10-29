#!/usr/bin/env python3
"""Mine the LOCAL PDF library for public-repository accessions and link them to datasets.

Why this is a builder and not a query. `publication_dataset` says which papers
used which data, and no API will tell you: the accession is in the paper's data
availability statement, in prose, often only in a supplementary table. The PDFs
are already on disk for exactly this kind of question.

What it will and will not do:

- It NEVER creates a `dataset`, `dataset_version` or `dataset_address` row.
  Deciding that a pile of spectra is a named dataset, and which version of it,
  is a judgement call -- the whole point of the three-table split -- so an
  accession this script has not seen before goes to `dataset_candidates.csv`
  for a human, ranked by how many papers cite it.
- With `--write` it creates `publication_dataset` rows, and only for accessions
  already recorded in `dataset_address`. That half IS mechanical: if a paper
  prints `MSV000081382`, it used the nine-species benchmark.

**It must never run in CI.** There is no PDF library on a runner. Same rule as
`build_pdf_library.py`, which also owns the filename-to-publication mapping this
script reads out of `pdf_status.csv` rather than redoing.

The ranking in the candidates CSV is the useful output. Accession reuse is
bimodal: a few are shared resources cited by a dozen papers, and a long tail of
several hundred are deposits cited once, by the paper that made them. That split
is what `dataset.kind` records, and the tail is correctly NOT worth a dataset row
each.

    uv run python scripts/build_dataset_accessions.py                # report only
    uv run python scripts/build_dataset_accessions.py --write        # link known accessions
    uv run python scripts/build_dataset_accessions.py --min-papers 3 # tighter candidate list
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import zipfile

DB = (pathlib.Path(__file__).resolve().parent.parent / "denovo.db")
DEFAULT_DIR = pathlib.Path.home() / "Documents" / "de_novo_peptide_sequencing"  # = build_pdf_library.DEFAULT_DIR
CANDIDATES = (pathlib.Path(__file__).resolve().parent.parent / "dataset_candidates.csv")

# Keep these anchored on the identifier's own shape. A looser pattern picks up
# figure labels and reference numbers, and an accession that is not real cannot
# be told from one that is without a lookup per hit.
PATTERNS = {
    "PRIDE":         re.compile(r"\bPXD\d{6}\b"),
    "MassIVE":       re.compile(r"\bMSV\d{9}\b"),
    "jPOST":         re.compile(r"\bJPST\d{6}\b"),
    "iProX":         re.compile(r"\bIPX\d{7,}\b"),
    "PeptideAtlas":  re.compile(r"\bPAe\d{6}\b"),
    "Zenodo":        re.compile(r"10\.5281/zenodo\.\d+", re.I),
    "figshare":      re.compile(r"10\.6084/m9\.figshare\.\d+", re.I),
    "Hugging Face":  re.compile(r"huggingface\.co/datasets/([\w.-]+/[\w.-]+)", re.I),
}

# A Zenodo or figshare DOI in a methods section is as likely to be a software
# release or a supplementary-file bundle as a deposit of spectra, and those are
# not datasets. The first run surfaced three in the top of the candidate list:
# PyTorch Lightning 0.7.6, and supplementary files for two de novo papers. They
# would come back on every run, so they are filtered on the repository's own
# title and the exclusion is COUNTED in the report rather than done silently.
#
# Only DOI-shaped accessions are checked. An accession is never excluded on its
# own shape: a figshare DOI is a perfectly good home for spectra.
NOT_DATA = [
    (re.compile(r"\bsupplementar|supporting information|^supplementary data\b", re.I),
     "supplementary material, not a deposit of spectra"),
    (re.compile(r":\s*v?\d+[\d.]*\s*(release)?$|^[\w.-]+/[\w.-]+:\s", re.I),
     "software release (a repository snapshot, not data)"),
    (re.compile(r"\b(source code|scripts?|notebooks?|docker image)\b", re.I),
     "code rather than data"),
]
DOI_LIKE = re.compile(r"^10\.\d{4,9}/")

# A version with more provenance addresses than this is a training CORPUS
# listing its source projects, not a benchmark assembled from a few
# submissions. Printing one of MassNet's 647 PRIDE sources says a paper used
# that project, not MassNet: three papers (582, 801, 846) were proposed as
# MassNet users that way. The nine-species benchmark's nine sources stay under
# it, because a paper printing one of those IS running on the benchmark's data.
CORPUS_PROVENANCE = 20

# Links a reader has refused, by (publication id, accession), each with the
# paper's own reason. Without this they come back on every run.
REJECTED_LINKS = {
    (996, "PXD023419"): "PepPr names the Herceptin and anti-FLAG-M2 data only to "
                        "say it did not use them: their cysteines were alkylated with "
                        "iodoacetic acid, which Casanovo does not support.",
    (57, "PXD008899"): "A typo in Table 4.4's caption of the PointNovo thesis: the "
                       "sclera data it searched is PXD008999, linked as such.",
}


def datacite_attributes(doi: str, cache: pathlib.Path) -> dict:
    """The DataCite record's attributes, cached; {} when there is none.

    Zenodo and figshare DOIs are DataCite, not Crossref; Crossref answers 404."""
    cache.mkdir(parents=True, exist_ok=True)
    key = cache / (re.sub(r"[^\w.-]", "_", doi) + ".json")
    if key.exists():
        payload = json.loads(key.read_text())
    else:
        out = subprocess.run(
            ["curl", "-s", "-A", "awesome-de-novo (mailto:j.vangoey@instadeep.com)",
             f"https://api.datacite.org/dois/{doi}"],
            capture_output=True, text=True, timeout=60).stdout
        try:
            payload = json.loads(out)
        except ValueError:
            payload = {}
        key.write_text(json.dumps(payload))
    try:
        return payload["data"]["attributes"] or {}
    except (KeyError, TypeError):
        return {}


def datacite_title(doi: str, cache: pathlib.Path) -> str:
    try:
        return datacite_attributes(doi, cache)["titles"][0]["title"]
    except (KeyError, IndexError, TypeError):
        return ""


def not_data(doi: str, cache: pathlib.Path) -> tuple[str, str] | None:
    """Return (title, reason) when this DOI is demonstrably not a data deposit."""
    title = datacite_title(doi, cache)
    # THE REPOSITORY'S OWN TYPE comes first. A title need not say software:
    # 'Fine-Tuning Scheduler', a PyTorch Lightning extension on Zenodo, reads
    # like nothing in particular, passed every title pattern below, and became
    # a dataset with its own page. DataCite records it as Software.
    rtype = ((datacite_attributes(doi, cache).get("types") or {})
             .get("resourceTypeGeneral") or "")
    if rtype.lower() == "software":
        return title, "software (DataCite resourceTypeGeneral), not data"
    if not title:
        return None          # unknown is not the same as excluded
    for rx, reason in NOT_DATA:
        if rx.search(re.sub(r"\s+", " ", title)):
            return title, reason
    return None


def pdf_text(path: pathlib.Path) -> str:
    """pdftotext, collapsed to one line so an accession split over a line break still matches."""
    try:
        out = subprocess.run(["pdftotext", "-q", str(path), "-"],
                             capture_output=True, text=True, timeout=180).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  !! {path.name}: {exc}", file=sys.stderr)
        return ""
    return re.sub(r"\s+", " ", out)


def file_to_publication(con: sqlite3.Connection, library: pathlib.Path) -> dict[str, int]:
    """Call build_pdf_library.py's own matcher rather than matching titles again.

    That matching is the hard part and it is already solved and audited there
    (prefix-against-prefix, cut to the shorter string, with a year tie-break);
    a second copy here would be a worse one that could disagree with the
    filenames on disk.

    Do NOT read the `file` column of `pdf_status.csv` for this, which is the
    obvious-looking shortcut and is wrong: that column is written by `fetch` for
    the files it downloads, so it covers neither hand-filed PDFs nor renames.
    Measured when this script first ran, it named 130 of the 243 files present
    and 82 names that no longer existed, which silently halved the scan.

    Importing build_pdf_library pulls in rapidfuzz, so run this script under
    `uv run python`, not bare python3.
    """
    import build_pdf_library as bpl

    pubs = bpl.load_publications(con)
    out: dict[str, int] = {}
    for pid, paths in bpl.coverage(pubs, library).items():
        for path in paths:
            out[path.name] = pid
    return out


def sheet_text(path: pathlib.Path) -> str:
    """The text of an .xlsx, read as the zip of XML it is: an accession in a
    spreadsheet is a plain shared string, so no spreadsheet library is needed."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = " ".join(z.read(n).decode("utf-8", "replace")
                           for n in z.namelist() if n.endswith(".xml"))
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"  !! {path.name}: {exc}", file=sys.stderr)
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))


def supplements_of(pubs: list[dict], library: pathlib.Path) -> list[tuple[int, pathlib.Path]]:
    """Every file in supplements/ named after a paper: '<paper> - <label>.pdf|.xlsx'.

    Wider than build_pdf_library.supplement_files(), which takes only the
    numbered PDFs because that is what the table miner reads. An accession
    list is as likely to sit in 'Supplementary Data 1.xlsx' or 'Source
    Data.xlsx': MassNet's 647 source projects are a spreadsheet."""
    import build_pdf_library as bpl

    d = library / bpl.SUPP_DIR
    if not d.is_dir():
        return []
    out = []
    for pub in pubs:
        stem = bpl.zotero_name(pub)[:-4] + " - "
        for f in sorted(d.iterdir()):
            if f.name.startswith(stem) and f.suffix.lower() in (".pdf", ".xlsx"):
                out.append((pub["id"], f))
    return out


def scan(library: pathlib.Path, only: set[int] | None, pub_of: dict[str, int],
         supplements: list[tuple[int, pathlib.Path]] = ()):
    import build_pdf_library as bpl

    pdfs = bpl.pdfs(library)
    hits: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
    sources = [(pub_of.get(pdf.name), pdf) for pdf in pdfs] + list(supplements)
    for n, (pid, pdf) in enumerate(sources, 1):
        if pid is None or (only and pid not in only):
            continue
        text = sheet_text(pdf) if pdf.suffix.lower() == ".xlsx" else pdf_text(pdf)
        for repo, rx in PATTERNS.items():
            for m in rx.finditer(text):
                acc = m.group(1) if rx.groups else m.group(0)
                # A repository name may contain '.', so the pattern takes
                # dots, and a URL ending a sentence then keeps the full stop:
                # 'InstaDeepAI/ms_proteometools.' became a dataset of its own,
                # a duplicate of HC-PT's own address.
                acc = acc.rstrip(".")
                hits[(repo, acc)].add(pid)
        if n % 50 == 0:
            print(f"  {n}/{len(sources)}", file=sys.stderr)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR,
                    help=f"PDF library root (default {DEFAULT_DIR})")
    ap.add_argument("--write", action="store_true",
                    help="create publication_dataset rows for KNOWN accessions")
    ap.add_argument("--ids", help="comma-separated publication ids to restrict the scan to")
    ap.add_argument("--min-papers", type=int, default=2,
                    help="candidate CSV keeps accessions cited by at least this many papers (default 2)")
    args = ap.parse_args()

    if not args.dir.is_dir():
        sys.exit(f"no PDF library at {args.dir}")

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")

    # accession -> (dataset_id, version_id or None). A version_id is only
    # asserted when exactly one NON-provenance address claims the accession: a
    # provenance accession says where spectra came from, not which curated
    # version a paper actually ran on, so it resolves to the dataset with the
    # version left NULL. That NULL is a finding, not a gap.
    known: dict[str, tuple[int, int | None]] = {}
    rows = con.execute("""
        SELECT a.accession, v.dataset_id, v.id, a.is_provenance
          FROM dataset_address a JOIN dataset_version v ON v.id = a.dataset_version_id
    """).fetchall()
    by_acc: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
    # A DOI IS CASE-INSENSITIVE, and papers print Zenodo's in both cases:
    # '10.5281/ZENODO.6791263' became a second dataset beside the lower-case
    # one already catalogued. Every accession is keyed in lower case.
    for acc, dsid, vid, prov in rows:
        by_acc[acc.split("#")[0].lower()].append((dsid, vid, prov, "#" in acc))
    prov_count = collections.Counter(vid for _a, _d, vid, prov in rows if prov)
    # accession -> the versions that list it as a source. A paper already linked
    # to one of those versions has said all an accession of its sources can:
    # MassNet's own paper prints its 647 projects in a supplementary sheet, and
    # nine of them are deposits catalogued in their own right.
    prov_of: dict[str, set[int]] = collections.defaultdict(set)
    for acc, _d, vid, prov in rows:
        if prov:
            prov_of[acc.split("#")[0].lower()].add(vid)
    corpus_sources: set[str] = set()
    for acc, claims in by_acc.items():
        # A corpus's source list is not a use of the corpus (CORPUS_PROVENANCE).
        # Dropping those claims first also un-blocks an accession that is a
        # dataset in its own right AND one of a corpus's sources: PXD014877 is
        # the Kingdoms of Life deposit and one of InstaNovo-FM's 92 sources,
        # and was left out as ambiguous.
        kept = [c for c in claims if not (c[2] and prov_count[c[1]] > CORPUS_PROVENANCE)]
        if not kept:
            corpus_sources.add(acc)
            continue
        claims = kept
        datasets = {d for d, _, _, _ in claims}
        direct = [(d, v) for d, v, p, _ in claims if not p]
        # A PAPER PRINTS THE BARE ACCESSION, never the '#folder' that tells one
        # version's address from another's. MSV000090982 holds three builds
        # under 'MSV000090982#peak' and two '#updates/...' folders, plus the
        # bare address on 'revised (build not stated)', which is exactly what a
        # paper printing only the accession has told us. So when one direct
        # claim is the bare accession and the rest are folders, it wins.
        # The same holds ACROSS datasets: denovo_benchmarks' deposit
        # MSV000096182 is a dataset of its own at the bare accession, and its
        # nine-species part is a version of the nine-species benchmark at
        # 'MSV000096182#9_species'. A paper printing MSV000096182 means the
        # deposit.
        bare = [(d, v) for d, v, p, sub in claims if not p and not sub]
        subs_only = all(sub and not p for d, v, p, sub in claims if (d, v) not in bare)
        if len(datasets) == 1 and len(direct) == 1:
            known[acc] = direct[0]
        elif len(bare) == 1 and subs_only:
            known[acc] = bare[0]
        elif len(datasets) == 1:
            known[acc] = (next(iter(datasets)), None)
        # An accession claimed by two different datasets is ambiguous and is
        # left out rather than guessed at.

    only = {int(i) for i in args.ids.split(",")} if args.ids else None
    pub_of = file_to_publication(con, args.dir)
    print(f"{len(pub_of)} filed PDFs, {len(known)} known accessions", file=sys.stderr)
    import build_pdf_library as bpl
    supps = supplements_of(bpl.load_publications(con), args.dir)
    print(f"{len(supps)} supplementary files", file=sys.stderr)
    hits = scan(args.dir, only, pub_of, supps)

    # A SET, not a dict keyed by paper: a paper can introduce several versions,
    # and the dict kept only the last, so every other deposit of that paper came
    # back proposed as a second, 'uses' link beside its 'introduces' one (62 of
    # the 83 proposals when this was found).
    introduced = {tuple(r) for r in con.execute(
        "SELECT introduced_by, id FROM dataset_version WHERE introduced_by IS NOT NULL")}
    # A paper that already links a dataset, in any version and any role, has
    # had that link curated: a bare accession can only ever add a LESS specific
    # version ('revised (build not stated)' beside 'revised (MassIVE 2024)'), or
    # repeat as 'uses' what a reader recorded as 'trains-on' or 'evaluates-on'.
    # tuple(), because a sqlite3.Row never equals the tuple it is tested against.
    already = {tuple(r) for r in con.execute("SELECT publication_id, dataset_id FROM publication_dataset")}
    linked_versions = {tuple(r) for r in con.execute(
        "SELECT publication_id, dataset_version_id FROM publication_dataset "
        "WHERE dataset_version_id IS NOT NULL")}

    linked = skipped = 0
    # Several accessions legitimately resolve to the same link: a paper that
    # lists all nine provenance submissions of the nine-species benchmark is one
    # publication_dataset row and nine hits. Without this set the report counts
    # hits while --write counts rows, so the two disagree (187 against 86) and
    # the report is the wrong one.
    seen: set[tuple[int, int, int, str]] = set()
    unknown: list[tuple[int, str, str, str]] = []
    rejected = {(pid, acc.lower()) for pid, acc in REJECTED_LINKS}
    n_corpus = n_rejected = 0
    for (repo, acc), pids in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        if acc.lower() in corpus_sources:
            n_corpus += len(pids)
            continue
        refused = {pid for pid in pids if (pid, acc.lower()) in rejected}
        n_rejected += len(refused)
        pids = pids - refused
        if not pids:
            continue
        target = known.get(acc.lower())
        if target is None:
            title = con.execute("SELECT title FROM publication WHERE id=?",
                                (sorted(pids)[0],)).fetchone()
            unknown.append((len(pids), repo, acc,
                            (title[0] if title else "")[:70]))
            continue
        dsid, vid = target
        for pid in sorted(pids):
            # The miner cannot tell training from evaluation, so a new link is
            # 'uses': purpose not recorded. A reader upgrades it.
            role = "introduces" if (pid, vid) in introduced and vid else "uses"
            key = (pid, dsid, vid if vid is not None else -1, role)
            if key in seen:
                continue
            seen.add(key)
            if (pid, dsid) in already or any(
                    (pid, v) in linked_versions for v in prov_of.get(acc.lower(), ())):
                skipped += 1
                continue
            if args.write:
                con.execute("""INSERT INTO publication_dataset
                    (publication_id,dataset_id,dataset_version_id,role) VALUES(?,?,?,?)""",
                            (pid, dsid, vid, role))
            linked += 1
    if args.write:
        con.commit()

    # Drop the DOIs a repository's own title shows are software or supplementary
    # material. Done here rather than during the scan so it costs one lookup per
    # unknown DOI instead of one per mention, and only for DOI-shaped ids.
    cache = (pathlib.Path(__file__).resolve().parent.parent / ".cache") / "datacite"
    excluded: list[tuple[int, str, str, str]] = []
    surviving = []
    for papers, repo, acc, example in unknown:
        verdict = not_data(acc, cache) if DOI_LIKE.match(acc) else None
        if verdict:
            title, reason = verdict
            excluded.append((papers, acc, title[:60], reason))
        else:
            surviving.append((papers, repo, acc, example))
    unknown = surviving

    unknown.sort(reverse=True)
    kept = [u for u in unknown if u[0] >= args.min_papers]
    with CANDIDATES.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["papers", "repository", "accession", "example_paper"])
        w.writerows(kept)

    print()
    print(f"  accessions seen          {len(hits)}")
    # Subtract BOTH: an excluded DOI is neither known nor unknown, and leaving
    # it out of this arithmetic reported "402 of 402 known" while 17 were in
    # fact excluded.
    print(f"  of those already known   {len(hits) - len(unknown) - len(excluded)}")
    print(f"  publication_dataset rows {'written' if args.write else 'proposed'}: {linked}"
          f" ({skipped} already linked to that dataset)")
    print(f"  not proposed             {n_corpus} hits on a corpus's source projects, "
          f"{n_rejected} refused in REJECTED_LINKS")
    print(f"  unknown accessions       {len(unknown)}, "
          f"{len(kept)} cited by >= {args.min_papers} papers -> {CANDIDATES.name}")
    print(f"  excluded as not data     {len(excluded)}")
    for papers, acc, title, reason in sorted(excluded, reverse=True):
        print(f"      {papers:2} papers  {acc:28} {reason}")
        print(f"                   {title!r}")
    if not args.write:
        print("\nReport only. Re-run with --write to create the links.")
    print("Datasets are never created automatically: review the CSV by hand.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

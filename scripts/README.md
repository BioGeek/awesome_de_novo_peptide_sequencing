# Scripts

Everything that builds, refreshes, mines or checks the catalog. This folder
holds **31 scripts**; every one has an entry below, under a heading that is its
filename, and `check_counts.py` fails the commit when the two drift apart:
**0 scripts** lack an entry below, and **0 entries** name a script that does not
exist.

Run them from the repository root, which is where they read `denovo.db` and
write their audit CSVs and caches:

```bash
uv run python scripts/build_pages.py          # most need the project's deps
python3 scripts/check_counts.py               # a few are stdlib-only
```

The background on each, including the traps met while writing it, is in
`CLAUDE.md`. Three rules hold across the folder:

- **CI or local.** Scripts marked *CI* run on a schedule in
  `.github/workflows/` and commit only when their data changed. Scripts marked
  *local* need the PDF library, a browser, a GPU or a person's judgement, and
  must never run in CI.
- **Report-only by default.** A script that proposes catalog content (a paper,
  a dataset, a repository) writes a CSV or a page for a person and never
  writes `denovo.db` without an explicit flag.
- **One writer per table.** Each refresh builder owns its tables, which is what
  lets `.github/actions/commit-refreshed-db` recover from a push race.

## Scheduled refreshes (CI)

### `build_repo_metrics.py`
GitHub stars, open issues and pull requests and last push for every repository
in `algorithm_repository`, into `repository_metrics`. Daily.

### `build_publication_impact.py`
OpenAlex `cited_by_count` and work id per publication, into
`publication_impact`. Weekly. Run it before `build_affiliations.py`, which
skips any publication without an OpenAlex id.

### `build_citations.py`
The intra-catalog citation graph from Crossref and Semantic Scholar, into
`publication_citation`. Monthly; `--only <ids>` for new papers. Writes
nothing until the walk ends, and fuzzy title matches go to
`citation_audit.csv`.

### `build_journal_metrics.py`
OpenAlex two-year mean citedness per peer-reviewed venue, into
`journal_impact`. Twice a year.

### `build_benchmarks.py`
Results, curves and rankings from bittremieuxlab/denovo_benchmarks, into the
`benchmark_*` tables. Weekly; exits early when the upstream commit has not
moved.

### `build_proteobench.py`
ProteoBench's de novo DDA-HCD submissions, into the `proteobench_*` tables.
Weekly, with the benchmarks.

### `build_candidates.py`
Papers that probably belong in the catalog: works our papers cite and works
citing them, scored by how many of ours they link to. Monthly in CI, writes
`candidates.csv` only, and skips everything in `WATCHLIST.md` and
`screening_decisions.tsv`.

### `build_dnps_candidates.py`
The same question asked of the DNPS-DR daily literature report (a Hugging Face
Space). Monthly in CI, writes `dnps_candidates.csv` only.

## Curation (local)

### `build_abstracts.py`
Backfills `publication.abstract` from bioRxiv, arXiv, Europe PMC, OpenAlex
and Crossref, recording the source; never overwrites a hand-entered abstract.

### `build_affiliations.py`
Institutions, departments, cities and countries from OpenAlex, matched per
byline into `publication_author_affiliation`. Report-only until `--write`;
refusals go to `affiliation_audit.csv`.

### `build_author_ids.py`
Fills `author.orcid` and `author.openalex_id` from OpenAlex, matched per
publication; anything ambiguous goes to `author_id_audit.csv`.

### `build_versions.py`
Links preprints to their peer-reviewed versions through `publication_version`,
from bioRxiv and Crossref relations; uncertain pairs go to
`version_audit.csv`.

### `build_checkpoints.py`
Checks every recorded model checkpoint link (live, gated, dead,
unverifiable); `--mirror` reports what could be backed up and what blocks it.

### `build_repository_candidates.py`
Proposes code repositories for methods that have none, from the URLs their own
PDFs print, scored by name match and availability wording. Writes
`repository_candidates.csv` only.

## The PDF library and what is mined from it (local)

### `build_pdf_library.py`
Keeps `~/Documents/de_novo_peptide_sequencing/pdfs/` in step with the catalog:
`report`, `fetch` (only what a source states is free), `ingest` (hand
downloads, with chapter slicing), `rename`, `dedupe`, `supplements`.

### `build_pdf_abstracts.py`
Lifts an abstract out of a paper's own PDF for rows no API can serve, and
rejects anything that does not end like an abstract.

### `build_dataset_accessions.py`
Finds repository accessions (PXD, MSV, Zenodo, figshare, Hugging Face) in the
PDFs and links papers to datasets already catalogued; unknown accessions go to
`dataset_candidates.csv`.

### `build_paper_comparisons.py`
The comparison-table miner: locates benchmark tables in the PDFs, parses them
under a set of named guards, and resolves methods, datasets, metrics and
bases. Holds the per-paper registries.

### `review_comparisons.py`
Builds the local review page that puts every mined table beside a crop of the
printed one, takes sign-offs from `paper_comparison_review.json`, and with
`--write-db` rebuilds the `paper_comparison*` tables.

### `image_tables.py`
Finds tables that exist only as images and turns two agreeing vision-model
readings into a grid the miner can resolve.

### `read_table_images.py`
Reads table crops with one vision model per run (GLM-OCR or PaddleOCR-VL)
into a cache, and `--crosscheck` reads the text tables too.

### `crosscheck_tables.py`
Compares every mined text table with both vision-model readings row by row,
and points a person at any cell both models read differently.

### `build_table_vlm.py`
An earlier, single-model cross-check of a table read by a vision model;
report-only.

### `build_table_structure.py`
Reads a table's column grouping from its arXiv LaTeX source, where one
exists, and proposes overrides for the miner.

## The site

### `build_pages.py`
Generates one Quarto page per paper, method, author, institution, venue,
dataset, family and application area, from `denovo.db`.

### `slugs.py`
The URL of every generated page, shared by `build_pages.py` and `index.qmd`;
`--check` guards `slugs.lock`, and `REDIRECTS` keeps renamed URLs working.

### `render_scope.py`
Decides before a publish whether it needs a full, partial or index-only
render, from the manifest the previous run left in `_site`.

### `postrender.py`
Post-render fixes Quarto cannot express itself, run by `quarto render`.

## Checks

### `check_counts.py`
Keeps every number quoted in the docs and comments equal to the database, and
fails on a violated must-be-zero invariant. Runs in the pre-commit hook and in
CI. It also counts this folder against this file.

### `check_chart_overlap.py`
Renders nothing itself: drives the rendered `_site` in headless Chrome and
fails on any colliding or clipped chart label.

## Shared modules

### `openalex_key.py`
Reads `OPENALEX_API_KEY` from the environment or `.env` and adds it only to
requests going to OpenAlex.

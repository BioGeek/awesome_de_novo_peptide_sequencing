# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A curated knowledge base covering the *de novo* peptide sequencing field: algorithms, post-processors, downstream applications, and adjacent tools, deep-learning and classical alike. The "code" is mostly data plumbing around two artifacts:

- `denovo.db`: SQLite database (the source of truth) holding publications, algorithms, authors, affiliations, cities, countries, and the join tables that link them.
- `denovo.sql`: full SQL dump of `denovo.db`, committed alongside the binary so diffs are reviewable in git. Treat `denovo.sql` as the canonical, human-readable representation; regenerate it after any DB write.
- `scripts/`: every builder, miner and check, each described under its own heading in `scripts/README.md`. Run them from the repository root (`uv run python scripts/build_pages.py`): they anchor `denovo.db`, the audit CSVs and `.cache/` on the root, not on their own folder. `scripts/check_counts.py` fails the commit when a script has no README entry or an entry names a script that is gone.
- `plots.ipynb`: Jupyter notebook that connects to `denovo.db`, runs SQL, and renders matplotlib figures (offline exploration / sanity-check only, not published).
- `index.qmd` + `_quarto.yml`: the Quarto site that renders interactive charts straight from `denovo.db`.
- `WATCHLIST.md`: tools that belong in the catalog but have no citable manuscript yet, plus things deliberately left out. Check it before concluding a tool is simply missing, and add to it rather than adding a method with no publication.
- `NINE_SPECIES.md`: a short pointer to the site page *The nine-species benchmark, version by version*, which compares the benchmark's eleven public versions spectrum by spectrum. The page's source is `analysis/nine_species/page.qmd` plus `site_data.json`, and the code that produced the numbers is beside them (run off-repo, never in CI). Read it before attaching a number to a nine-species version: MSV000090982 holds three builds, none of them the published Zenodo main, and denovo_benchmarks runs on a selection of its own.
- `BENCHMARKS.md`: the field's public benchmarks, which two are charted and why the others are not, and the retrained-versus-released trap. **Read it before putting a benchmark number next to another one**: NovoBench, ProteoBench and denovo_benchmarks all say "nine-species" and only two of the three are comparable.


## Topic docs (read the one for the area you touch)

CLAUDE.md holds what every change needs. Everything specific to one area is in `docs/`, and a section named elsewhere ("see **Patents**", "'Checkpoints' in CLAUDE.md") is in the file listed here:

- [`docs/benchmarks.md`](docs/benchmarks.md): **Public benchmarks**; **The critical-difference diagram**; **ProteoBench**; **Performance in release order**; **Benchmark numbers on the method pages**
- [`docs/publishing.md`](docs/publishing.md): **The publish renders what changed, not everything**; **Structured data on the generated pages**; **IndexNow: telling Bing what changed**; **URLs are a lock file**
- [`docs/data-entry.md`](docs/data-entry.md): **Publication dates**; **Preprint versions**; **Abstracts**; **Affiliations, and why the ROR matters**
- [`docs/pdf-library.md`](docs/pdf-library.md): **The local PDF library**
- [`docs/literature-feeds.md`](docs/literature-feeds.md): **The OpenAlex key, and the Space that watches the literature**; **BioGeek/denovo-radar**; **Citation graph**; **Finding papers the catalog is missing**; **Finding code the catalog is missing**; **Mining the DNPS-DR feed**
- [`docs/datasets.md`](docs/datasets.md): **Datasets, at three grains**; **What a paper did with a dataset**; **The two dataset charts**; **The dataset network and the dataset table**; **build_dataset_accessions.py**; **A deposit happens once**; **Auditing the deposit roles**; **A dataset page per dataset**
- [`docs/checkpoints.md`](docs/checkpoints.md): **Checkpoints**; **What the download actually found**; **The mirror, and the three places checkpoints appear**; **build_checkpoints.py**; **Which dataset a checkpoint was trained on**
- [`docs/patents.md`](docs/patents.md): **Patents**
- [`docs/comparison-tables.md`](docs/comparison-tables.md): **Reading a table's structure out of its LaTeX source**; **Spot-checking the mined tables**; **Comparison tables in the database**; **Supplements, Extended Data, and a paper's own results**; **On the algorithm pages**
- [`docs/site.md`](docs/site.md): **"Recently added" is publication.id, not a date**; **The navbar and the hero badges**; **Working with the notebook**; **The Quarto site**; **The homepage loads its data as files**; **The two network charts filter on papers per author, with a slider**; **OJS source is public, Python chunk source is not**; **Every family gets a lane, and one switch turns it into the history chart**; **Hand-written analysis pages**; **A family page needs two methods**; **Plot's dx, dy and textAnchor are constants, not channels**; **No chart on the page has an overlapping label**; **Clamping a force layout hides the bug; it does not fix it**; **Plot tooltips truncate the value, not the label**

## Common commands

```bash
# Environment (Python >=3.12, managed by uv)
uv sync                          # install deps from pyproject.toml / uv.lock
uv run jupyter notebook plots.ipynb

# Regenerate the SQL dump after any change to denovo.db, commit BOTH files together
# (also automated by the pre-commit hook — see 'Repo hooks' below).
sqlite3 denovo.db .dump > denovo.sql

# Rebuild denovo.db from the dump (e.g. after pulling a commit that changed denovo.sql)
rm denovo.db && sqlite3 denovo.db < denovo.sql

# One-time setup per clone: activate the tracked pre-commit hook that
# auto-regenerates denovo.sql whenever you stage denovo.db.
git config core.hooksPath .githooks

# Quick inspection
sqlite3 denovo.db ".tables"
sqlite3 denovo.db "SELECT name, algorithm_family FROM algorithm ORDER BY name;"

# Rebuild the citation graph from Crossref + Semantic Scholar (offline, ~30 min)
uv run python scripts/build_citations.py

# Refresh GitHub stars / issues / PRs / last-pushed for every repo in algorithm_repository
# (offline, ~15 min, uses the `gh` CLI for auth, run `gh auth login` first if needed)
uv run python scripts/build_repo_metrics.py

# Refresh OpenAlex cited_by_count per publication (~5 min)
uv run python scripts/build_publication_impact.py

# Refresh OpenAlex 2-year mean citedness per peer-reviewed venue (~5 min)
uv run python scripts/build_journal_metrics.py

# Fill author ORCID + OpenAlex ids from OpenAlex, matched PER PUBLICATION
# (offline, ~1 min). Refuses to write anything ambiguous; conflicts go to
# author_id_audit.csv and are usually duplicate or merged author rows.
uv run python scripts/build_author_ids.py

# Look for papers that belong in the catalog but are not in it, by asking
# OpenAlex what our publications cite and what cites them (offline, ~10 min).
# Writes candidates.csv and NEVER touches denovo.db: deciding what belongs is a
# judgement call, not something a citation count can automate.
python3 scripts/build_candidates.py                        # both directions, >=3 links
python3 scripts/build_candidates.py --direction citations   # only work that cites us
python3 scripts/build_candidates.py --min-links 6           # tighter, less noise

# Check (or refresh) the counts quoted in CLAUDE.md and WATCHLIST.md against
# denovo.db. Stdlib only, no network, instant. The pre-commit hook runs --fix
# automatically on every commit, and check-counts.yml runs the
# bare check in CI, so you rarely need to call this by hand.
python3 scripts/check_counts.py           # report stale counts, exit 1 if any
python3 scripts/check_counts.py --fix     # rewrite them in place

# Fill affiliations, departments, city coordinates and country ISO codes from
# OpenAlex, matched PER BYLINE (offline, ~10 min, responses cached in .cache/).
# REPORT-ONLY by default: writes nothing until --write, and never overwrites a
# hand-curated value. Everything it declines to write goes to
# affiliation_audit.csv.
python3 scripts/build_affiliations.py                  # report only
python3 scripts/build_affiliations.py --write          # apply
python3 scripts/build_affiliations.py --only-new        # just the papers added since last time

# Refresh the public-benchmark results from bittremieuxlab/denovo_benchmarks
# (offline, ~2 min, responses cached in .cache/benchmarks/). Exits early when
# the upstream commit is the one already recorded, so a quiet week costs one API
# call. --dry-run prints the per-tool table and writes nothing.
uv run python scripts/build_benchmarks.py
uv run python scripts/build_benchmarks.py --force

# Refresh the ProteoBench de-novo DDA-HCD submissions (offline, ~1 min). Same
# early exit on an unchanged upstream commit.
uv run python scripts/build_proteobench.py

# Fail if any chart on the rendered site has a colliding or clipped label.
# Measures the real glyph boxes in headless Chrome, so it needs a rendered
# _site and google-chrome (~1 min). Not in CI for that reason.
uv run quarto render index.qmd
uv run --with websockets python3 scripts/check_chart_overlap.py

# Check that no generated page's URL changed. Every slug in slugs.lock is a
# live, indexed address. Run --write ONLY when a rename is intended.
python3 scripts/slugs.py --check
python3 scripts/slugs.py --write

# Keep a LOCAL folder of paper PDFs in step with the catalog: report what is
# missing, fetch what is legally free, name every file the same way. Offline,
# and the only script here that writes OUTSIDE the repository (--dir). Never in
# CI: there is no library there. See 'The local PDF library' below.
python3 scripts/build_pdf_library.py report              # coverage + missing/*.txt
python3 scripts/build_pdf_library.py fetch               # download what is free (~20 min)
python3 scripts/build_pdf_library.py rename --apply      # re-derive every filename
python3 scripts/build_pdf_library.py dedupe --apply      # drop byte-identical copies
# Filing hand-downloaded PDFs, including slicing one chapter out of a
# proceedings volume. Slicing needs pypdf, which is deliberately NOT a project
# dependency -- CI would install it for a script CI never runs.
uv run --with pypdf python3 scripts/build_pdf_library.py ingest manual/ --apply \
    --map 978-3-031-94039-2.pdf=13:106-114

# Mine the DNPS-DR daily report (publication 272, a Hugging Face Space) for
# papers the catalog is missing. Writes dnps_candidates.csv and NEVER touches
# denovo.db, exactly like build_candidates.py. Exits early when the Space has
# not changed. See 'Mining the DNPS-DR feed' below.
uv run python scripts/build_dnps_candidates.py
uv run python scripts/build_dnps_candidates.py --force --summary dnps_summary.md

# Mine the LOCAL PDF library for repository accessions (PXD / MSV / iProX /
# Zenodo / figshare / Hugging Face) and link them to datasets. Never in CI: it
# needs the PDF library. Creates no dataset rows, only publication_dataset
# links, and only for accessions already recorded. See 'Datasets' below.
uv run python scripts/build_dataset_accessions.py                 # report only
uv run python scripts/build_dataset_accessions.py --write         # link known accessions
uv run python scripts/build_dataset_accessions.py --min-papers 3  # tighter candidate list

# Backfill publication abstracts from bioRxiv / arXiv / OpenAlex / Crossref
# (offline, ~10 min). Skips publications that already have one, so it never
# overwrites hand-curated text; pass --force only if you mean to.
uv run python scripts/build_abstracts.py
```

### Scheduled refreshes (GitHub Actions)

All five builders also run on a cron in `.github/workflows/`, scoped to the
cadence at which each metric meaningfully moves. Each workflow commits only
when its data actually changed (no quiet-day churn) and then triggers
`publish.yml` to redeploy the site.

| Workflow                      | Script                       | Cadence                          | Cron expression  |
|-------------------------------|------------------------------|----------------------------------|------------------|
| `refresh-repo-metrics`        | `build_repo_metrics.py`      | Daily 06:00 UTC                  | `0 6 * * *`      |
| `refresh-publication-impact`  | `build_publication_impact.py`| Weekly Sun 06:30 UTC             | `30 6 * * 0`     |
| `refresh-citation-graph`      | `build_citations.py`         | Monthly 1st 07:00 UTC            | `0 7 1 * *`      |
| `refresh-journal-metrics`     | `build_journal_metrics.py`   | Semi-annual Jan 1 + Jul 1 08:00 UTC | `0 8 1 1,7 *` |
| `refresh-benchmarks`          | `build_benchmarks.py` + `build_proteobench.py` | Weekly Mon 09:00 UTC | `0 9 * * 1` |

The slot-per-hour staircase is deliberate: when two workflows are scheduled
on the same calendar day (e.g. daily + weekly on a Sunday, four of them on
Jan 1 / Jul 1) the earlier one finishes before the next one starts, so they
never race for `main` and the conditional-commit + `gh workflow run` chain
stays deterministic.

All five are also `workflow_dispatch`-able from the Actions tab if you need an
on-demand refresh (e.g., right after adding a new paper); `refresh-benchmarks`
takes a `force` input, because its builder otherwise no-ops when the upstream
commit has not moved.

### Push races and `.github/actions/commit-refreshed-db`

The staircase only separates the workflows from *each other* — it can't stop a
**human** pushing while a refresh is mid-run. That did happen and broke a daily
run: the job checked out, rebuilt, committed, and by the time it pushed `main`
had moved, so `git push` was rejected and the whole workflow failed.

All five refresh workflows now commit through the shared composite action
`.github/actions/commit-refreshed-db`, which retries a rejected push (5 attempts,
increasing backoff). The interesting part is *how* it rebases, because a plain
`git pull --rebase` is not an option here: `denovo.db` is binary so it conflicts
every time, and a textual merge of `denovo.sql` can't be trusted.

Instead it exploits an invariant of this repo — **each refresh workflow is the
sole writer of its own tables, which no other writer touches**:

| Workflow                     | Owns table            |
|------------------------------|-----------------------|
| `refresh-repo-metrics`       | `repository_metrics`  |
| `refresh-publication-impact` | `publication_impact`  |
| `refresh-citation-graph`     | `publication_citation`|
| `refresh-journal-metrics`    | `journal_impact`      |
| `refresh-benchmarks`         | the five `benchmark_*` and four `proteobench_*` tables |

On a rejected push it dumps just those tables (`sqlite3 denovo.db ".dump
<table>"`), hard-resets to `origin/main` to pick up whatever landed, replays its
rows on top, regenerates `denovo.sql`, and pushes again. Everything the other
side changed survives untouched, and the refreshed rows are not lost — no need
to re-run the (slow, network-bound) builder.

`table:` takes a space-separated LIST, parents first: the action dumps each
table in turn, drops them in reverse order so a child never outlives its
parent, and replays them in the declared order. `refresh-benchmarks` owns nine,
across its two builders.
A new refresh workflow still needs tables nobody else writes.

**`.dump <table>` carries neither indexes nor triggers**, and this step drops
the table, so the action captures their DDL from `sqlite_master` and replays it
after the rows. Without that, a push race on `refresh-citation-graph` silently
discarded `idx_publication_citation_cited` and both
`prevent_future_publication_citation_*` triggers, which are the guard against
recording a citation of a paper that did not exist yet. Verified after the fix
by dumping six tables, dropping them, replaying, and diffing the sorted dump
against the original: identical, with all six triggers present.

One cosmetic consequence is unavoidable. A dropped-and-recreated table moves to
the END of `denovo.sql`, because `.dump` follows `sqlite_master` order, so the
diff after a race recovery is large while the content is unchanged. That was
true of the single-table version too.

## Schema shape (read before editing data)

**47 tables and two views.** Core catalog: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `algorithm_repository`, `publication`, `publication_algorithm`, `publication_author`, `publication_citation`, `publication_version`, `publication_supplement`, `thesis_supervisor`, `subdomain`, `family_note`. Builder-owned tables, one set per refresh workflow: `repository_metrics`, `publication_impact`, `journal_impact`, and the nine `benchmark_*` / `proteobench_*` tables described under **Public benchmarks** below. Seven more hold the comparison tables mined from the papers, under **Comparison tables in the database** below. Plus the `author_display` view, which appends a `disambiguator` in parentheses to the name; **every chart aggregates on `display_name`, not `author.name`**, because distinct researchers share a name (three different people are called Xiang Zhang). The view is defined as `SELECT a.*, ... FROM author a` on purpose: it used to list columns explicitly, which meant every new `author` column had to be hand-added to the view, and forgetting surfaced later as a baffling `no such column` from an unrelated query. `author` carries the external identifiers `orcid`, `openalex_id`, `scholar_id` and `sciprofiles_id`; 3588 of 3831 authors have at least one. One author is **not a person**: `Micromass UK Ltd` carries the vendor manual that documents PepSeq, because vendor documentation has a corporate author and every publication needs at least one (a convention, not a trigger). Both network charts gate on authors with three or more papers in the query (raised to five by default with each chart's **Min papers per author** slider), so it stays out of the co-authorship graph and the bipartite chart.

Authors connect to publications via `publication_author` (with `author_order`) and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm` (with `role`, see **Describing a method or using it** below); thesis supervision lives in `thesis_supervisor` (`publication_id`, `author_id`) and deliberately NOT in `publication_author`, since a supervisor is not an author and recording them as one would inflate their publication count and forge a co-authorship edge; a trigger enforces that the publication is a thesis and that the supervisor is not also its author. Intra-catalog citation edges live in `publication_citation` (`citing_id`, `cited_id`, `source` ∈ `{crossref, semanticscholar, both}`). `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, `aliases`, `subdomain`) added after initial schema creation.

**Duplicate authors are merged on evidence, never on a name alone.** A bulk
import matches authors by ORCID, then OpenAlex id, then name, so one person
arriving as "Pavel A Pevzner", "Pavel Pevzner" and "Pavel A. Pevzner", or as
"J. V. Olsen" on a 2004 paper, gets a row each. The 2026-10-05 sweep paired
every two rows whose names reduce to the same surname and compatible
initials, and merged the 28 pairs that also shared a co-author or an
institution. It kept apart every pair with two different ORCIDs (20), every
pair on the same paper (3), and every common name with
nothing linking the two rows (Hong, Xin and Wei Zhang, Bin Ma, the Korean
"H. Kim" and "S. Kim"). Two different OpenAlex ids do NOT block a merge:
OpenAlex splits one person across ids often, which is what Elisa Mori and
Fanny Guzmán had. A merge moves `publication_author`,
`author_affiliation`, `publication_author_affiliation` and
`thesis_supervisor`, copies any external id the survivor lacks, and gives
the retired URL a `REDIRECTS` entry in `slugs.py`.

**"On the same paper" is not proof of two people.** Two of those three pairs
were the Yi Liu rows on publication 382, and a Crossref byline check showed
the paper has ONE Yi Liu: three rows sat in author position 1, one of them a
Beijing Yi Liu attached by an import, the other two one Waterloo student
entered twice (1235, with the 2015 thesis, merged into 1490, the ORCID row).
So a byline is checked for the shape directly now: 0 bylines put two
authors in one position, an invariant in `check_counts.py`.

**A name is stored given name first.** Nine came in from thesis repositories
as "Warren, René L.", and the page, which joins a byline with commas and
splits it again, counted each as two people: 3770 authors over 3761 rows.
Four were people already here under the usual spelling (Matthias Müller,
Susan R. Wilson, Celso Calomeno, Lancia Darville) and were merged; the other
five were turned round. 0 author names are stored surname first, an
invariant.

The same sweep undid three splits made during the imports with a
`FORCE_NEW` list (Nan Liu, Binhai Zhu, Yuanliang Zhang): those had been
forced apart on the assumption that a common Chinese name is two people,
and the co-authors and institutions said one. Force a split only on a
contradiction, such as a second ORCID or both rows on one byline.

`publication.publication_type` is a string and the SQL column comment is stale: it names only `'preprint'` / `'peer-reviewed'`, but the full vocabulary in use is `'peer-reviewed'` (797), `'preprint'` (120), `'thesis'` (42), `'ML conference'` (13), `'resource'` (4, for citable things that are not manuscripts: this catalog's own Zenodo record, a third-party link collection, a daily literature-briefing Space, and a vendor software manual, the Micromass MassLynx NT BioLynx & ProteinLynx Guide, which is the only documentation PepSeq's method has), `'postprint'` (2), `'commentary'` (1), `'abstract'` (18) and `'presentation'` (3). Use one of those nine; do not invent a tenth without updating this list, and never leave it empty.

`'abstract'` is for a citable record with a DOI behind which **no full text will ever exist**: a meeting or showcase abstract. Publications 355 and 356 are in the Journal of Student-Scientists' Research (George Mason, ISSN 2689-7679), whose navigation is literally organised as "Abstracts by Department" and whose records carry no `citation_pdf_url` and no galley. Publication 357 is an ASBMB Annual Meeting abstract carried in a Journal of Biological Chemistry supplement: OpenAlex types it `conference-abstract`, Crossref holds no abstract text, and the title itself begins "Abstract 4402", all despite a jbc.org `/fulltext` URL that makes it look like a research article. All three come from the same George Mason host-defence peptide lab. Calling such a record `'peer-reviewed'` would be wrong twice over: it is faculty-mentored rather than peer-reviewed, and it would inflate a count this file and the site both report. The type was added rather than stretched because abstracts are a recurring shape, not a one-off: `WATCHLIST.md` had already parked the Hellbender ASBMB abstract on exactly this blocker, recording that it was "in scope on the merits" and waiting only because "no `publication_type` value fits without inventing an eighth".

`'presentation'` is for a talk, poster or recording: citable, often DOI'd, and
not a manuscript. Zenodo alone holds eight presentations, a poster and a video
on this subject. **Publication 427 was retyped from `'abstract'` to this**, and
it is the worked example of why the two are different: it is EPS poster P270,
and the PDF behind its DOI is a poster, with an Overview, five figures and a
Conclusion. An abstract is a record with no full text behind it; a poster has a
full text, just not an article's. Typing it `'abstract'` understated what is
there and inflated a count this file reports.

A talk still needs an `algorithm` link like any publication, and the honest one
is the method the talk is ABOUT, with the usual role: a group presenting their
own method describes it, and a survey talk uses what it surveys.

`'postprint'` exists for a record posted to a preprint server AFTER the version of record, which is not the same thing as a preprint and must not be counted as one. Publication 30 is the worked example: an arXiv posting whose own comment field cites the BIBE 2023 conference paper it came from. **The arXiv id is the tell: a `25xx` id on a paper whose version of record predates it is a postprint, however the submitter labels it.** But check that the version of record really is one first. Publication 352, RankNovo's arXiv posting (`10.48550/arXiv.2505.17552`, 2025-05-23), was typed a postprint of "ICLR 2025" from an OpenReview forum page (publication 24). That forum is a SUBMISSION. RankNovo is not in ICLR 2025's accepted list, which does hold ReNovo and SearchNovo. The paper was accepted at ICML 2025 instead, under the arXiv title, as PMLR 267:50672-50696 with its own forum `HtSdgubxsJ`. So 352 is that paper's preprint and is linked to the proceedings row, 978, and 24 is an OpenReview preprint like 20 and 64. The lab's own README said "RankNovo have been accepted by ICML'2025" all along. Typing it correctly keeps it out of both sides of the Publication lifecycle chart, which measures a preprint-to-journal gap that does not exist here, and out of `n_preprints`. Adding a type means touching four places besides this list: the wave chart's colour domain, the BibTeX `entry_type_of` map and its `note` field, and the slug suffix policy in `slugs.py` (publication 30 shares a title with 120, so without a semantic suffix its URL falls back to `-30`).

**An ML preprint and its proceedings paper are two rows**, typed `'preprint'`
and `'ML conference'` and linked through `publication_version` with source
`'manual'`, the same shape as a bioRxiv preprint and its journal article. Six
pairs so far: RefineNovo (16 to 438), LIPNovo (17 to 439) and RankNovo (352 to
978) in PMLR v267 for ICML 2025, AdaNovo (32 to 440) and NovoBench (78 to 441) in the NeurIPS 2024
proceedings, the last in the Datasets and Benchmarks track, and CrossNovo (9 to 979)
in the NeurIPS 2025 proceedings. The preprint rows
used to carry the conference as their `journal` while pointing at arXiv; they
now say `arXiv`, and the conference row holds the proceedings URL and, for
NeurIPS, its `10.52202` DOI. Two traps: AdaNovo's old label said ICML 2024,
which was wrong, and its NeurIPS title differs ("Towards Robust De Novo Peptide
Sequencing in Proteomics against Data Biases"). The proceedings row inherits
the preprint's `publication_dataset` links, since it evaluates on the same data.

**An OpenReview forum page is not a proceedings paper.** Publications 20 and 64
had ICLR venues on the strength of an OpenReview URL; the forum pages say
"Submitted to ICLR 2025 ... Decision: Reject" and "ICLR 2026 Conference
Withdrawn Submission", so both are `journal = 'OpenReview'`, still preprints.
Publication 24, RankNovo, was the third. Its forum could not be read from here,
but ICLR's own accepted list (`iclr.cc/virtual/2025/papers.html`, 3896 papers)
settles it without OpenReview: an absent title was not accepted.
OpenReview answers scripted requests, its API included, with a challenge page;
a headful browser reads it. CrossNovo's NeurIPS 2025 proceedings appeared on
`papers.nips.cc` by 2026-10-05 (`10.52202/085713-1721`, dated 2026-04-23 by the
publisher; the row takes the conference's 2025-12-02, as 440 and 441 take
NeurIPS 2024's opening day), so it is now row 979 and 9 is its arXiv preprint.

**The proceedings row takes the clean URL.** When the preprint and the
proceedings share a title, `slugs.py`'s suffix policy gives the preprint
`-preprint`, so adding the proceedings row CHANGES the preprint's slug. The old
address does not break, because it now serves the version of record, so no
`REDIRECTS` entry is needed, and one would collide with that page. A venue
whose lowest publication id moves (NeurIPS 2025, from 9 to 979) shows up in
`--check` as one venue REMOVED and one ADDED under the same slug; that is a key
moving, not a URL.


### Adding a paper by hand

When adding rows by hand, always check whether the entity already exists before inserting: author names and affiliation `(name, department)` pairs are the natural keys, not the surrogate IDs. A typical insert path for a new paper is: `country` → `city` → `affiliation` → `author` → `author_affiliation` → `algorithm` → `publication` → `publication_author` (with `author_order` set per author) → `publication_algorithm`. See any of the previously committed paper insertions (e.g. the `CausalNovo` commit) for the standard `INSERT … SELECT id FROM …` pattern.

Then finish the job outside the database: `build_abstracts.py` for the abstract,
so `abstract_source` records where the text came from rather than claiming it
was curated, and `build_pdf_library.py fetch --ids <id>` for the PDF. See **The
local PDF library**.

## Describing a method or using it

`publication_algorithm.role` says what a paper does with a method: `'describes'`
or `'uses'`. 266 of the 1276 are `'uses'`, and they are concentrated rather than
spread: 142 of PEAKS's 149 papers are applications that ran it, mostly snake-venom
proteomics.

Without the column the two were the same link, and the consequences were not
cosmetic. PEAKS's page listed 21 papers under one heading and credited **107
authors**, nearly all of whom had merely used it; the true byline is 8. The
author→model graph carried 24 edges asserting that a venomics author had helped
publish PEAKS. And `MIN(publication_date)` over every link dated PepSeq and
manual interpretation to publication 289, an application paper, rather than to
the Micromass manual and the Current Protocols tutorial that document them.

The rule:

- **`'describes'`** — introduces, defines, extends or documents the method.
  Includes the other version of the same work (a preprint and its version of
  record both describe), a follow-up method paper by the same group (PEAKS has
  three), and an author's own thesis about their method.
- **`'uses'`** — applies it or reports results from it, while the method itself
  is described elsewhere.

`'describes'` is the DEFAULT, so an insert that predates the column still means
what it meant, and a new application link has to say `role = 'uses'` explicitly.
When adding a paper that runs an existing tool, the tool link is `'uses'` and the
paper's own workflow row is `'describes'`.

**The invariant worth re-checking after any role edit** is that every publication
with a link still has a describing one. A paper with only `'uses'` links claims to
contribute nothing, which for a paper in this catalog means the role is wrong:

```sql
SELECT p.id, p.title FROM publication p
 WHERE EXISTS (SELECT 1 FROM publication_algorithm WHERE publication_id = p.id)
   AND NOT EXISTS (SELECT 1 FROM publication_algorithm
                    WHERE publication_id = p.id AND role = 'describes');
```

The backfill was mechanical, and the two things it got wrong are the two shapes
to watch for. A link is `'describes'` when the paper links to no other method at
all, or when it is that method's earliest paper (or the other version of that
earliest paper); everything else is `'uses'`. That misread **publication 289**,
an application that is nonetheless the earliest catalog paper for both PepSeq and
manual interpretation, and **publication 339**, Samaneh Azari's thesis, which is
the sole author of both methods it links and so describes rather than uses them.
The rule cannot see either case, which is why the column is curated data and not
a view.

Four places read the role rather than the raw link: the algorithm page splits its
papers and takes its authors from the describing ones, the publication page
mirrors the split, `algorithms.first_pub` and `family_firsts` date a method by
the paper that describes it, and the author→model graph uses the `models_described`
column rather than `models`. The publications table shows the two roles as
two columns, **Describes** and **Uses** (`models_described`, `models_used`),
and keeps the full `models` list in each row, so its search box still answers
"which papers involved PEAKS" whichever role the link has.

### Editorial conventions

- **Italicize *de novo*** in every piece of user-facing copy (page title, subtitle, prose, chart titles, README). In markdown: `*de novo*`. In HTML cells: `<em>de novo</em>`. Don't italicize it inside copied paper titles, DB string literals, or identifiers.
- **Scope is comprehensive**: frame the site as a map of the whole field (algorithms + post-processors + downstream apps + adjacent tools, DL and classical alike). Do **not** re-introduce a "deep-learning only" disclaimer; previous versions had one and it's been removed.

### Classification taxonomy

Every `algorithm` row carries three classifier columns:

- `kind`: `'algorithm'`, `'post-processor'`, `'downstream-application'`, `'adjacent'`, `'review'`, `'benchmark'`, or `'meta'` (residual catch-all for commentaries / theses-without-method).
- `is_deep_learning`: `1` (TRUE), `0` (FALSE), or NULL.
- `acquisition_mode`: `'DDA'`, `'DIA'`, `'both'`, or NULL.

When adding a new entry, fill all three. The site's filters (and the hero counters) depend on them.

`algorithm.subdomain` names an application area, and is used only by
`kind='downstream-application'` rows. The areas are rows in the **`subdomain`**
table (17 of them): `name`, which is what `algorithm.subdomain` holds and what
the page's URL is, `label` for display, and `blurb`, one line on what de novo
sequencing is for in that area.

**Adding an area means a `subdomain` row plus two OJS registrations** in
`index.qmd`: `subdomain_order` and `subdomain_color`, which are presentation and
stay in the chart. `subdomain_label` used to be a third; it is now read from the
table, because `build_pages.py` generates a page per area and needed the same
labels, which would have made the OJS cell a fourth copy of the same 17 strings.
There was also a `subdomain_lane_height`; lane heights come from the row packing
now.

There is deliberately **no foreign key** from `algorithm.subdomain` to
`subdomain.name`: adding one means rebuilding a 13-column core table.
`check_counts.py` asserts the two sets agree instead, in both directions.
Both numbers are checked in the pre-commit hook and in CI, and both should
always read zero: 0 areas unregistered, 0 registered but unused. An unregistered area would otherwise
reach the site as a grey lane with a raw slug for a label and no page.

Forgetting used to be fatal: an unregistered subdomain made the timeline
dereference a missing lane and throw `TypeError: Cannot read properties of
undefined (reading 'y0')`, which kills that OJS cell and every one after it on
the page. Both charts now fall back to the raw slug and a neutral grey instead,
and the timeline appends unregistered subdomains rather than dropping them, so a
missing registration degrades visibly instead of breaking the page. Register it
anyway: the fallback is a safety net, not the intended appearance. Pick a colour
at least ~20 CIE Lab deltaE from the existing ones, and dark enough to read as a
small dot. **What matters is distance to the lanes ADJACENT in
`subdomain_order`**, not to the whole palette: two similar colours six lanes
apart are fine, two in neighbouring lanes are not. Every adjacent pair is now
at least 20 (worst 21.9); the global minimum is 12.7 between
immunopeptidomics and food-authentication, which are six lanes apart. Two
adjacent pairs used to fail this, antibodyomics against venomics at 16.0 and
plant-pathogen against metaproteomics at 11.1, both of them same-hue pairs
sitting side by side.

### Local dev

```bash
uv run quarto preview        # live-reload at http://localhost:4200
uv run quarto render         # one-shot build into _site/
```

### Updating data → updating the site

Edit `denovo.db` directly (sqlite3 CLI / DB Browser / any SQLite tool) → `sqlite3 denovo.db .dump > denovo.sql` → commit both `denovo.db` and `denovo.sql` → push to `main`. The Action rebuilds and republishes within ~2-3 minutes. **No manual `plt.savefig` step anymore.**

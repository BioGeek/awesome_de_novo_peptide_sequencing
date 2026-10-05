# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A curated knowledge base covering the *de novo* peptide sequencing field: algorithms, post-processors, downstream applications, and adjacent tools, deep-learning and classical alike. The "code" is mostly data plumbing around two artifacts:

- `denovo.db`: SQLite database (the source of truth) holding publications, algorithms, authors, affiliations, cities, countries, and the join tables that link them.
- `denovo.sql`: full SQL dump of `denovo.db`, committed alongside the binary so diffs are reviewable in git. Treat `denovo.sql` as the canonical, human-readable representation; regenerate it after any DB write.
- `plots.ipynb`: Jupyter notebook that connects to `denovo.db`, runs SQL, and renders matplotlib figures (offline exploration / sanity-check only, not published).
- `index.qmd` + `_quarto.yml`: the Quarto site that renders interactive charts straight from `denovo.db`.
- `WATCHLIST.md`: tools that belong in the catalog but have no citable manuscript yet, plus things deliberately left out. Check it before concluding a tool is simply missing, and add to it rather than adding a method with no publication.
- `BENCHMARKS.md`: the field's public benchmarks, which two are charted and why the others are not, and the retrained-versus-released trap. **Read it before putting a benchmark number next to another one**: NovoBench, ProteoBench and denovo_benchmarks all say "nine-species" and only two of the three are comparable.

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
uv run python build_citations.py

# Refresh GitHub stars / issues / PRs / last-pushed for every repo in algorithm_repository
# (offline, ~15 min, uses the `gh` CLI for auth, run `gh auth login` first if needed)
uv run python build_repo_metrics.py

# Refresh OpenAlex cited_by_count per publication (~5 min)
uv run python build_publication_impact.py

# Refresh OpenAlex 2-year mean citedness per peer-reviewed venue (~5 min)
uv run python build_journal_metrics.py

# Fill author ORCID + OpenAlex ids from OpenAlex, matched PER PUBLICATION
# (offline, ~1 min). Refuses to write anything ambiguous; conflicts go to
# author_id_audit.csv and are usually duplicate or merged author rows.
uv run python build_author_ids.py

# Look for papers that belong in the catalog but are not in it, by asking
# OpenAlex what our publications cite and what cites them (offline, ~10 min).
# Writes candidates.csv and NEVER touches denovo.db: deciding what belongs is a
# judgement call, not something a citation count can automate.
python3 build_candidates.py                        # both directions, >=3 links
python3 build_candidates.py --direction citations   # only work that cites us
python3 build_candidates.py --min-links 6           # tighter, less noise

# Check (or refresh) the counts quoted in CLAUDE.md and WATCHLIST.md against
# denovo.db. Stdlib only, no network, instant. The pre-commit hook runs --fix
# automatically on every commit, and check-counts.yml runs the
# bare check in CI, so you rarely need to call this by hand.
python3 check_counts.py           # report stale counts, exit 1 if any
python3 check_counts.py --fix     # rewrite them in place

# Fill affiliations, departments, city coordinates and country ISO codes from
# OpenAlex, matched PER BYLINE (offline, ~10 min, responses cached in .cache/).
# REPORT-ONLY by default: writes nothing until --write, and never overwrites a
# hand-curated value. Everything it declines to write goes to
# affiliation_audit.csv.
python3 build_affiliations.py                  # report only
python3 build_affiliations.py --write          # apply
python3 build_affiliations.py --only-new        # just the papers added since last time

# Refresh the public-benchmark results from bittremieuxlab/denovo_benchmarks
# (offline, ~2 min, responses cached in .cache/benchmarks/). Exits early when
# the upstream commit is the one already recorded, so a quiet week costs one API
# call. --dry-run prints the per-tool table and writes nothing.
uv run python build_benchmarks.py
uv run python build_benchmarks.py --force

# Refresh the ProteoBench de-novo DDA-HCD submissions (offline, ~1 min). Same
# early exit on an unchanged upstream commit.
uv run python build_proteobench.py

# Fail if any chart on the rendered site has a colliding or clipped label.
# Measures the real glyph boxes in headless Chrome, so it needs a rendered
# _site and google-chrome (~1 min). Not in CI for that reason.
uv run quarto render index.qmd
uv run --with websockets python3 check_chart_overlap.py

# Check that no generated page's URL changed. Every slug in slugs.lock is a
# live, indexed address. Run --write ONLY when a rename is intended.
python3 slugs.py --check
python3 slugs.py --write

# Keep a LOCAL folder of paper PDFs in step with the catalog: report what is
# missing, fetch what is legally free, name every file the same way. Offline,
# and the only script here that writes OUTSIDE the repository (--dir). Never in
# CI: there is no library there. See 'The local PDF library' below.
python3 build_pdf_library.py report              # coverage + missing/*.txt
python3 build_pdf_library.py fetch               # download what is free (~20 min)
python3 build_pdf_library.py rename --apply      # re-derive every filename
python3 build_pdf_library.py dedupe --apply      # drop byte-identical copies
# Filing hand-downloaded PDFs, including slicing one chapter out of a
# proceedings volume. Slicing needs pypdf, which is deliberately NOT a project
# dependency -- CI would install it for a script CI never runs.
uv run --with pypdf python3 build_pdf_library.py ingest manual/ --apply \
    --map 978-3-031-94039-2.pdf=13:106-114

# Mine the DNPS-DR daily report (publication 272, a Hugging Face Space) for
# papers the catalog is missing. Writes dnps_candidates.csv and NEVER touches
# denovo.db, exactly like build_candidates.py. Exits early when the Space has
# not changed. See 'Mining the DNPS-DR feed' below.
uv run python build_dnps_candidates.py
uv run python build_dnps_candidates.py --force --summary dnps_summary.md

# Mine the LOCAL PDF library for repository accessions (PXD / MSV / iProX /
# Zenodo / figshare / Hugging Face) and link them to datasets. Never in CI: it
# needs the PDF library. Creates no dataset rows, only publication_dataset
# links, and only for accessions already recorded. See 'Datasets' below.
uv run python build_dataset_accessions.py                 # report only
uv run python build_dataset_accessions.py --write         # link known accessions
uv run python build_dataset_accessions.py --min-papers 3  # tighter candidate list

# Backfill publication abstracts from bioRxiv / arXiv / OpenAlex / Crossref
# (offline, ~10 min). Skips publications that already have one, so it never
# overwrites hand-curated text; pass --force only if you mean to.
uv run python build_abstracts.py
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

**41 tables and two views.** Core catalog: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `algorithm_repository`, `publication`, `publication_algorithm`, `publication_author`, `publication_citation`, `publication_version`, `thesis_supervisor`, `subdomain`, `family_note`. Builder-owned tables, one set per refresh workflow: `repository_metrics`, `publication_impact`, `journal_impact`, and the nine `benchmark_*` / `proteobench_*` tables described under **Public benchmarks** below. Seven more hold the comparison tables mined from the papers, under **Comparison tables in the database** below. Plus the `author_display` view, which appends a `disambiguator` in parentheses to the name; **every chart aggregates on `display_name`, not `author.name`**, because distinct researchers share a name (three different people are called Xiang Zhang). The view is defined as `SELECT a.*, ... FROM author a` on purpose: it used to list columns explicitly, which meant every new `author` column had to be hand-added to the view, and forgetting surfaced later as a baffling `no such column` from an unrelated query. `author` carries the external identifiers `orcid`, `openalex_id`, `scholar_id` and `sciprofiles_id`; 3033 of 3635 authors have at least one. One author is **not a person**: `Micromass UK Ltd` carries the vendor manual that documents PepSeq, because vendor documentation has a corporate author and every publication needs at least one (a convention, not a trigger). Both network charts gate on authors with three or more papers in the query (raised to five by default with each chart's **Min papers per author** slider), so it stays out of the co-authorship graph and the bipartite chart.

Authors connect to publications via `publication_author` (with `author_order`) and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm` (with `role`, see **Describing a method or using it** below); thesis supervision lives in `thesis_supervisor` (`publication_id`, `author_id`) and deliberately NOT in `publication_author`, since a supervisor is not an author and recording them as one would inflate their publication count and forge a co-authorship edge; a trigger enforces that the publication is a thesis and that the supervisor is not also its author. Intra-catalog citation edges live in `publication_citation` (`citing_id`, `cited_id`, `source` ∈ `{crossref, semanticscholar, both}`). `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, `aliases`, `subdomain`) added after initial schema creation.

`publication.publication_type` is a string and the SQL column comment is stale: it names only `'preprint'` / `'peer-reviewed'`, but the full vocabulary in use is `'peer-reviewed'` (737), `'preprint'` (111), `'thesis'` (35), `'ML conference'` (12), `'resource'` (4, for citable things that are not manuscripts: this catalog's own Zenodo record, a third-party link collection, a daily literature-briefing Space, and a vendor software manual, the Micromass MassLynx NT BioLynx & ProteinLynx Guide, which is the only documentation PepSeq's method has), `'postprint'` (3), `'commentary'` (1), `'abstract'` (12) and `'presentation'` (2). Use one of those nine; do not invent a tenth without updating this list, and never leave it empty.

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

`'postprint'` exists for a record posted to a preprint server AFTER the version of record, which is not the same thing as a preprint and must not be counted as one. Two cases so far. Publication 30 is an arXiv posting whose own comment field cites the BIBE 2023 conference paper it came from. Publication 352 is RankNovo's arXiv posting, `10.48550/arXiv.2505.17552`, dated 2025-05-23, which is AFTER ICLR 2025 in April; the conference version is publication 24, from OpenReview. Note the arXiv title differs from the conference one ("Universal Biological Sequence Reranking for Improved De Novo Peptide Sequencing" against "RankNovo: A Universal Reranking Approach for Robust De Novo Peptide Sequencing"), so unlike publication 30 it needs no slug suffix, and it is deliberately NOT linked through `publication_version`. **The arXiv id is the tell: a `25xx` id on a paper whose version of record predates it is a postprint, however the submitter labels it.** Typing it correctly keeps it out of both sides of the Publication lifecycle chart, which measures a preprint-to-journal gap that does not exist here, and out of `n_preprints`. Adding a type means touching four places besides this list: the wave chart's colour domain, the BibTeX `entry_type_of` map and its `note` field, and the slug suffix policy in `slugs.py` (publication 30 shares a title with 120, so without a semantic suffix its URL falls back to `-30`).

**An ML preprint and its proceedings paper are two rows**, typed `'preprint'`
and `'ML conference'` and linked through `publication_version` with source
`'manual'`, the same shape as a bioRxiv preprint and its journal article. Four
pairs so far: RefineNovo (16 to 438) and LIPNovo (17 to 439) in PMLR v267 for
ICML 2025, AdaNovo (32 to 440) and NovoBench (78 to 441) in the NeurIPS 2024
proceedings, the last in the Datasets and Benchmarks track. The preprint rows
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
OpenReview answers scripted requests, its API included, with a challenge page;
a headful browser reads it. CrossNovo (9) is a NeurIPS 2025 paper whose
proceedings were not yet published on 2026-10-04, so it is still the arXiv row.

## Describing a method or using it

`publication_algorithm.role` says what a paper does with a method: `'describes'`
or `'uses'`. 238 of the 1162 are `'uses'`, and they are concentrated rather than
spread: 134 of PEAKS's 141 papers are applications that ran it, mostly snake-venom
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
column rather than `models`. The publications table keeps the full `models` list,
because "which papers involved PEAKS" is a question worth being able to answer.

## Public benchmarks

`build_benchmarks.py` reads
[bittremieuxlab/denovo_benchmarks](https://github.com/bittremieuxlab/denovo_benchmarks),
which runs 18 tools in their own containers over 84 public datasets and commits
the evaluation curves. It is the only apples-to-apples comparison the field has,
and this catalog's own data cannot produce it: everything else here describes
what a method IS.

Five tables, all written by that one builder and by nothing else:
`benchmark_dataset` (84 rows), `benchmark_tool` (18), `benchmark_result`
(1782 = one per tool, dataset and version), `benchmark_curve` (3636 = a
101-point averaged curve per tool and metric level) and `benchmark_source` (the
upstream commit, its date and the fetch date, which the site quotes).

**Two more numbers per run, read off the same curves.** `prec_full_peptide`
and `prec_full_aa` are each curve's own last point, at coverage 1, from the raw
points rather than the interpolated grid. Their meaning is fixed by upstream's
`evaluation/evaluate.py`, not guessed: the peptide curve runs over every
LABELLED spectrum with an unanswered one counted as a miss, so its last point
is correct peptides over all spectra, the papers' **peptide recall**; the
amino-acid curve runs over PREDICTED residues, so its last point is the papers'
**amino-acid precision**. Every run's curves reach coverage 1. The per-species
results ARE these curves: each `results/<dataset>/` folder holds the two
precision curves plus spectral-angle and retention-time curves that no paper
table has a counterpart for.

**What the numbers are.** `auc` in the upstream CSVs is the area under a
precision-coverage curve, which the benchmark manuscript calls AP. Two levels
are stored, peptide and amino acid. `benchmark_curve` holds the
**dataset-macro-averaged** curve: each run's curve is interpolated onto a shared
101-point coverage grid and the grid points are averaged, which is the only
correct way to average curves whose own x values differ per run. That is figure
1b/1c of the manuscript, and the site's ranking chart and heatmap are its
figure 1d.

**The AP of the averaged curve is not the median of the per-dataset APs**, and
the gap is large: InstaNovo's median AP is 0.854 and the AP of its averaged
curve is 0.736. Both are in the summary table, labelled. Averaging curves is not
averaging areas.

**Latest version, not best.** Four tools ship two versions in the results
(`_old` twins for PEAKS and Novor, two container builds each for InstaNovo and
π-HelixNovo). `version_key()` prefers the unsuffixed twin, then compares the
embedded numbers, and the chosen row is flagged `is_latest` because the rule is
not expressible in SQL. The upstream visualisation PR keeps the highest-AUC
version instead, which lets a tool use a different version on every dataset;
measured, the two rules disagree on 83 of 1452 (tool, dataset) pairs with a
median AP difference of 0.0000 and a maximum of 0.2088. Note the upstream
dashboard's own "latest" is `sorted(versions)[-1]`, which picks `12.5_old` over
`12.5`.

**Tool names are matched to `algorithm` rows, and a miss stays NULL.** Matching
normalises case and punctuation and folds `π` to `pi`, against both
`algorithm.name` and `algorithm.aliases`. `TOOL_ALIASES` in the builder carries
the four that cannot match that way, with the reasoning for each; `gcnovo` to
`Denovo-GCN` is the one that is an identification rather than a spelling, and it
rests on the container being DeepNovoV2's code plus a `model_gcn` module.
`casanovo-scaling` is deliberately unmapped: it is a scaling experiment over a
24-dataset subset, not a released tool. An unmatched tool keeps its upstream
name and simply has no link, which is visible; a wrong guess would not be.

**A partial tool is in the heatmap and out of the ranking.** Ranking a tool that
ran on 24 of 84 datasets against tools that ran on all of them would be
meaningless, so the two cross-dataset charts filter on
`n_datasets = the dataset count` while the heatmap shows every cell that exists.
Missing cells are drawn grey, because on a white-to-teal scale an absent run and
an AP of 0 are the same colour.

**The numbers will not match the manuscript** and are not meant to: its figure 1
was drawn over 29 datasets, and this is the 84 the repository holds now, with
newer containers. The site quotes the commit it was built from for exactly that
reason.

**Dataset grouping is editorial, twice.** `categorize()` is the upstream
visualisation PR's name-based categoriser plus three rules that empty its
`Other` bucket, giving 17 fine categories; `CATEGORY_GROUP` then folds those
into 8 coarse groups for the heatmap's column headers, because 17 groups over 84
columns leaves a group two columns wide and no room for its label. The fine
category survives in `benchmark_dataset.category` and in the chart's tooltip.

### The critical-difference diagram

The box plot says which method ranks better; the critical-difference diagram
([Demšar 2006](https://www.jmlr.org/papers/volume7/demsar06a/demsar06a.pdf)) says whether the ranking is evidence. Both are computed in OJS
from the same per-dataset ranks, and the statistics are worth knowing because
they are easy to get subtly wrong:

- **Friedman first.** The post-hoc comparison is only licensed if the omnibus
  test rejects "all methods are equivalent", so the section reports χ² and its
  p-value. The p-value needs a chi-square upper tail, which `chi2_sf()`
  computes as the regularised incomplete gamma, series below the crossover and
  continued fraction above it. Quoting a statistic without its p-value leaves
  the reader to look up a table.
- **`CD = q_alpha * sqrt(k(k+1)/6N)`**, with q from Demšar's table 5(a) for
  alpha = 0.05, which is a TABLE and not a formula. It is inlined in
  `bench_cd`, indexed by the number of methods. 17 methods over 84 datasets
  gives CD = 2.69 rank positions.
- **A bar is a failure to separate, not a finding of equivalence.** That is the
  misreading the diagram invites, and the prose says so. So does the other one:
  a rank discards the SIZE of a difference, so losing every dataset by 0.001 AP
  ranks exactly as badly as losing by 0.3.

`fill: "family"` on a mark whose data has no `family` field renders **nothing**,
silently: the datum is dropped rather than drawn in a fallback colour. The
diagram's leads appeared and its dots and labels did not, which cost an hour,
because `bench_cd.tools` was built by `d3.rollup` and carried only the name and
the mean rank.

## ProteoBench

`build_proteobench.py` reads
[Proteobench/Results_denovo_lfq_DDA_HCD](https://github.com/Proteobench/Results_denovo_lfq_DDA_HCD),
one JSON per submitted run, and fills four tables: `proteobench_submission`
(7 rows), `proteobench_metric` (28 = one per submission, level and match
definition), `proteobench_curve` and `proteobench_source`.

It is a different benchmark from denovo_benchmarks, not a second opinion on the
same one: **one** dataset, the published nine-species benchmark of 779,879
spectra, with each run's parameters recorded alongside its numbers. A submission
is therefore evidence about a checkpoint and its settings, not about a method at
its best.

**Four metrics that are easy to confuse**, all defined in ProteoBench's
`proteobench/datapoint/denovo_datapoint.py`:

| | |
|---|---|
| `precision` | correct / predictions MADE, at whatever coverage the tool chose |
| `recall` | correct / all spectra, i.e. precision at full coverage with an unanswered spectrum counted wrong |
| `coverage` | predictions made / all spectra; can exceed 1 at amino-acid level, where the denominator is the ground truth's residue count |
| `auc` | area under the precision-coverage curve |

**Which one you pick changes the ranking**, which is the substance of
ProteoBench's own design discussion
([#356](https://github.com/orgs/Proteobench/discussions/356)). π-PrimeNovo has
the best peptide-level precision of the seven runs and answers 88% of the
spectra; by AUC it is fourth. Precision alone rewards a tool for keeping quiet,
which is why that discussion settled on AUC as the default and argued for
precision@coverage=1 alongside it. The site's metric toggle offers all three and
says this.

Each metric comes at two levels (peptide, amino acid) and under two match
definitions: `mass`, where residues count as correct within 0.1 Da so I/L are
indistinguishable, and `exact`, which requires the sequence. Mass-based is
always the higher number.

**The scatter is ProteoBench's own main plot**, reproduced: the same metric at
peptide level against amino-acid level, on fixed `[-0.05, 1.1]` axes split at
0.5 into the four quadrants it names (Good performance, Near-miss, Low
performance, Alternative candidate). Not reproduced: its background gradient
from light at the origin to dark in the top-right, because on this page the
colour channel carries the architecture family and two colour encodings in one
frame is one too many.

**Curves are strided, not interpolated.** ProteoBench stores up to 500 points
per curve; `subsample()` keeps at most 101 of them at an even stride, first and
last included. The other builder interpolates onto a shared grid instead, which
is wrong here: these curves do not all start at coverage 0 (one tool's
amino-acid curve starts at 0.028, which the design discussion flags), and
interpolating would invent values below a curve's first point rather than leave
the gap visible.

## Performance in release order

A section on the site, right after **How they score**: every number the papers
print on four shared datasets (nine-species, seven-species, HC-PT, and DIA's
OC/UTI/plasma), one **equal-width column per method**, the methods in order
of release date (the earliest paper describing each). It was a time axis.
That put PEAKS (2003) a decade before everything else and squeezed the field
into its last years, and even a broken axis left 2024-2026 crowded. So the
order now carries the chronology, with a faint rule and the year wherever the
year changes, and the date stays in every tooltip. The method labels are
vertical: at an angle their boxes overlapped, which `check_chart_overlap.py`
counts. The section was called *Performance over time* and was renamed once
the axis stopped being time. Its heading keeps the old anchor,
`{#performance-over-time}`, so links already pointing at it still land. denovo_benchmarks -- the living proteomics benchmark,
whose peer-reviewed results paper is not out yet -- is laid over it per species
and as the mean of the nine; ProteoBench as one pooled point under *Average*.

- **One nine-species version at a time.** A "Nine-species version" checkbox
  list defaults to *original (DeepNovo, 2017)*, where most papers report.
  Benchmark points carry their own versions: denovo_benchmarks is *revised
  (main)*, because its `datasets_info.py` names MSV000090982, the revised
  deposit; ProteoBench is *ProteoBench selection*. Each shows when its version
  is ticked. A "Comparing across versions" warning appears whenever the
  visible points span more than one. Measured: 12 points by default; 40
  paper, 17 denovo_benchmarks and 7 ProteoBench with everything ticked.
- **A number several papers print is one point**, its tooltip listing every
  paper, table and page it was seen in. Seven-species' 371 printed values are
  143 points: most are NovoBench's, quoted again.
- **A benchmark appears only on the metric whose definition it shares**:
  AP on AUC, `prec_full_peptide` on peptide recall and precision,
  `prec_full_aa` on amino-acid precision. Casanovo's "Prec. at Cov.=1" is
  filed as peptide recall, which it is by definition, and says so in the hover.
- **The gap between the papers and the benchmarks is the finding**, and the
  prose computes it live: the number most papers print for a method
  (NovoBench's) against denovo_benchmarks' released checkpoint. It runs both
  ways -- DeepNovo lower released, because its weights are old; InstaNovo far
  higher, because its checkpoint saw far more data -- which is the
  retrained-versus-released trap made visible.
- **A merged point's basis can be `mixed`, and that is real.** Papers disagree
  about the same number: one calls InstaNovo's 0.164 "provided by NovoBench",
  another marks it its own retraining. So `mixed` cannot be filtered on; the
  interpretation picks each method's MOST-PRINTED value instead.

Three Plot traps, all silent:

- **A stroke-only symbol vanishes under a white outline.** 'version not
  stated' was drawn as `times`, which Plot draws by stroke alone. With the
  dots' white outline a lone one was invisible, so PLMNovo's column looked
  empty, and the stray x elsewhere showed only where it overlapped a circle.
  Every shape in the version scale is now a FILLED symbol (`cross`).

- **A symbol value outside the symbol domain drops the mark.** The version
  shape is meaningful only on nine-species; HC-PT points carried version
  `HC-PT (NovoBench)` and drew NOTHING, with no error, until the symbol became
  a constant off nine-species. Same family as `fill: "family"` on a field that
  is not there.
- **`r` is a scale.** A radius computed per point is squeezed into Plot's
  default range unless the plot declares `r: {type: "identity"}`; without it
  every paper point drew at about 2 px.

Tooltips are the `.map-tooltip` div bound by DOM order, one Plot.dot mark per
source, with `sort: null` so a mark's elements stay in data order (Plot
otherwise sorts dots by radius). Verified in headless Chrome: all four datasets
draw, 0 OJS errors, and a hover on each of the three marks shows its provenance.

## Benchmark numbers on the method pages

The 17 methods with benchmark results carry a `## Benchmarks` section on their
page: median AP and median rank over the 84 datasets, plus the ProteoBench AUC
and precision for the 7 that have a submission.

**Each benchmark line names its data and links to it.** "over 84 datasets"
links to a list of all 84 on the page of denovo_benchmarks' own deposit
(MSV000096182, dataset *De novo peptide sequencing tools benchmark*), section
`#denovo-benchmarks-datasets`, built from `benchmark_dataset`. The ProteoBench
line says it runs on the nine-species benchmark and links to that dataset's
*ProteoBench selection* version (`#proteobench-selection`). The main page's
*How they score* intro links the same two places, with the dataset count read
from the data rather than written in.

These ARE baked into the pages, unlike repository stars. The rule is how often
the number moves: stars change daily, so putting them on a page would rewrite
the whole page set nightly for nothing, whereas benchmark results change when an
upstream repository commits new runs and the refresh workflow only commits when
they actually changed. And a week where they do costs 17 page renders, not 2485,
because of the next section.

## "Recently added" is publication.id, not a date

The site's **Recently added** list, first thing under Browse all papers, answers
"what changed since I last looked" without the catalog storing a second date per
row. `publication.id` is handed out by SQLite in insertion order, so ordering by
it descending IS the order papers arrived; the same id is the sortable `#`
column in the full table, which is how to see past the three the list shows.

**It shows three, and every link that means "the papers" skips it.** The list
sits directly under the `Browse all papers` heading, so `#browse-all-papers`
landed a reader on recent arrivals rather than on the table the link promised.
The navbar's `Papers` entry, the hero `papers` counter, the kind-breakdown rows
and `ANCHORS["browse-papers"]` in `build_pages.py` -- which every generated
page's footer uses -- now all point at **`#every-paper`**, the table's own
heading. Three places still point at `#browse-all-papers` and should: the
section's heading anchor and its TOC entry, which are navigation TO the section,
and nothing else. The `Browse all authors` link needs no equivalent, because
that section opens on its table.

The prose above the list reads its count back out of the array, so shortening it
again cannot leave the text claiming ten.

Two things to keep straight. The date shown beside each entry is the PAPER's
publication date, not when it was catalogued, and the list says so, because
several additions each month are older work that surfaced in a
`build_candidates.py` sweep. And the highest id can exceed the row count, since
a deleted row does not give its id back -- 918 against 917 publications today --
so the id is an ordering, never a count.

A real `added_at` column would be better and is not worth it: the value only
exists in git, backfilling it means an `-S` search per row over the whole
history, and every future insert would have to remember to set it. The ordering
is free and cannot drift.

**The table itself sorts on the full DATE, descending, not on the year.**
`Inputs.table`'s sort is stable, so sorting on a year leaves the rows within
each year in the array's own order -- here the SQL's
`ORDER BY p.publication_date`, oldest first. The table therefore opened on
January 2026 and a paper added today landed sixty rows below it, in a column
that said 2026 all the way down. The column is now the ISO date as a STRING,
which sorts lexicographically, matches "2026-09" in the search box, and needs no
formatter. The BibTeX export still reads `year` off the row, which is in the
data whether or not it is a column.

## The publish renders what changed, not everything

`render_scope.py` decides, before Quarto is invoked, whether the publish needs
**full**, **partial** or **index** scope. A full render is 1017 seconds and
98.7% of the job, essentially all of it the ~2485 generated pages at ~0.4s each;
index.qmd alone is 13.5s.

| mode | when | what runs |
|---|---|---|
| `index` | no generated page changed | `quarto render index.qmd` |
| `partial` | 1 to 100 pages changed | index.qmd, then one `quarto render` per changed page |
| `full` | Quarto version, `_quarto.yml` or `custom.scss` changed; no usable `_site`; more than 100 pages changed; or the workflow's `full_render` input | `quarto render` |

`index` is the common case: build_pages.py keeps volatile metrics out of the
pages, so the daily repo-metrics commit changes zero of them. `partial` is the
case this section is about, and the two that produce it are a new paper and the
weekly benchmark refresh, which touches the 17 method pages carrying a benchmark
line and nothing else. Authors, publications, institutions and venues are not
affected by a benchmark number, so they are not re-rendered.

**`_site/.render-manifest`** is how one run tells the next what it built: the
Quarto version plus `_quarto.yml` and `custom.scss` in one "global" hash, and
then a hash per page keyed by path. Path as well as content, so a slug rename
registers as a removal and an addition rather than as nothing. It lives inside
`_site` because gh-pages carries it to the next run, the same trick that makes
the restore step a free and exact cache. It replaced `.render-key`, a single
hash over everything, which could only answer "did anything change".

**One `quarto render` per file, because `quarto render a.qmd b.qmd` silently
renders only the first input.** That is the kind of thing that ships as "the
partial render works" while quietly publishing stale pages, so it is worth
saying twice. The per-file cost is ~6s in CI against ~0.4s inside a project
render, which is what sets the `MAX_PARTIAL = 100` cap: past that the full
render is both faster and more predictable.

**All three modes have now run in CI**, which is where the per-file number above
comes from:

| mode | run | wall clock | what it rendered |
|---|---|---|---|
| `full` | 36714122487 | 17m41s | everything, 2521 pages |
| `partial` | 36716139226 | 3m48s | index.qmd + 24 family pages, 192s in the render step |
| `index` | 36709905609 | 1m07s | index.qmd, 2497 pages byte-identical |

192s for index.qmd plus 24 pages puts a page at roughly 6s once index.qmd's own
~40s is taken out, so the 100-file cap costs about 10 minutes against 17m41s for
the full render. Break-even is nearer 160 files; the cap stays at 100 because a
change that size is rare and the margin is worth more than the minutes.

**The full render grows with the catalog, and the job's timeout has to grow
with it.** At about 0.4 s per page the 17 minutes above was for 2521 pages;
the citation sweep took the site to 6282, about 42 minutes, and the job's old
`timeout-minutes: 30` cancelled run 37236776725 at page 4370, leaving the
live site on the previous publish. It is 90 now. Re-check it whenever a bulk
import adds pages: the slug count `slugs.py --check` prints is the number to
multiply by 0.4 s.

A **full** project render empties `_site` and repopulates it at the end, so the
directory is bare for the whole 17 minutes; a partial render writes into
whatever is already there. The restore step therefore matters only to the two
fast paths, and the manifest is recreated after every render rather than carried
through one.

**What a partial render leaves alone**, all verified rather than assumed:

- the other pages in `_site`, which keep their bytes;
- `sitemap.xml`, which Quarto MERGES into rather than rewrites: it keeps all
  2449 entries and refreshes the ones it rendered. It only merges when the file
  is already there, though. Delete it and an index-only render produces a
  sitemap with **one** URL, which is the second reason the workflow restores
  `_site` from gh-pages before rendering anything;
- `search.json`, which never carried the generated pages anyway
  (`search: false` in their metadata), so nothing there can go stale.

**A removed page forces the full render**, and the reason is the sitemap rather
than the page. Since Quarto merges into an existing `sitemap.xml`, neither fast
path can drop the entry, and the restore brings both the entry and the orphaned
`.html` back on the next run; only a full render, which empties `_site` and
writes the sitemap from what it produced, forgets a page. Removals are rare and
deliberate -- `slugs.py --check` fails the commit on one -- so 17 minutes is the
right price. Before the manifest this happened by accident, because any change
to the single key meant a full render anyway.

## Publication dates

`publication.publication_date` is a full `YYYY-MM-DD` string with no NULLs, so a
date must always be produced even when the source has coarser precision. Two
conventions, both reverse-engineered from the existing rows rather than written
down anywhere, and worth following so the timeline stays comparable:

- **Which date.** For a journal with a print issue, use the **issue** date, not
  the online-first date. Of the sampled rows where Crossref's `published-online`
  and `published-print` disagree, 6 of 7 carry the print date. For an online-only
  journal (BMC, PLOS, Frontiers) the online date *is* the publication date, and
  any nominal "issue" it is later bundled into can postdate the article by
  months: Proteome Science 8:24 went online 2010-05-10 but sits in a Dec 2010
  issue.
- **Coarser precision.** Month-only sources get `YYYY-MM-01`; 394 rows use day
  `01` and 354 of those are in non-January months, so a first-of-the-month date is
  normal here and not a red flag by itself.

**The trap:** OpenAlex reports `publication_date` as `YYYY-01-01` whenever it
holds only year precision. Copied in as-is that is indistinguishable from a real
1 January, and it silently backdates a paper by up to a year: Chemical Science
16(39) read as 2025-01-01 when it was published 2025-09-04. Four such rows were
corrected (66, 153, 169, 171, plus thesis 57 from UWSpace's own metadata) by
going to the publisher, Europe PMC or the repository instead. No builder writes
`publication_date`, so these stay fixed, but hand-entry from an OpenAlex record
can reintroduce it. When a source gives only a year, prefer the publisher page,
Europe PMC `firstPublicationDate`, or a repository's `citation_publication_date`
before settling for `YYYY-01-01`.

**The other trap is a thesis date on the journal row.** Publication 50,
NovoRank, was dated 2022-08-01 while its DOI is `10.1021/acs.jproteome.4c00300`
-- an ACS `4c` identifier, which means 2024. Crossref gives J Proteome Res
24(2), 903-910, online 2024-12-31, print issue 2025-02-07. The 2022 date was
Jangho Seo's master's thesis of the same work, which is now publication 359 in
its own right, and the row was carrying its author's thesis date for two and a
half years of the timeline.

**The ACS DOI suffix is the tell**: `4c` is 2024, `5c` is 2025, the same way an
arXiv `25xx` id dates a posting. When a row's date and its DOI disagree by more
than a few months, the date is usually a different publication's. Note the
method itself keeps its 2022 first appearance, because `first_pub` takes the
earliest DESCRIBING paper and the thesis is now that paper -- which is the
honest reading, and a reason to enter the thesis rather than just fix the date.

**A year-only date can come from Crossref too, and SSRN is where it bites.**
The OpenAlex trap above has a Crossref twin: for publication 432, LIPNovo+ on
`10.2139/ssrn.6890054`, Crossref's `posted` and `issued` both read `[[2026]]`,
year precision and nothing more, which copied in becomes 2026-01-01. The
documented fallbacks do not help here either, because SSRN has no Europe PMC
record and its own abstract page answers **HTTP 403** behind Cloudflare, so
there is no publisher page to read a real date off. What Crossref does carry is
`created`, the DOI registration timestamp, 2026-06-06, which for an SSRN
posting is the deposit that accompanies it. That is the date the row holds, and
it beats 1 January by five months of timeline. **Prefer `created` over a
year-only `posted` whenever the publisher page cannot be read**, and note it is
an upper bound on the posting rather than the posting itself.

Five rows legitimately keep 1 January (146, 158, 187, 286 and 310: Mass Spectrometry
Reviews 34(1), Mol Cell Proteomics 8(1), AIChE Journal 53(1), J Biol Chem 279(1),
Biomedical Chemistry: Research and Methods 1(1)) because each really is a
January issue. Two theses keep a year-only 1 January because their repositories publish no
month: publication 196, whose Digital Commons ETD record gives "Date of Award
2013", and publication 339, whose figshare-backed record at Victoria University
of Wellington gives "Date of Award = 2020-01-01" as a literal placeholder. Note
that record's `published_date` is 2023-09-26, which is when the repository
deposited the thesis, not when it was awarded.

## Preprint versions

Normally one `publication` row per preprint, with `url` pinned to the version
whose metadata the row reflects (InstaNovo's points at `v3`). BiATNovo is the
exception and the worked example: bioRxiv v1 and v2 differ in BOTH title and
author list (5 authors, one of whom is dropped, versus 10), so each version gets
its own row and its own `publication_author` byline. `publication.version`
distinguishes them, the same column that separates Casanovo v1 from v2.

Two traps if you ever do this again:

- **The DOI must go on the EARLIER row.** bioRxiv mints one DOI for all versions
  (the versioned form `10.1101/...540352v1` is a Crossref 404) and
  `publication.doi` is UNIQUE, so only one row can hold it. Citation harvesting
  resolves the DOI to whichever row holds it, and papers cite a preprint from
  before a later revision exists. With the DOI on the later row,
  `build_citations.py`'s `is_valid_citation()` discards those edges as citations
  of a future paper, silently and with no audit entry, on every monthly rebuild.
  Anchored on the earliest row every edge stays valid. The cost is that the
  newer row shows no DOI and no OpenAlex count, which is the right place for
  them anyway: both describe the preprint as a whole.
- **Do NOT link the versions through `publication_version`.** That table drives
  the Publication-lifecycle chart's preprint-to-journal gap; a v1-to-v2 pair
  would render as a preprint that took 17 months to be "published" when neither
  version is. The versions are already linked by sharing an `algorithm` row plus
  distinct `version` labels, which is how the architectures swim lane groups
  them.

Note this makes `n_preprints` count both versions, exactly as it already counts
Casanovo's several preprints.

## Abstracts

`build_abstracts.py` fills `publication.abstract`, trying bioRxiv, arXiv,
**Europe PMC**, OpenAlex and Crossref in that order and recording which one won
in `publication.abstract_source`. A NULL `abstract_source` alongside a non-empty
`abstract` means the text was entered by hand and is authoritative: the script
skips those rows unless `--force`, so don't pass `--force` casually.

Coverage is 852/917, of which 5 came from the PDFs themselves via
`build_pdf_abstracts.py` (`abstract_source = 'pdf'`). Four more carry
`abstract_source = 'proceedings'`: the ICML and NeurIPS rows 438 to 441, whose
abstracts were copied from the proceedings page itself.
The 65 without one are mostly theses, conference pages and records with no DOI,
where neither an API nor the PDF yields a clean abstract.

**`build_pdf_abstracts.py` rejects more than it accepts, 13 of 18, and the
rejections are the point.** Lifting an abstract out of a PDF fails in ways that
look like success:

- publication 254's two-column layout puts "Abstract" and "1. Introduction" on
  the SAME physical line, so a heading search lands in the wrong column and
  returns the introduction, which reads perfectly and carries five citations;
- publication 20 ran past the abstract into Chinese margin annotations;
- 55 stopped mid-word at "backed by high-", 258 carried on into the
  introduction, 318 finished on a page number ("a special de ii");
- `-layout` leaves a hyphen plus a SPACE where it joins two columns, giving
  "Data-Independent Acquisi- tion" and "the pep- tides", and ICLR margin line
  numbers survive inside the line as zero-padded triples.

The first guards -- starts with a capital, 200 to 4000 characters -- passed all
four of those. What catches them is checking the END: a real abstract finishes
on a sentence terminator. With that, plus no CJK, fewer than three inline
citations, no Introduction heading and no surviving line numbers, 11 apparent
successes came down to 5 correct ones. Loosening any of them puts a
plausible-looking fragment in the database, which the rule above already says
is worse than no abstract at all.

Europe PMC is asked before OpenAlex on purpose. OpenAlex reassembles an
inverted index that, for Nature-family journals, has the journal's separate
one-sentence editorial summary glued onto the abstract with no marker to cut
on: Nat Commun 15 on `10.1038/s41467-024-53105-8` returns 1470 characters
against a real abstract of 1151, the extra ending "Here the authors
present...". Europe PMC serves the abstract alone. It does answer 200 with an
empty `resultList` rather than 429 when pushed, which is indistinguishable from
"no record", so `from_europepmc` retries once.

`strip_publisher_extras()` removes the rest: Liebert and SAGE journals keep a
promotional `Teaser:` field that Crossref AND Europe PMC both concatenate onto
the abstract (Astrobiology 23:657 arrives 291 characters too long), plus two
format artifacts, a space left inside a bracket by tag stripping ("( e.g.,")
and trademark symbols. Between them the script now reproduces both
hand-corrected abstracts byte for byte, and alters none of the other 240.

Two guards worth knowing about, because both were hit in practice:

- OpenAlex delivers abstracts as an inverted index (word -> positions) that has
  to be reassembled in position order, and some records carry only a PARTIAL
  index. That reassembles into a fragment starting mid-sentence, so any
  candidate whose first character is lowercase is rejected and the next source
  is tried. A fragment presented as an abstract is worse than no abstract.
- arXiv DOIs are minted as `10.48550/arXiv.2512.12272`, but the API's `id_list`
  wants the bare `2512.12272`. Leaving the prefix on returns an empty feed
  rather than an error, which silently falls through to OpenAlex.

## Affiliations, and why the ROR matters

`build_affiliations.py` was the catalog's last unautomated entity. Until it
existed, `affiliation`, `author_affiliation`, `city` and `country` were the only
entity tables with **no writer at all**: every institution, department, city and
country was typed in by hand from a byline. Nine of sixteen tables had no
builder; this closes the largest gap.

The data was already being fetched and discarded. `build_author_ids.py:135`
loops `authorships[]` reading only the author fields, and drops
`institutions[]` and `raw_affiliation_strings` on the same matched authorship.

**Identity is the ROR, not the name.** The apparent disagreement between
OpenAlex and the curated rows is two facts in one column:

| column | meaning | shown on the site |
|---|---|---|
| `affiliation.name` | as PRINTED on the paper | yes |
| `affiliation.canonical_name` | current legal name (OpenAlex) | no |
| `affiliation.ror` | identity | no |

"Swiss Federal Institute of Technology" is what a 1999 byline says; "Ecole
Polytechnique Federale de Lausanne" is what EPFL is called now. Keyed on the
ROR they stop competing and the site keeps saying what the paper said. Note
`ix_affiliation_ror` is deliberately **NOT unique**: a ROR identifies an
institution, but this table's grain is (institution, department), and Technical
University of Munich alone has ten rows.

**Resolution is tiered, and the tiers are not equally trusted.** Tier 1 matches
a known ROR, tier 2 an exact normalised name, tier 3 our as-printed name
appearing verbatim in the paper's own affiliation string. Tier 3 establishes
which of our rows a byline belongs to, but **must never write a ROR**: one raw
string routinely names a company and a funding body, so trusting it bound
"Baizhen Biotechnologies Inc." to the Wuhan Science and Technology Bureau's ROR
and "Talus Bioscience" to TRIA Bioscience's. That was caught and reverted; the
guard is `if ror and tier in ("ror", "name")`.

**Departments are parsed, so they are never auto-written.** OpenAlex has no
structured department field. Because `affiliation` is UNIQUE(name, department),
a wrong department does not mislabel a row, it creates a duplicate institution
and pollutes both the institution page set and the geo chart. Candidates go to
`affiliation_audit.csv` for a human.

**Per-byline, not per-author.** `author_affiliation` is author-level, so joining
`publication_author -> author_affiliation` returns every institution an author
was ever given rather than the one on that byline, which makes any per-paper
geography claim wrong for the authors who have several. The new
`publication_author_affiliation` table records the precise relation; the
curated author-level rows are left untouched.

Results of the first full run: 1922 per-byline rows, 427 of 621 affiliations
given a ROR, and city coordinates filled from `/institutions/{ror}` geo, taking
the cities missing lat/lng from 106 down to 27. 818 findings went to the audit
CSV, of which the largest group is institutions OpenAlex models as separate
entities where this catalog records them as departments of a parent (Novo
Nordisk Foundation under DTU, Science for Life Laboratory under KTH, Academy of
Mathematics and Systems Science under CAS). Those are correct refusals, not
failures.

## The local PDF library

`build_pdf_library.py` maintains a folder of paper PDFs beside the catalog:
`report` says what is missing and why, `fetch` downloads what a source states
is free, `ingest` files hand-downloaded PDFs, `rename` re-derives every
filename from the catalog and `dedupe` drops byte-identical copies. It writes
`pdf_status.csv` and `missing/*.txt` into the LIBRARY, never into the repo, and
touches no table.

**ADDING A PAPER INCLUDES FETCHING ITS PDF.** After the insert, run

    python3 build_pdf_library.py fetch --ids <the new publication id>

which writes the file straight into the library root, Zotero-named. When
nothing free can be fetched, run `report`: the paper lands in whichever
`missing/*.txt` describes why, and THAT is the expected outcome rather than a
failure. The two lists to read it in are `blocked-but-open-in-pmc.txt`, meaning
a free copy exists and only a browser can take it, and `paywalled.txt`, meaning
no source reports one at all.

**Filenames carry the FULL title**, in the catalog's own casing, with nothing
cut. The old `[:95]` came from what Zotero happens to export and cost
information on 154 of 359 papers; the longest full name this catalog can
produce is 223 bytes, well inside the 255-byte `NAME_MAX`, so the length guard
in `zotero_name()` is for a hypothetical future title and fires on nothing
here. Renaming the library to full titles touched 103 of 231 files.

**A twin pair gets a `(preprint)` suffix.** A preprint and its version of
record with the same normalised title, year and first author produce the same
filename, and that cost a real file: fetching LIPNovo's ICML PDF overwrote the
arXiv one already in the library. So `load_publications` flags such pairs as
`twin`, `zotero_name` appends ` (preprint)` (or ` (postprint)`) to that side,
`identify` honours the marker, and `fetch` never overwrites an existing name.
**An unmarked file is not proof of the version of record**: a preprint filed
before its twin existed carries no marker, and the first version of this rule
proposed renaming four bioRxiv and ChemRxiv files onto their journal twins'
names. `identify` now reads the first two pages for a preprint banner
(bioRxiv's, ChemRxiv's, arXiv's `arXiv:2602.20209v1 [q-bio.QM]` margin stamp,
which unlike a bare arXiv id cannot come from a reference list).

That re-attributed two papers' mined tables. GA-Novo's and the
regressor-guided diffusion paper's comparison tables had been read from their
arXiv files while carrying the journal rows' ids (152, 7); neither journal row
has a PDF of its own, so the 11 sign-offs were re-keyed to the preprint rows,
224 and 222, along with GA-Novo's registry entries.

**Two tie-breaks, and the first one was wrong.** Where two rows differ only in
a title's capitalisation or punctuation -- publication 108's "NovoBoard: a
comprehensive framework" against 80's "A Comprehensive Framework", 29's
"data-independent" against 103's "data independent" -- `title_score`
normalises the difference away and the pick is arbitrary. The first fix
preferred "whichever candidate the current filename already matches", which
tied the answer to the name: dropping the truncation changed every name, and
four files started matching the other row of their pair, so `rename` proposed
lateral moves it could never apply because the target was taken. It now breaks
the tie on the EXACT characters of the title against the filename's title text,
then on the lower id -- a rule that reads only the title and so converges
wherever it starts. Verified: `rename` reports 0 on the second pass.

**`dedupe` checks the publication as well as the hash.** Byte equality is too
strict for the same paper twice: bioRxiv served one preprint to two fetches 26
bytes apart, a timestamp inside the PDF, so two copies each of Sanders 2024 and
pi-PrimeNovo 2024 sat in the library with different hashes while `rename` could
only report a rename it could never apply. Files are now also grouped by the
publication they resolve to, and that group is REPORTED rather than deleted: a
preprint and its version of record legitimately give one paper two files under
two rows, and choosing between two copies of one row is a human's call.

**The library lives at `~/Documents/de_novo_peptide_sequencing`, its PDFs in
`pdfs/`.** The root holds only the working folders beside them (`manual/`,
`missing/`, `supplements/`, `comparison-review/`, `citation-sweep/`) and
`pdf_status.csv`. Every script takes the root from
`build_pdf_library.DEFAULT_DIR` and finds PDFs through `pdfs()`, which still
reads a PDF left at the root or in `retrieved/`, and every writer goes through
`pdf_dir()`. Three scripts used to hard-code the old path with spaces in it.

**The library is ONE flat folder.** There used to be a `retrieved/`
subdirectory separating downloads from the owner's own Zotero exports. Once the
same generator named every file and `pdf_status.csv` recorded the provenance,
the split only hid half the library from `rename` and `dedupe`, each of which
looked in one folder and not the other. `pdfs()` still reads `retrieved/` if it
exists, so an older layout keeps working, but nothing is written there.

**It must never run in CI.** There is no library on a runner, and a full
`fetch` is a few hundred HTTP requests. `pypdf`, needed only for slicing, is
deliberately not a project dependency for the same reason; the command line
above passes it with `uv run --with`.

Four things in it were learned the hard way and are worth not re-learning:

- **Fetch through `curl`, not `requests`.** HTTPS on the machine this was
  written for is intercepted by a Perimeter81 Secure Web Gateway whose
  re-signed certificates carry no Authority Key Identifier, so Python rejects
  them with `[SSL: CERTIFICATE_VERIFY_FAILED] Missing Authority Key
  Identifier` while `curl`, against the *same* CA bundle, returns 200. The
  first run read that as the publishers blocking us: 36 downloads of 322, with
  140 filed under "open access but the download failed", including five theses
  hosted on this project's own domain. Switching transport took it to 101. If
  everything fails at once, suspect the transport.
- **Match a filename to a title prefix-against-prefix, cut to the shorter
  string.** A filename is a truncated title, and both obvious metrics are
  wrong in opposite directions: `partial_ratio` scores a short title sitting
  inside a long filename at 100, which filed DeepNovoV2's paper under
  publication 57 ("Peptide Sequencing with Deep Learning"), and
  `token_set_ratio` ignores unmatched tokens, so publication 315 ("De novo
  Peptide Sequencing", two words) scored 100 against 26 of 37 files. Where a
  preprint and its version of record share a title, the filename's year breaks
  the tie; `--map` handles the rest.
- **Take the first author from an ordered subquery**, never from
  `GROUP_CONCAT` with a trailing `ORDER BY` -- see the byline note under **The
  Quarto site**. It named 52 of 107 downloads after the wrong author.
- **Slice with `pypdf`, not `pdfseparate` + `pdfunite`.** The latter copies the
  volume's shared fonts onto every page: a 9-page slice of a 31 MB proceedings
  volume came out at 32.7 MB, against 0.3 MB from `pypdf` with
  `compress_identical_objects`.

**Only locations a source says are free are ever fetched**, and a paper with
nothing free is reported rather than worked around. PMC is not tried at all:
every automated route into it is shut by design, and it directs bulk users to
its FTP/cloud packages instead. `report` therefore lists a paper with an
open-access PMC copy as a **PMC link**, in `blocked-but-open-in-pmc.txt`,
because that link opens normally in a browser and is one click from the PDF.

**A browser User-Agent changes nothing here, measured.** `--browser-ua` sends
one, for open-access papers whose publisher refuses the default agent; it is
opt-in because misrepresenting the client is a choice worth making knowingly,
and it gets past bot detection rather than any paywall. Run over the 27
publisher-blocked papers it recovered **0**: ACS, Europe PMC's `?pdf=render`
and IEEE answered 403, 403 and 202 exactly as before, so the block is not the
agent. What that run did expose is the next entry.

**Europe PMC reports availability; OpenAlex reports that a deposit exists.**
Where they disagree, Europe PMC wins, and publication 360 is why. OpenAlex says
`is_oa` true, `oa_status` green, `oa_url` PMC13457817 -- and that PMC record is
**embargoed until 2027-08-10**, which OpenAlex's green status does not
distinguish from readable. Europe PMC says `isOpenAccess` N, `inPMC` N, `hasPDF`
N, no `pmcid`, "Subscription required": correct on every count. Of 27 papers
the report called "open access but blocked", **22** were that shape, free on
OpenAlex's word alone. So `epmc_verdict` overrides, and OpenAlex only speaks
where Europe PMC has no record.

Two incidental traps in the same place. OpenAlex puts `is_oa` under
`open_access`, not at the top level, so `work["is_oa"]` reads None for every
work. And a PMC id is NOT a readable copy: `pmcid()` used to grep `PMC\d{4,}`
out of the raw cached JSON, which put 360's embargoed deposit on the
"open in PMC" list a year before it opens, so it now requires Europe PMC to say
the text is actually there.

**The Europe PMC query must be URL-encoded**, and this is the worst bug this
script has had. The query needs literal double quotes around the DOI, and an
unencoded `"` makes Europe PMC answer **HTTP 400**. The first version passed
params through `requests`, which encoded them; switching the transport to
`curl` moved the URL into an f-string and broke every Europe PMC lookup from
then on. Silently: a failure is not cached, so it retried and failed again, and
an HTTP 400 was indistinguishable from "this paper has no record" -- the script
carried on deciding access from OpenAlex alone. The answers already on disk
predate the switch, which is why it surfaced only on a newly added paper.
`get_json` now PRINTS a failed lookup rather than returning something that
reads like an empty result.

**Read the PMC id out of the raw cached JSON, not from one field.** The first
`pmcid()` asked Europe PMC for `pmcid` and required `isOpenAccess == "Y"`, and
so missed publications 86, 91, 95 and 120 -- all four of which Europe PMC had
already offered an "Open access" PDF location for, with the PMC id sitting in
the URL. They were filed as publisher-blocked, the hardest bucket, when a PMC
link opens fine. Fixing it moved 24 papers from `blocked-publisher` (27 down to
2) into `blocked-but-open-in-pmc` (12 up to 36).

**An EMBARGOED paper is neither paywalled nor blocked, and saying so stops it
being chased.** `EMBARGOED` in the builder maps a publication id to a release
date and the publisher's own wording; `fetch` skips those ids and `report`
files them in `embargoed.txt` before any stored verdict is read, since an
earlier run will have recorded "paywalled", which is the wrong word when the
text is not for sale either. Recording the DATE rather than a flag means the
entry expires by itself: once it passes, the paper rejoins the normal buckets.

One entry so far, publication 119, the DiffNovo-DIA thesis. UNT's record says
"The contents of this dissertation are unavailable for full viewing on this
site ... It will be made available on this site on June 1, 2030", and the DOI
it offers as an alternative resolves back to the same embargoed record, so
there is no second route. This is the same shape as publication 360's
PMC deposit, embargoed to 2027-08-10, which the Europe-PMC-wins rule above
already handles because Europe PMC reports it; a repository embargo has no
such API to ask, so it is recorded here by hand.

**A paper a person has checked stays checked.** `CHECKED` in the builder
maps a publication id to a verdict, `no-pdf` (no full text exists or ever
will: this catalog's own Zenodo code deposit, the two abstract-only JSSR
records) or `paywalled` (sold, and no free copy found by hand). `fetch` skips
them and `report` files them before any stored verdict, `no-pdf` in its own
`no-pdf-exists.txt`, so the missing lists shrink to the work actually left
instead of re-proposing what the owner already looked at. The first hand
check also paid for itself the other way: publication 700, listed as
paywalled at IEEE, has the authors' own copy on HAL (`inria-00270867`), and
is filed rather than recorded. Check an open repository before writing a
paper off.

**Do not bucket on the last host tried.** Doing that produced a
`blocked-doi-resolver` list of 31 which the README then recommended as the
largest recoverable group, on the theory that OpenAlex had offered `doi.org` as
their PDF location. It had not: `doi.org` was simply the record's own `url`,
tried LAST after the real open-access candidates failed. Following the DOI and
reading the landing page's `citation_pdf_url` recovered **0 of 31** -- ACS, OUP
and MDPI answer 403 at the landing page itself, and Wiley and Elsevier's
linkinghub answer 200 with no such tag. The bucket now keys on whether an
open-access copy is known to exist, which is a fact about the paper rather than
an artefact of the attempt order.

## The OpenAlex key, and the Space that watches the literature

`openalex_key.py` reads `OPENALEX_API_KEY` from the environment or from a
gitignored `.env`, and `with_key(url, params)` adds it **only** when the
request is going to `api.openalex.org`, so a builder that also calls Crossref,
Europe PMC or ORCID through the same helper cannot leak it to them. Wired into
`build_publication_impact.py`, `build_abstracts.py` and
`build_affiliations.py`. Absent, nothing raises: every builder worked without a
key before and still does, so an unkeyed clone is slower, not broken.

The key exists because OpenAlex's anonymous pool is a daily budget shared
across every caller from one IP, and a heavy day exhausts it.
`build_publication_impact.py` hit that mid-run after a bulk paper import: it
aborted on ten consecutive empty lookups, which is the right behaviour -- it
preserved the ids it had rather than writing nothing over them -- but the new
papers were left with no `openalex_id`, and **`build_affiliations.py` silently
skips any publication without one**. That ordering is the thing to remember:
impact first, then affiliations.

### BioGeek/denovo-radar

<https://huggingface.co/spaces/BioGeek/denovo-radar> is a Gradio Space showing
recent de novo peptide sequencing papers with each one marked against this
catalog, so the gaps are visible. `refresh-denovo-radar.yml` runs its
`harvest.py` weekly, commits `data/papers.json` into the Space, and commits
nothing here.

**The harvest runs in CI, not in the Space, and that is the design.** A Space
that schedules its own work writes into container storage: the results never
reach its git repo and do not survive a restart. The feed that inspired this
one does exactly that, which is why its published data stopped moving while the
Space itself kept running.

Two secrets are needed and this repo cannot create them: `HF_TOKEN` with write
access to BioGeek, and optionally `OPENALEX_API_KEY`. The workflow fails with
an explicit error rather than a confusing one when `HF_TOKEN` is absent.

**The filtering is the point, not the fetching.** A keyword feed for "de novo"
is mostly a different field: of 682 records fetched on the first run, 551 were
not de novo sequencing of a peptide at all, 14 were Zenodo or MassIVE deposits
of model weights and datasets (which match the topic perfectly and are not
papers, and one of which was this catalog's own Zenodo record), 5 sequenced
another analyte and 1 was de novo design. 45 survived, 35 of them already
catalogued, leaving 10 new. Versioned DOIs are collapsed (`10.17044/x.v1` onto
`10.17044/x`) and the catalog is matched on title as well as DOI, since the
same paper arrives under an arXiv DOI in one source and a conference DOI in
another.

**Pin gradio, and pin it forward.** The Space's `requirements.txt` asked for
`gradio>=4.44` and let pip resolve `huggingface_hub` freely, which is the one
combination that cannot work: gradio 4.x's `oauth.py` does `from
huggingface_hub import HfFolder`, and `huggingface_hub` 1.0 removed `HfFolder`,
so the Space died at `import gradio` before reaching a line of its own code.
It is pinned to `gradio>=6.29,<7` on python 3.12 rather than to an old
`huggingface_hub`, because 6.x does not import `HfFolder` at all and so stays
installable as the hub moves. `sdk_version` in the README frontmatter has to
agree: it is what the builder installs, and `requirements.txt` cannot override
it downwards.

**The list is a FEEDBACK LOOP, and closing it needed both files.** The Space
was re-proposing work already argued about: a paper added to the catalog stops
being new by itself, because `harvest.py` matches the catalog by DOI and
normalised title, but a paper REJECTED has no catalog DOI and came back every
week. `refresh-denovo-radar.yml` now passes `--watchlist ../WATCHLIST.md` as
well as `--catalog ../denovo.db`, and rejections are dropped by DOI, the same
contract `build_candidates.py` has. **Matching is DOI-only on purpose**: a
watch-list entry argues about a paper in a sentence, and fuzzy-matching those
sentences against titles would drop papers nobody rejected. An entry with no DOI
is not enforceable there, which is a reason to record one when rejecting
something.

**Name the repository that PUBLISHES a checkpoint, not where you found it.**
Both π-PrimeNovo checkpoints were first credited to the ProteoBench discussion
that led me to them; they are published by `PHOENIXcenter/pi-PrimeNovo`, the
MassIVE model in its top-level README and the phosphorylation model in
`pi-PrimeNovo-PTM/`, both MIT. The repository also described the model better
than the thread did: fine-tuned from `model_massive.ckpt` on 2020-Cell-LUAD,
predicting one extra token `B` for Phosphorylation (+79.97). A provenance table
that cites a discussion thread is recording my search history, not the
provenance.

**Fixing that exposed a worse bug: the harvest REPLACED the list.** Two runs a
week apart fetched 682 and 435 records over the same window, because neither
Europe PMC nor OpenAlex returns a stable set, so an overwrite would have
silently dropped twenty papers from the Space. The list is now cumulative:
earlier entries are carried forward and re-filtered, so it shrinks only when a
paper is catalogued or rejected, and `counts.carried_over_from_last_run` says
how many are not from this week. Verified end to end against the real catalog
and watch list: 45 in, 44 out, 1 dropped as already rejected, 20 carried over,
44 of 44 catalogued, 0 new.

**The Space's refresh button is a link, not an API call.** "Run a new fetch on
GitHub" opens this workflow's page, where GitHub already limits **Run workflow**
to people with write access. Dispatching from the Space itself would need a
GitHub token stored there plus a Hugging Face login gate to keep visitors from
starting runs. The Space's date reads "List last changed", not "Generated":
the workflow commits nothing when the papers are unchanged, so the date only
moves when the list does.

**Three harvest bugs surfaced on the button's first day**, all fixed:
- `--since` (and the workflow's `since` input) takes a date or a number of days
  back. A bare number used to reach Europe PMC as `FIRST_PDATE:[20 TO ...]`,
  the year 20, and fetch the whole literature up to the page cap.
- **The page caps truncated silently.** Europe PMC stopped at exactly 1000
  records, newest first, so the oldest vanished. A ten-year window is about
  5100 records; the caps are now 6000 and 3000 and print a WARNING when hit.
- **Titles were compared with their HTML entities**, so "synthesis &amp; de
  novo" and its DOI-less copy were two papers. Titles are unescaped and
  stripped of markup wherever they are compared, catalog included, and a
  record with a DOI registers its title so a DOI-less copy is dropped.

And one in this repo: the publish step's deliberate `exit 7` ("only the date
moved") ran under GitHub's `bash -e`, so the step died before `rc=$?` and a
quiet week failed the workflow. It is caught on the same line now,
`python3 - <<'PY' || rc=$?`.

**The ten-year backlog was curated once, on 2026-10-04.** A `--since 3650`
harvest kept 259 papers, 83 of them not in the catalog. 67 went in as
publications 447 to 513, under 50 new or existing method rows. Seven of them
are preprints of papers already here or added in the same pass, linked
through `publication_version`. 16 stayed out:
- 11 Wiley open-review records of publication 419;
- two IUPAC Gold Book entries;
- a nanopore genome assembly and its Research Square twin;
- the nanopore amino-acid paper (the non-MS boundary) and a polyketide
  siderophore paper, both recorded in WATCHLIST.md.

The first three shapes are now dropped by the harvest itself. Authors were
matched by ORCID, then OpenAlex id, then exact name only where nothing
contradicted it, and every same-name reuse and near-miss spelling was
read before writing. Eleven spellings of people already catalogued were
pinned to their rows ("A.T. Lebedev", "Koen C.A.P. Giesbers").

**A citation sweep added 343 more, on 2026-10-04.** The 30 most-cited
`algorithm`/`post-processor` rows (by OpenAlex citations to their DESCRIBING
papers, PEAKS first) were walked in order, taking every citing work that says
"de novo" in its title or abstract and is neither catalogued nor in
WATCHLIST.md: 555 candidates. The pools saturate fast, so 30 methods is the
neighbourhood: the last eight added 14 between them. Six agents screened them
against the bar in BRIEF form (method, application, review and false-friend
rules), with read-only access to the catalog: 391 IN, 10 VERSION, 154 OUT.
Inserted: the 334 high- and medium-confidence INs and the 10 versions, less
one duplicate OpenAlex record. **The 57 low-confidence INs were held back**
for a person, on the local review page `citation-sweep/index.html` beside the
PDF library, which lists every verdict and its reason.

Three things the agents could not see across batches, fixed before writing:
the same method proposed under two names (the 2005 and 2006 multi-charge
papers), a paper appearing as two OpenAlex records, and 'ML conference' used
for RECOMB, CSB and LNCS proceedings, which are `peer-reviewed` here. It also
gave six families their second method, and so a page with no note; the
`INVARIANTS` guard in `check_counts.py` refused the commit until each had one.

**No code from the upstream Space is used.** It publishes no licence, so it is
all rights reserved and cannot be redistributed; `BioGeek/denovo-radar` is an
independent implementation, MIT licensed, crediting the original as the idea.

## Datasets, at three grains

"Trained on nine-species" identifies almost nothing, and the catalog now says
so with structure rather than prose. Four tables:

| table | the thing it holds | example |
|---|---|---|
| `dataset` | what people NAME | Nine-species benchmark |
| `dataset_version` | what models RUN ON | `revised`, `InstaNovo split` |
| `dataset_address` | where a version LIVES | `MSV000090982`, a Hugging Face repo |
| `publication_dataset` | what a paper DID with it | `uses`, `introduces` |

**478 datasets, 506 versions, 537 addresses, 844 publication links over 268 papers.**

**The nine-species benchmark alone has four versions**, and they are
distinguishable by number, which is the only reliable way:

- **original** (`MSV000081382`, DeepNovo 2017) is what a paper means unless it
  says otherwise. Its MassIVE title is "De novo peptide sequencing by deep
  learning", i.e. the deposit is the DeepNovo paper's, not a dataset release.
- **revised**, introduced by "A multi-species benchmark for training and
  validating mass spectrometry proteomics machine learning models"
  (publications 98 and 113), re-curated to remove peptide redundancy between
  species, which leaked test peptides into training in the original.
  2,844,842 spectra. It is **itself two variants**, `main` and `balanced`, the
  balanced one randomly thinned so the species are more evenly represented;
  both ship in one Zenodo record, so "the revised benchmark" is still two
  things. Addresses: `MSV000090982`, whose record carries DATED UPDATE FOLDERS
  (one paper cites `updates/2024-05-14_woutb_71950b89`) so even that accession
  is not a single fixed object; `10.5281/zenodo.13685813` for the data; and
  `10.5281/zenodo.12926326` plus `Noble-Lab/multi-species-benchmark` for the
  construction code, marked as such in `part` because code is not an address of
  the spectra.

  **Beware the neighbouring DOI.** `10.5281/zenodo.10358625` and `...626` look
  like they belong here and do not: they are `ismaRP/PPbenchmark`, released with
  "Benchmarking the identification of a single degraded protein to explore
  optimal search strategies for ancient proteins" (publications 341 and 342).
  They were briefly attached to the revised benchmark by misreading a two-line
  report where the title prints under the NEXT accession. They are not in the
  catalog at all now: both are GitHub releases of the paper's analysis code,
  which DataCite types as Software.
- **InstaNovo split** is parquet with a fixed 499,402 / 28,572 / 111,312
  train/validation/test split and its own DOI (`10.57967/hf/3821`), which makes
  it the only version reproducible by citation alone. Its 499,402 training
  spectra are the same count `BENCHMARKS.md` records NovoBench retraining every
  architecture on.
- **ProteoBench selection**, the 779,879 spectra that benchmark scores against.

**Provenance is an address, not a different dataset.** The benchmark is nine
unrelated third-party PRIDE submissions re-curated into one MassIVE deposit, so
those nine carry `is_provenance = 1` and name their species in `part`. They are
real datasets about honeybees and tomatoes that happen to be where these
spectra came from, and treating them as nine catalog datasets would be wrong
twice: it would invent nine rows and lose the fact that they are one benchmark.

**A NULL `dataset_version_id` is the finding, not a gap.** 57 papers use the
nine-species benchmark; 20 name the original, 14 the revised, 3 the InstaNovo
split, and **20 print only a per-species provenance accession**, which does not
determine which curated version they ran on. Inventing a version for those
would hide exactly the ambiguity the table exists to expose.

**`dataset.kind` separates the two populations, and they are empirically
distinct.** Accession reuse across the PDF library is bimodal: a couple of dozen
accessions are cited by five or more papers, and a tail of several hundred by
exactly one, the paper that deposited them. So `'benchmark'` and `'training'`
are shared resources models are evaluated or trained on, and `'deposit'` is data
a paper produced as its own result. `kind` records where a dataset came from,
and the links record how often anyone reused it.

**The tail is in, all of it.** Every accession the library yields now has a
`dataset` row, titled by its own repository, which is what makes "what data has
this field actually used" answerable at all. 30 are named by their accession
because no repository returned a title, and 13 carry an accession suffix because
repository titles collide -- two accessions of one study, or a generic title --
and merging two deposits that share a name would be wrong. `acquisition_mode` is
left NULL for all of them rather than guessed: a repository title does not say
DDA or DIA, and a wrong value would feed the site's filters.

**`role = 'introduces'` is inferred from TITLE, never from citation count.** The
tempting rule, "a deposit cited by exactly one paper was deposited by it", is
wrong: a single-citation accession is very often third-party data that one
method paper evaluated on. What distinguishes a depositor is that repositories
title a submission after the study that made it. Matching is prefix-against-
prefix cut to the shorter string, with `token_set_ratio` allowed only above 40
normalised characters, and with the repository's prefix shapes stripped and
retried (`Files for: X`, `data from "X"`, `Yeast mirror LC-MS/MS - X`). The
length guard is not optional: `token_set_ratio` ignores unmatched tokens, so a
deposit called `Casanovo` scored **100** against "Improvements to CasaNovo, a
deep learning de novo peptide sequencing...", exactly the failure the PDF
matcher hit. 29 links were promoted; both short-name cases now score 0.

**Tiers are versions.** The InstaNovo-FM corpus publishes three nested labelled
tiers, HCFM within MCFM within LCFM, plus **ACFM, the unlabelled superset,
which is not published as a tier**. ACFM is still reproducible: the
InstaNovo-FM paper lists the raw accessions it was built from, so rebuilding it
means reprocessing those rather than downloading them. Recording it as a
version with no address says exactly that, and is the reason the version table
allows a version with no `dataset_address` row at all.

**A version may have NO address, and that is a recorded fact rather than a
missing one.** 17 of 506 versions have none. Two shapes: ACFM, the InstaNovo-FM
tier that is not published but is reproducible from the raw accessions its paper
lists; and the living-proteomics benchmark's **private holdouts** (11 versions:
five organism sets, multi-protease, two immunopeptidomics, single-cell HeLa 2,
PTM/phospho, and non-natural peptides). Results are reported against the
holdouts and none can be downloaded, so those numbers are not independently
reproducible. A dataset nobody can fetch still belongs in the catalog, because a
reader comparing numbers needs to know which side of that line each one is on.

**A PER-SPECIES PROVENANCE ACCESSION IS THE BENCHMARK AT NO KNOWN VERSION,
and pNovo 3 is the worked example.** Its Table 2 scores four tools over seven
columns, and its supplementary Table S1 gives the accession behind each:
PXD005025, PXD004948, PXD004325, PXD003868 and PXD004467 are five of the
nine-species benchmark's OWN provenance submissions, already in
`dataset_address`. Going to those submissions directly does not say which
curated version was used, so those columns resolve the dataset and leave the
version NULL, and the paper is counted among those naming no version.

The other two columns, QE_HF_X1 and QE_HF_X2, are two HeLa runs on a Q Exactive
HF that share ONE PRIDE submission, PXD006932, and differ only in how much of
it was used (219,698 against 314,608 MS/MS spectra). So they are two versions
of one deposit rather than two deposits. The mapping is confirmed by
arithmetic rather than by name: Table S1's `#PSMs` column reproduces the
`#Total PSMs` row of Table 2 exactly, for all seven.

**"Seven-species" is a DIFFERENT DATASET, not a version of nine-species.**
Tran et al. 2017 built two evaluation sets, and papers name them side by side:
a **low**-resolution set of seven species, assembled from seven earlier
publications and scored leave-one-out, and the high-resolution nine-species
set. Different instruments, different species, different spectra, so one is not
a curation of the other. It gets its own `dataset` row with two versions,
DeepNovo's original and NovoBench's fixed split (317,009 / 17,740 / 17,094,
yeast held out).

**"HC-PT" is a version of PROTEOMETOOLS, and the arithmetic is the proof.**
NovoBench describes HC-PT as the set "detailed in the InstaNovo paper":
synthetic tryptic peptides spanning the canonical human proteome, alternative
proteases and HLA peptides, labelled from high-confidence MaxQuant results.
ProteomeTools already carried that corpus as `high-confidence (InstaNovo)`, and
HC-PT is almost exactly a **tenth** of it on all three counts, 213,284 against
2,132,847 train, 25,718 against 257,187, 26,536 against 265,369. So it is a 10%
subsample, recorded as a version; a second `dataset` row would have split one
resource across two pages each claiming the same spectra. A paper reporting "on
HC-PT" is reporting on a tenth of that corpus under NovoBench's split.

**Except in InstaNovo's own papers, where HC-PT is the WHOLE corpus.**
InstaNovo coined the name: its HC-PT is the full high-confidence set (2.6M
spectra, best PSM per peptide), and its AC-PT is every PSM regardless of
quality (about 28M spectra over the same 742k peptides), now a version of its
own, `all-confidence (InstaNovo)`. So the same four letters name two different
sets of spectra depending on who prints them, and the miner's default mapping
to NovoBench's subsample would be wrong on InstaNovo's tables. It is never
applied there: those tables resolve each row through `ROW_DATASET` (see
**Comparison tables in the database**), which pins InstaNovo's HC-PT to
`high-confidence (InstaNovo)`.

Both NovoBench splits have **no address**, which is the point rather than a
gap: NovoBench publishes code and not data, so its retrained numbers, the ones
`BENCHMARKS.md` warns against reading beside released-checkpoint numbers,
cannot be reproduced without splits nobody can download.

**A version label is unique only WITHIN its dataset**, which the seven-species
row proved by accident: its earliest version is also called
`original (DeepNovo, 2017)`, and two `check_counts.py` queries matched on the
label alone. The nine-species count silently moved from 16 to 17 and `--fix`
rewrote the prose to agree. Both queries now name the dataset too. Any new
registry query over `dataset_version.version` must do the same.

**ProteomeTools has eight versions, and they are not re-releases.** Parts I-III,
the high-confidence InstaNovo split, its all-confidence superset, NovoBench's
HC-PT subsample of it, the 21-PTM subset, the same peptide pools
re-run on a **Bruker timsTOF**, their **non-tryptic** counterpart on that
instrument, and a **TMT 6-plex** form. Instrument and label change the fragment
ladder a model has to read, so "trained on ProteomeTools" is as
under-determined as "evaluated on nine-species", for a different reason.

### The two dataset charts

**"The data underneath"** holds the version-ambiguity bars and the nine-species
provenance flow, between the benchmark sections and the application areas, which
is where a reader has just finished comparing numbers and should learn what the
numbers were computed over.

**Only three datasets qualify for the bar chart, and that is the finding.** A
dataset earns a row when naming it is ambiguous: more than one version in use,
or at least one paper that named none. Measured, exactly three clear that bar at
any floor from 2 to 4 papers, so the threshold is a constant and **not** a
slider: a control would imply a longer list than exists. The rest of the 400-odd
versions are cited once, have one version, or are always pinned.

**`fill` with a function returning a hex string renders the wrong colours,
silently.** Plot treats the returned string as a CHANNEL VALUE and maps it
through the default categorical colour scale, so `fill: d => "#d97706"` produces
scheme colours and the hex appears nowhere in the DOM. That is how the first
version of this chart shipped looking plausible and wrong. Use a named category
plus an explicit `color.range`, which is what the chart does now and which earns
a legend for free. This is the same class of trap as `dx`/`textAnchor` being
constants rather than channels, in the opposite direction.

**The provenance flow draws its two halves differently on purpose.** The nine
source submissions fan into one curated deposit with PALE, EQUAL-height ribbons,
because the catalog stores no per-species spectrum count and sizing them would
have invented one; the deposit then fans out to the versions with ribbons
weighted by the papers naming each. A version nobody has cited still gets a
visible box at a 16 px floor, because it exists whether or not anyone cited it:
that is how `ProteoBench selection` and `revised (balanced)` appear at 0 papers.
Hand-rolled SVG rather than d3-sankey, following `application_sankey` above it.

**A third chart, "Deposits that travel together", answers a different question
with the same data.** Six of the reused deposits have NO shared provenance: they
are unrelated studies, of dolphin tissue and bronchoalveolar lavage fluid and
diabetic beta-cells, that nobody merged into anything. What they share is that
the same papers reach for all of them at once, which makes them a benchmark
suite in practice and nothing in name. So this one is a usage flow, deposit to
paper, where the nine-species chart is a provenance flow.

**The block is computed from an identical citing-paper SIGNATURE, not from
pairwise similarity**, which is what makes it worth printing: five deposits are
cited by exactly the same five papers, and a sixth by those five plus one. The
set is DERIVED from a reuse count, so a block that forms later appears without
an edit; nothing hardcodes an accession. Every ribbon weighs 1, because a paper
either used a deposit or did not and there is no spectrum count to scale by, so
node height is degree.

Two measured fixes it needed. Paper labels are truncated at 40 characters, after
three ran past the frame; and where two papers share a leading method name --
a paper describing both PandaNovo and pi-HelixNovo leads with the same string as
the pi-HelixNovo paper -- the YEAR is appended, but only to the labels that
actually collide.

All three were audited with `check_chart_overlap.py`: **39 chart SVGs, 0 OJS
errors, 0 collisions**. It caught two things eyeballing had missed. The
nine-species provenance labels were clipped by 42 px at a 232 px left margin
("Candidatus Thiodiazotropha endoloripes" is the longest), now 300. And the
VENUES chart, which this work never touched, had a 3 px overlap between its
'40' tick and its axis label, fixed by remedy 3 above (`labelOffset` plus
`marginBottom`); the previous "0 across 20 charts" note had gone stale as the
page grew to 39.

### The dataset network and the dataset table

**The network holds a dataset once two or more papers link to it**: 83 datasets,
93 papers, 279 links, 176 nodes. A deposit cited only by the study that made it
says nothing about reuse, and several hundred are in that state. Edges are
UNWEIGHTED, because a paper either used a dataset or did not and there is no
spectrum count to scale by, so "weighted by reuse" is carried by node radius.

Three force settings were measured rather than guessed, and the number to tune
against is **node spread**, not the label audit, which passes happily on a
compact knot:

- **1250 px wide, not the 1400 the other two force charts use.** At 1400 the
  nodes filled 69% of the frame with nothing within 179 px of the right edge.
  Narrowing the frame to the content beats pushing the content at the frame.
- **Link distance 78 and charge -240**, up from 58 and -170. The layout
  converged at the tighter values but did not fill: the deposit cluster holds
  about 150 of the 176 nodes and a charge capped at 190 px cannot separate a
  ball that dense.
- **The cluster centroid is WEIGHTED by group size.** A phyllotaxis walk of
  three points is not symmetric about the origin, and subtracting a plain mean
  moved the targets without moving the network, because 72 of 83 datasets are
  deposits and the centre of mass sits at that group's target. Weighting puts
  the heaviest group near the middle, which is what centring a lopsided graph
  means.

Final measurement: 176 nodes, **0 within 4 px of the frame**, 75% of the width
filled (against 76% and 79% for the other two), 101 labels shown, 0 overlaps.

It carries the same **fullscreen button and `.map-tooltip`** as the other force
charts. The SVG `<title>` it shipped with was not good enough: the native
tooltip needs a hover pause, cannot be styled and wraps nothing, so a
120-character repository title arrived as one unbroken line. **The tooltip div
must live INSIDE `.chart-wrap`**, because a fixed-position element outside the
fullscreened subtree is not rendered in fullscreen at all, so a tooltip parked
on `body` disappears exactly when the chart is most readable.

**Count node marks, not every rect and circle.** The first spread measurement
read 36 px as the leftmost node and sent me tuning a centroid for two rounds;
36 px is the LEGEND swatch, drawn outside the zoom group. Filter to marks whose
parent group carries a `<title>`, and the real span was 257 px.

**"Every dataset" is a searchable table over all of them**, which is how the
long tail is reachable at all: `Inputs.search` is given the accession column
explicitly, so typing `PXD004424` or `zenodo` finds a dataset that no chart has
room to show. A tick under *Deposited* means a catalogued paper produced the
data rather than merely running on it.

**The bars count USES, not papers.** A paper naming two versions of one dataset
contributes two rows, so the bar total is a link count: nine-species reads 44
uses across 34 distinct papers. The axis said "Papers naming this dataset" and
the table beside it said 34, and the two disagreeing was a labelling bug, caught
by reading both at once.

### The navbar and the hero badges

The navbar is **The map, Papers, Methods, Datasets, Authors**. Papers points at
`#every-paper` and Datasets at the SECTION `#the-data-underneath`, and the
difference is deliberate: the papers section opens with a short list of recent
arrivals standing in front of the table, while the datasets section opens with
the charts a reader actually wants. Methods points at the architectures swim
lane, the only view showing every method, which is the target the hero badge
already used.

The fourth hero badge is **datasets**, replacing countries. `n_countries` stays
defined, because the geography section's own prose and the summary sentence both
still read it.

**The breakdown rows already filter, and the anchor was the bug.** Clicking
`Benchmarks` under *By kind* sets `kind_filter` and jumps to the table; it had
always done the first half, but it jumped to `#browse-all-papers`, which landed
the reader on the recently-added list where nothing visibly happened. Verified
after the anchor change by clicking in headless Chrome: the table goes 50 rows
to 18, every row reads `benchmark`, one of seven kind checkboxes is checked, and
the filter panel opens so the change is reversible.

### build_dataset_accessions.py

Mines the LOCAL PDF library for accessions and links them. It **never** creates
a `dataset`, `dataset_version` or `dataset_address` row: deciding that a pile of
spectra is a named dataset, and which version, is the judgement call the three
tables exist to record. Unknown accessions go to `dataset_candidates.csv`,
ranked by how many papers cite them. `--write` creates only
`publication_dataset` rows, and only for accessions already in
`dataset_address`, because that half is mechanical: a paper printing
`MSV000081382` used the nine-species benchmark.

**It must never run in CI**, same reason as `build_pdf_library.py`: there is no
library on a runner. It imports that module for `rapidfuzz`, so run it under
`uv run python`, not bare `python3`.

Two traps, both hit while writing it:

- **Do not map filenames to publications through `pdf_status.csv`'s `file`
  column.** That column is written by `fetch` for files it downloads, so it
  covers neither hand-filed PDFs nor the full-title rename. It named 130 of the
  243 files present plus 82 that no longer existed, silently halving the scan:
  100 accessions found instead of 402. Call `build_pdf_library.coverage()`
  instead, which is the audited matcher.
- **A Zenodo or figshare DOI is as often software as data.** The first run put
  PyTorch Lightning 0.7.6 and two supplementary-file bundles near the top of the
  candidate list, and they would return on every run. DOI-shaped accessions are
  now checked against DataCite (cached) and dropped when the repository's own
  title says software release, supplementary material or code, with the count
  and the reason PRINTED rather than filtered away silently, since that count comes
  from the lookups and not from the database. An unknown title is never an
  exclusion. **The repository's own TYPE is checked before the title**:
  DataCite's `resourceTypeGeneral` says `Software` for every Zenodo GitHub
  release whatever it is called, and a title need not sound like code.
  "Fine-Tuning Scheduler", a PyTorch Lightning extension, passed every title
  pattern and was a dataset with its own page until a reader spotted it. An
  audit of every DOI address by type then removed seven more rows:
  - TensorFlow, InstaNovo's and Casanovo's code releases, and PPbenchmark's
    analysis release, which are all software;
  - DiNovo's code release, recorded under a DOI misread with two trailing
    digits too many, so no record existed to flag it;
  - Casanovo's nine-species weights, already checkpoint 5;
  - Winnow's hold-one-out calibrators, which are now a checkpoint row.

  **A DOI is case-insensitive**, and papers print Zenodo's in both cases, so
  `10.5281/ZENODO.6791263` was a second Casanovo dataset beside the
  lower-case one. Accessions are compared in lower case.

  **A trailing full stop is not part of a repository name.** A Hugging Face
  name may contain dots, so the pattern accepts them, and a URL that ends a
  sentence brought its full stop along. That created
  `InstaDeepAI/ms_proteometools.`, a second copy of the HC-PT corpus that was
  already the address of `high-confidence (InstaNovo)`, and
  `InstaDeepAI/InstaNovo-P.`, which is now named after its dataset card. The
  scanner strips it.

  One verdict is worth knowing about:
  `Noble-Lab/multi-species-benchmark: Revised benchmark` is excluded as a repo
  snapshot, correctly, even though that repository is how the revised
  nine-species benchmark was built. It is code, so it is not an address of the
  data.
- **Count links, not hits.** Several accessions resolve to the same link, since
  a paper listing all nine provenance submissions is one row and nine hits.
  Without deduplication the report claimed 187 rows where `--write` created 86,
  and the report was the wrong number.

## Checkpoints

A benchmark number is only reproducible if the weights behind it can still be
downloaded, and this field keeps them in places with very different durability.
The `checkpoint` table records where they are and whether that is still true:
`(algorithm_id, label, tool_version, trained_on, host, url, accession, licence,
size_bytes, archival, status, http_code, last_checked, mirror_url, notes)`.
The 15 methods with a recorded checkpoint carry a **## Checkpoints** table on
their page, listing version, training data, host, licence, size and when the
link was last checked.

**One method name covers several models**, which is why `tool_version` and
`trained_on` are columns rather than prose. ProteoBench submits Casanovo
4.0.0, 4.2.0, 5.0.0 and 5.2.0-Orbitrap as separate datapoints
([discussion 356](https://github.com/orgs/Proteobench/discussions/356)), because
4.2.0 was trained on ~2M PSMs from MassIVE-KB v1 + v2.0.15 while 5.2.0 ships a
different default selector. Comparing a number against "Casanovo" without a
version is the same error as comparing one against "nine-species".

**`archival` is the durability class, not a label.** A DOI'd Zenodo or figshare
record is dated and preserved by its host; a Google Drive link, a personal
academic URL or a GitHub release tag is mutable, undated and can vanish leaving
nothing to cite. That distinction is the only reason to mirror anything.

### What the download actually found

The point of mirroring was link rot, and the survey found it in progress.

**Of nine Google Drive links, two were not files at all.** DeepNovo-DIA's is a
FOLDER holding `oc`, `plasma`, `uti`, a Windows installer, a code zip and
`train.urine_pain.ioncnn.lstm`, which is where its weights are: a TensorFlow
triple, `translate.ckpt-31800.data-00000-of-00001` plus its `.index` and a
`checkpoint` meta file, 184 MB. A file fetch against a folder id fails with a
permissions error, which reads like rot and is not.

**DeepNovo's is gated**, as above, and it is the one checkpoint here that cannot
be retrieved at all without a Google account until the catalog owner fetched it
from a signed-in session. Both DeepNovo entries turned out to be the awkward
ones: the least durably published weights are the ones hardest to rescue.

**The scan that preceded this was a lower bound and badly wrong.** Looking for
host names near checkpoint language in local PDFs found ONE Drive link. There
are nine, because the links live in repository READMEs rather than in papers:
`BEAM-Labs/denovo` publishes five in one table, and the ProteoBench discussion a
sixth. A paper's data-availability sentence is not where this field puts its
weights.

**Uploads fail partway and succeed on retry.** Three pushes to the mirror failed
after uploading 1 of 3, 3 of 3 and 3 of 4 objects, with `X-Cache: Error from
cloudfront` and 39 GB transferred for 4.4 GB of files, so git-lfs was re-sending
objects repeatedly. It is a flaky path through the TLS gateway, NOT a size
limit: the first diagnosis was that 2 GB objects were too large, and a plain
retry put both of them up. Push in a loop with `lfs.concurrenttransfers 1`.

**But not every failed push is that, and the two look identical in a log tail.**
Eight consecutive retries then failed on a `pre-receive hook declined`, which no
amount of retrying can fix: the Hub rejects anything over **10 MiB** that
bypasses LFS, and **a TensorFlow shard is named
`translate.ckpt-31800.data-00000-of-00001`, which does NOT match `*.ckpt`.**
The `.ckpt` sits in the MIDDLE of the name, so the pattern missed it, the 184 MB
file went in as a plain blob and the whole push was refused. `*.data-*` and
`*.index` are tracked now. Both failures end in `error: failed to push some
refs`; only the `remote:` lines above it say which one you have.

### The mirror, and the three places checkpoints appear

Backups of the checkpoints whose only home is a link with no DOI live in
<https://huggingface.co/BioGeek/denovo-checkpoints>, one folder per algorithm.

**Three surfaces, and each earns its place for a different reason**, which is
worth stating because a fourth was built and then deleted:

| surface | covers | regenerates |
|---|---|---|
| the Hugging Face README | the mirrored files | by hand, and it must exist: attribution has to travel with the bytes |
| **"The weights behind the numbers"** on index.qmd | every checkpoint, in one table | every render, from `denovo.db` |
| each algorithm page's `## Checkpoints` | that method's own | every `build_pages.py` |

A standalone `/checkpoints` page on the personal site was written, rendered and
then **deleted unpushed**, along with the `build_checkpoint_index.py` that fed
it. It was designed when the FILES were going to live at that URL; once they
moved to Hugging Face it added nothing the cross-method table does not, while
being the one copy that lived in a second repository and regenerated only when
somebody remembered a command. That is the duplicated-list failure this file
warns about for counts, and it applies to prose and tables too. **The place to
put a derived view is the project that owns the data.**

**The files cannot be served from jeroen.vangoey.be, which is where they were
first meant to go.** That domain is GitHub Pages: it **rejects any file over
100 MB on push**, and every checkpoint here is 270 MB to 2 GB. Git LFS does not
rescue it, because Pages serves the LFS pointer text rather than the object, and
a Pages site has a 1 GB published limit against 6.4 GB of weights. So the bytes
are on Hugging Face and only the index page is on Pages.

**The original link stays primary on every page.** A mirror is a fallback;
leading with it would obscure that the authors published the weights themselves,
and the original is what a paper should cite. The algorithm pages add a *copy*
column only when a mirror exists.

**Only weights are mirrored, and the data that was skipped is why that rule
exists.** Following every link in the sources would have cost **88 GB**:
`mskb_casanovo_splits.zip` is 63 GB, DIANovo's Dropbox folder is a 24 GB archive
of sample data with the weights inside it, and DeepNovo's Drive folder holds
`data`, `data.training` and `train.example`. DIANovo's weights were taken from
its **git-LFS** instead, 4.4 GB for the three `.pt` files, which is both smaller
and a better source than the Dropbox copy.

**Two operational notes, both learned the hard way.**

- **Stage multi-gigabyte work on the real disk.** Staging 4.4 GB of checkpoints
  plus an LFS clone in the session scratchpad filled a 16 GB tmpfs and killed
  the push with `ENOSPC`.
- **The Hugging Face Python client cannot reach the Hub from this machine**, for
  the same reason `requests` cannot reach publishers: the gateway's re-signed
  certificates carry no Authority Key Identifier, so `httpx` raises
  `CERTIFICATE_VERIFY_FAILED`. Create the repo with `curl` against
  `/api/repos/create` and push the files with `git` + `git-lfs`, both of which
  use a transport that accepts the certificate.

### build_checkpoints.py

This table's **only** writer, which is what would let it run under
`.github/actions/commit-refreshed-db`. It does not decide which checkpoints
exist: resolving a paper's data-availability sentence to a method is a judgement
call, so rows are added by hand and the script fills in `status`, `http_code`
and `last_checked`.

**A HEAD request is not enough on its own, and Google Drive is why.** Drive
answers **200 with an HTML interstitial** whether or not the file is still
shared, so a 200 there proves nothing. A 200 whose content type is HTML where a
binary was expected is recorded as `unverifiable` rather than `live`: the honest
answer is that the link resolves and what it resolves to cannot be confirmed
from a header.

**A DOWNLOAD OUTRANKS A PROBE.** `verified` means the bytes were fetched and
hashed, and `verified_at` says when; it is set from `sha256`, because a
completed download is stronger evidence than any HEAD request. It replaced
`unverifiable` and `moved` on 10 of the 11 rows whose contents the catalog
holds, which were the checker admitting it could not tell from a response
rather than a finding about the file. It does NOT override `dead` or `gated`:
those describe the ORIGINAL link and a reader needs them even when a copy
exists, which is exactly DeepNovo's case.

The pages print the date that matches the verdict: `verified_at` for a verified
row, `last_checked` for everything else. One date for both would conflate "we
have this file" with "this link answered".

Measured over the 27 recorded checkpoints: 16 live, 10 verified, 1 gated.

**A gated checkpoint can still be backed up, and DeepNovo now is.** Its weights
were retrieved from a signed-in session and mirrored, so the backup is the only
anonymous route to them. The status stays `gated`, because that describes the
ORIGINAL link, which is still unreachable without an account; what changed is
that a copy exists. Status and `mirror_url` answer different questions and the
pair is the useful reading.

The Drive folder held more than weights, which is the normal case: a yeast
FASTA, the `yeast.low.coon_2013` test spectra, training logs and decode output.
Only `translate.ckpt-48600`, its `.index` and meta went to the mirror, plus
`knapsack.npy.zip` -- not weights, but DeepNovo cannot run without it, so a
checkpoint alone would be unusable.

**`gated` is a status of its own, and DeepNovo earned it.** The pretrained model
for DeepNovo, the original deep-learning de novo method, now answers *"We can't
access this content right now. Try signing in to your Google Account"*. The
Kaiko paper cites that link as publicly available; it is no longer anonymously
downloadable, so the weights behind DeepNovo's published numbers cannot be
fetched without somebody's Google session. That is not `dead` and not merely
`unverifiable`, and a reader comparing against DeepNovo should see it.

**Detecting it depends on the URL FORM**, which cost a wrong verdict first time.
Drive's legacy `open?id=<id>` form returns a 944 KB application shell with no
gate text even when the file is gated; the canonical `/file/d/<id>/view` page
says so plainly. So `body_says_gated` extracts the id and tries both.

**Licence, not size, is what governs mirroring.** Of the non-archival
checkpoints, the five Casanovo and InstaNovo-FM GitHub releases are Apache-2.0
and redistributable; Winnow's HeLa QC model is **CC-BY-NC-SA-4.0**, so copying
it is permitted but conditional (non-commercial, share-alike, attributed); the
two DeepNovo repositories carry **custom non-commercial licences**; and the one
item with genuinely nothing stated is the `noble.gs.washington.edu/~melih` zip,
which is training data and was not mirrored.

**`NOASSERTION` means GitHub cannot CLASSIFY the licence, not that there is
none**, and reading it the second way was a mistake worth recording because it
produced a confident and wrong claim twice: that the two most at-risk
checkpoints "may not be copied", and that "silence is not permission" when
there was no silence. Both repositories have a LICENSE file:

> DeepNovo is publicly available for non-commercial uses. Copyright (C) 2017.
> Authors. All rights reserved.

> Copyright (C) 2018. Authors. All rights reserved. DeepNovo-DIA is provided
> free of charge for academic and non-commercial use.

Academic and non-commercial USE is granted; redistribution is not expressly
granted, which is a narrower and more accurate statement than the one it
replaced. **Read the LICENSE file, never the API's classification of it.**

Mirroring is therefore a deliberate manual step and **never part of a scheduled
refresh**: it copies someone else's bytes, publishes them, and commits this
project to keeping them alive, which a cron job should not decide. `--mirror`
reports the candidates and what blocks each one.

## URLs are a lock file

`slugs.lock` records the published URL of every generated page. A slug is
derived from mutable data, so editing a title or a name silently rewrites a URL,
404s the old one and discards its search equity. `python3 slugs.py --check`
fails on a CHANGED or REMOVED entry and passes on an ADDED one; it runs in
`.githooks/pre-commit` and in `check-slugs.yml`.

A **changed or removed** URL is never auto-fixed: it fails the commit, because
rewriting the baseline for those is the very failure being guarded against.
When a rename is intended, run `--write` and let the lock diff record it.

**An UNMATCHED count also fails the commit**, and for the same reason a
changed slug does. `check_counts.py --fix` repairs a stale NUMBER and exits 0,
but it cannot repair a claim whose PATTERN no longer matches the prose, and for
that it exits 1. The hook used to discard that status with `|| true`, so
rewording a sentence in CLAUDE.md -- an ordinary thing to do, and something
`--fix` does itself -- could silently unmatch the regex that reads a number out
of it. The registry entry then checks nothing and nobody hears about it until
CI goes red; two commits went out that way. The auto-fix and the re-staging
still run first, so a refused commit keeps the numbers `--fix` just corrected.

**A VIOLATED invariant fails the commit too, and is never rewritten.** Some
claims state the value the data must have, always zero (family pages with
no note, for one), and `INVARIANTS` in `check_counts.py` names them. `--fix`
used to treat them like any count, so when the Protease strategy family gained
a page with no note it rewrote that guard from zero to one in the same commit,
and the check passed. Now a nonzero invariant prints `VIOLATED`, leaves the prose
alone and exits 1 under `--fix` as well, so the remedy has to be to the data.
A label in `INVARIANTS` that names no claim is an error, so a renamed claim
cannot drop out of the set unnoticed.

**A renamed URL redirects; it is never just dropped.** `REDIRECTS` in
`slugs.py` maps each retired slug to its successor, and `build_pages.py`
gives the successor page a Quarto `aliases:` entry, which writes a redirect
page at the old address. Add to it and never prune it. The first 21 came from
publisher markup: 24 titles carry the paper's own `<i>`, `<sub>` or JATS
`<italic>`, which slugged to `...-i-de-novo-i-...`, and two journal names
stored as `&amp;` had venue pages of their own. `slugify()` now strips tags
and entities, the two journal names were decoded into the real venues, and
the lock was rewritten with `--write`.

**The markup is kept in the data and rendered, not stripped.** It is the
paper's own typography, and the publication page's heading already showed it
in italics while every list escaped it into literal tags. `title_md()` in
`build_pages.py` and the `title_html` OJS cell in `index.qmd` escape
everything except `<i>`, `<b>`, `<sub>` and `<sup>` (JATS `<italic>` and
`<bold>` normalised to those), and drop any other tag; JSON-LD gets the
title as plain text.

A **new** URL is recorded automatically by the hook, right after `--check`
passes. That is safe precisely because the dangerous cases already failed by
then, and the alternative is worse: `--check` passes on additions, so the lock
quietly stopped being a complete inventory. Publication 352's page was live and
in the sitemap while absent from the lock, which means a later rename of it
would not have been caught at all.

The hook used to skip both guards entirely unless `denovo.db` was staged, which
had the same effect for a different reason: adding the `families` entity type
put 24 new URLs on the site from a commit that touched no data, and the hook
returned before `slugs.py` ran. The dump step is still conditional; the count
refresh and the URL guard are not.

## Citation graph

`build_citations.py` is the offline builder: it walks every publication, queries Crossref (by DOI) and Semantic Scholar (by DOI or title search), resolves references back to local publication ids by DOI-exact or fuzzy-title match (token-set ratio ≥ 92), and inserts edges into `publication_citation`. Fuzzy matches are also logged to `citation_audit.csv` for human review. The script is intentionally NOT run by CI; it's ~30 min of network I/O and Semantic Scholar rate-limits hard. Re-run locally when new papers are added, eyeball the audit CSV, then commit the regenerated `denovo.db` + `denovo.sql`.

When adding rows by hand, always check whether the entity already exists before inserting: author names and affiliation `(name, department)` pairs are the natural keys, not the surrogate IDs. A typical insert path for a new paper is: `country` → `city` → `affiliation` → `author` → `author_affiliation` → `algorithm` → `publication` → `publication_author` (with `author_order` set per author) → `publication_algorithm`. See any of the previously committed paper insertions (e.g. the `CausalNovo` commit) for the standard `INSERT … SELECT id FROM …` pattern.

Then finish the job outside the database: `build_abstracts.py` for the abstract,
so `abstract_source` records where the text came from rather than claiming it
was curated, and `build_pdf_library.py fetch --ids <id>` for the PDF. See **The
local PDF library**.

## Reading a table's structure out of its LaTeX source

`build_table_structure.py` fetches an arXiv source tarball and reads
`\multicolumn` and `\cmidrule` directly, which is the only statement of a
column grouping that involves no geometry at all. It is REPORT-ONLY: it writes
`table_structure_candidates.csv` and prints proposed `SPANNER_OVERRIDE`
entries, and edits no Python, because deciding that a given tabular is the one
behind a given PDF table is a judgement. Never in CI; it needs the network and
the PDF library.

**Its reach is limited and worth knowing before reaching for it.** Of the 25
papers with a refused table and none accepted, 7 are arXiv with source and 3
more bioRxiv, so at most 10 are reachable. Measured over the 7, it proposed 2
overrides: most "failures" are tables whose methods are ROWS, which need no
override, and the rest are caption-match misses. Its real value is the narrow,
high-value case of a method spanner over an uneven number of columns.

It did pay for itself once: it showed that publication 30's own table prints
`Transformer-DIA`, which exposed the `paper_vocabulary` bug below.

## Spot-checking the mined tables

`review_comparisons.py` builds a LOCAL page that puts every mined table beside
a picture of the printed one, cropped out of the PDF, with the refused tables
and their reasons in the same list. Checking an extraction from a CSV is
hopeless, because what needs checking is whether the grid matches the page.

    uv run --with pdfplumber python3 review_comparisons.py
    uv run --with pdfplumber python3 review_comparisons.py --ids 49,58
    uv run --with pdfplumber python3 review_comparisons.py --rejected-only

**It writes OUTSIDE the repository and is never published**, into
`comparison-review/` beside the PDF library, for the same reason
`build_pdf_library.py` writes `pdf_status.csv` there: the crops are figures
from other people's papers, so it is a private reading aid. Nothing it
produces is committed, and it must never run in CI, which has neither a
library nor poppler.

Rendering is `pdftoppm` plus a PIL crop rather than pdfplumber's `to_image()`,
since poppler is already behind this project's PDF handling. The page render is
cached per (file, page): the first version re-ran `pdftoppm` once per table and
rendered one page seven times.

`emit()` takes an optional `collect` list and appends the resolved structure to
it, so the review page renders the parse from the same code that writes the
audit row rather than reimplementing the layout logic. `block_bbox()` is used
only for the crop and never for parsing, and it is computed BEFORE the caption
vetoes, because a refused table is exactly the case a human most needs to see.

**The sign-offs are committed, in `paper_comparison_review.json`.** They
are the curated judgement that decides which parsed tables become data -- 80
approved and 44 dismissed refusals at the first full sign-off -- so the result
cannot be reproduced without them. The file holds ids, verdicts and dates
only, nothing copied from a paper, which is why it can be committed when the
crops cannot. It used to live beside the crops as `approved.json`; the script
copies that across on first run if the repository file is missing.
Reproducing the page needs three things: this repository, the local PDF
library, and `uv run --with pdfplumber python3 review_comparisons.py`. A full
run takes about 8 minutes and should end on `0 accepted, 0 rejected`, meaning
every table is signed off; anything else is a table whose parse changed.

**Three caption traps, all from sub- and superscripts the text layer sets on
lines of their own.** An unmapped glyph (`(cid:100)`, InstaNovo's ŝe_B hats)
and a short sub/superscript fragment (Pairwise's 'Casanovo_bm') each sit alone,
indented, and read as a header row that ended the caption a sentence early. The
walk now steps over both. An unmapped glyph in the caption text becomes `�`
rather than vanishing, since deleting one turned "The ▲ denotes" into "The
denotes"; `CAPTION_OVERRIDE` holds the captions only a person can restore.
Labels the text layer glues are re-spaced by one shared rule set,
`unglue_label()` ('HeLasingle-shot', 'S.Brodae', 'Exc.Yeast',
'Candidatus“Scalindua'), with `PROTECTED_CASE` names such as 'HeLa' never
split. And the page's ranking reads a cell with the miner's own `numeric()`:
stripping non-digits read '0.463(0.004)' and '0.609±0.007' as non-numbers, so
every cell printing its spread fell out of the bold/underline ranking.

**Re-rendering is incremental, at three speeds.** Almost all of a full
render's ~8 minutes is parsing PDFs, so the page stores what it parsed in
`items.json` and the cheaper paths reuse it:

| command | parses | for | time |
|---|---|---|---|
| `review_comparisons.py` | every paper | a change to the miner | ~8 min |
| `... --ids 9,17` | those papers, merged into the full page | a fix to a few papers | seconds per paper |
| `... --rewrite` | nothing | a change to how the page is drawn, or a sign-off | under a second |

A crop is also redrawn only if it MOVED: each is keyed by source file, mtime,
page and bbox in `crops.json`, and the run prints `crops: N reused, M
rendered`; `--recrop` forces the lot. Sign-offs are re-applied to every item
from `approved.json` at write time, so an approval for a paper outside `--ids`
still takes effect. A restricted run must never prune what it did not touch:
it used to delete every other paper's crops and cut the manifest down to its
own entries, so a smoke test over seven papers cost the next full run 88
redrawn crops.

**A table's id is derived from the table, not its position**: paper, page
and printed label, as in `p202-pg10-t6` or `p64-pg6-t1-nine-species`, with
`-2` for a clash on one page. It used to be the n-th item on the page, in an
order that followed each item's verdict, so turning a refusal into an accept
reshuffled the page -- MemNovo's Table 6 moved from `pg10-0` to `pg10-2` -- and
a sign-off keyed by id could have landed on a different table. None had, which
was checked before the change: all 64 recorded labels still matched. The
migration re-keyed sign-offs, crop manifest and crop files together, and a full
re-parse then generated identical ids (118 of 118, every crop reused). An id
now changes only if its label is read differently.

**While a run is in progress the page says so**: a banner with papers done of
total, elapsed and an estimate, and a 15-second reload that stops by itself
when the finished page replaces it. Before that, the page mid-render showed
the previous version as if it were current.

**Bold and underline are OURS, always**: best value and next value within each
measurement, ties included, whether or not the paper marks anything. That is
one consistent reading across tables whose own conventions differ.

**The paper's own marks are checked, not drawn**, and every mark that differs
from ours gets a footnote saying what the original table bolded or underlined,
listing only the marks that differ. That includes a paper counting its own
variants as one method: DiffuNovo underlines pi-HelixNovo, its best competitor,
where we underline its other variant DiffuNovo (Logits). That was first left
unfootnoted as a legitimate convention, and the reviewer reversed it, because a
reader comparing the picture with the grid otherwise cannot tell why they
differ. It also covers a plain mistake: CrossNovo's Table 1 bolds both
pi-PrimeNovo (0.697) and InstaNovo (0.732) for peptide recall on Tomato, and the
odd-looking 0.732 is genuinely printed, since it reproduces that row's stated
average of 0.530.

A tie marked only IN PART is footnoted too, naming the tied cell we add:
LIPNovo's Table 1 underlines pi-HelixNovo-dagger at 0.765 for amino-acid
precision and not pi-HelixNovo, also 0.765. Which row carries that underline
was settled by geometry rather than by eye, because the two readings disagreed:
the rect spans x 184.5-202.9 at y 215.0, on the dagger row's baseline (215.5);
the plain row's underline, at y 205.0, is in the NEXT column, under its
amino-acid recall.

Three things are not differences: an unmarked measurement, a measurement
holding a single value (nothing to rank), and missing underlines in a table
that never underlines. A lone value the paper bolded is footnoted only where
the others are explicitly not run, as in LIPNovo's Table 4, where GraphNovo's
AUC is '-'. Measured over
the whole library: 11 footnotes -- six on DiffuNovo's best-competitor
underlines, one on the Tomato cell, and four on ties marked in part.

**Margin line numbers are not the regular face.** The regular face is the
commonest numeric face on the page, and on CausalNovo's page 16 the review
template's 54 margin line numbers, set in `NimbusSanL-Bold`, outnumbered the
table's own faces. So every cell read as bold, and the page reported the
paper bolding its own Casanovo rows. Line numbers are now stripped before
counting, and a face named bold, black, heavy, medium or semibold is never
taken as the regular face while a plainer face exists. Over the whole library
that changed one table's marks, and no other.

The marks are still READ, because checking needs them. The underline is exact
-- a thin rect spanning x 355.0-377.4 under a word spanning 355.0-377.4 -- and
bold is the face that is not the page's commonest numeric face, because LaTeX
with Times renders `\\textbf` as `NimbusRomNo9L-Medi`, which no
`bold|black|heavy` pattern catches. The fonts come from a separate extraction
pass: `extract_words` splits a word wherever an extra attribute changes, so
asking for `fontname` in the parsing pass could move a cell into another
column.

**One name per species, whatever a paper prints.** Across the accepted
tables, nine species and an aggregate arrive under 69 spellings: `B. sub.`,
`B.sub.`, `Bacillus` and `BacillusSubtilis` are one organism, `Clam bacteria`,
`Clam Ba.`, `C. end.` and `CandidatusEndoloripes` another. `canonical_subset()`
resolves each to the catalog's OWN name -- the nine-species benchmark's
provenance submissions record their species in `dataset_address.part` -- and so
to the accession its spectra came from. Common names are an explicit table
(nothing derives 'Human' from 'Homo sapiens'); abbreviations resolve by genus
initial plus the start of the epithet; a typo (`B. subtilus`) by a near match on
the epithet; proteases likewise (`HC Chymo.` is `HC Chymotrypsin`). A subset
that is not a species -- OC, UTI, a pNovo run -- keeps its printed form rather
than being forced into one, and an aggregate keeps its own WORD: 'Average' and
'Mean' are not merged, since one paper's average may be weighted by spectra
where another's mean is the plain mean of its per-species values; only
spellings of one word are ('Average', 'AVERAGE', 'Avg.'). The review page shows the canonical name under the
printed one, and the audit records both, `subsets_canonical`, so a schema can
store the species as printed for checking and as named for comparing.

**A table that prints differences is converted, and the conversion is
marked.** TSARseqNovo's Table 1 prints only its own scores, each followed by
'vs CasaNovo' and 'vs pi-HelixNovo' rows giving its improvement. At the
reviewer's request those become ordinary Casanovo and pi-HelixNovo rows,
computed as TSARseqNovo minus the improvement, with a note under the table
saying so. This is the ONLY place the miner records a number the paper did not
print, which is why it is a registry (`DIFFERENCE_TABLES`) and not a rule, and
why every such cell carries `derived` with the expression it came from: when
the schema lands, a derived value must stay distinguishable from a printed one.
The unit was confirmed rather than assumed: subtracting in percentage points
reproduces CrossNovo's independently printed pi-HelixNovo values exactly, in
all nine species; a relative reading matches none.

**Ranking happens within a measurement, and the subset can be a ROW GROUP.**
With methods down the side, a species can head the column (CrossNovo) or a
group of rows (LIPNovo's leave-one-out Table 3). Grouping on the column alone
ranked LIPNovo's nine species against each other as if they were one
measurement.

**A row-group label is often CENTRED on its group**, and in the text layer it
then lands between the group's rows. Attaching it to the row below, as a label
set above its group would be, paired every LIPNovo species with the previous
species' baseline -- and the parse still "verified": each LIPNovo row was
right, one baseline row looked missing, and an average of the rest happened to
match the prose's +5.3%. It did not match the other two figures the prose
gives (+4.5%, +2.3%), and that mismatch was the tell that should have been
taken. A label within a quarter of the gap of the midpoint between two rows
now belongs to both; read that way every species has its pair, the printed
Mean row is 0.751 against 0.804, and all three prose figures match exactly.
**Check a parse against every number the prose gives, not the first one.**

## Comparison tables in the database

Seven `paper_comparison*` tables hold every SIGNED-OFF mined table, in two
layers, so that one record can both reproduce the table as the paper printed
it and put its numbers beside another paper's:

| layer | table | holds |
|---|---|---|
| printed | `paper_comparison` | one printed table, or one dataset part of a split one: label, page, crop box, caption, footnote, our design note, review status |
| printed | `paper_comparison_column` | every grid column, stub included, with its role (`group`, `label`, `data`, `not_recorded`) |
| printed | `paper_comparison_header` | header cells with `col_start`/`col_end` and `header_row`/`header_row_end`, so a spanner, or a cell set across two header rows, stays one cell |
| printed | `paper_comparison_row` | every printed row, its label and row-group label |
| printed | `paper_comparison_cell` | every printed cell, text exactly as printed, plus the paper's own bold, underline and not-run marks |
| standard | `paper_comparison_result` | one measurement per cell (or per part of a two-value cell): method, variant, metric, level, canonical species and accession, value on 0-1, basis and its cue |
| standard | `paper_comparison_note` | every place the paper's bold or underline differs from the ranking of the values |

Plus the view **`paper_comparison_measurement`**, which flattens a result
with its paper, method and dataset and is where a standardised table starts.

**104 verified tables from 32 papers, 4348 measurements.** The 119 refusals the
reviewer confirmed are kept as `rejected`, with their reason and no cells, so
a refusal is a recorded decision and not an absence.

**A table whose ROWS are datasets carries a dataset per measurement.**
InstaNovo's results tables list eleven to fourteen evaluation sets down the
side, and only two of them are ProteomeTools; the caption mentions
ProteomeTools, so the whole table used to be filed under it. `ROW_DATASET` in
the miner maps each printed row label, per paper, to a dataset, version and
canonical name, taken from the paper's Data Availability statement:
- HeLa single-shot, HeLa degradome, nanobodies and *S. brodae* go to
  InstaNovo's own deposit, PXD044934;
- immunopeptidomics and snake venoms go to the deposits already catalogued;
- the wound exudates and Herceptin go to deposits added for them;
- HC-PT and AC-PT go to ProteomeTools.

`paper_comparison_result` therefore has its own `dataset_id` and
`dataset_version_id`, and the measurement view reads them before the table's.
The table itself records `one per row`, and a row the registry does not name,
such as the mean, has no dataset. A deposit with a single version takes it,
while a benchmark with several stays NULL. That is why InstaNovo's
Yeast/Bacillus/Mouse rows name no version, because the paper does not say.

π-PrimeNovo's Supplementary Table 6 has the same layout, with HCC,
IgG1-Human-HC, PT and three-species down the side, and is mapped the same
way. Three-species is GraphNovo's own test set on Zenodo 8000316, the deposit
LIPNovo and LIPNovo+ also compare on.

InstaNovo-FM's Tables S12 and S13 print no accession for their "six held-out
biological validation datasets". The Methods identify them anyway: they are
InstaNovo's application sets minus "the Immuno and Herceptin datasets". Four
keep InstaNovo's names (TPL Antibodies is glossed "nanobodies"). GluC is
InstaNovo's "HeLa GluC degradome", and Hela QC is, by elimination, HeLa
single-shot. Winnow's HeLa QC is a different set. The chain of evidence is
in the tables' design note, and the rows carry InstaNovo's canonical names, so
the two papers' numbers now meet on one subset.

**A protease is a subset, and the chain belongs in it.** Digest-level tables
(CrossNovo's antibody tables, π-PrimeNovo's Supplementary Table 7) record each
digest as the measurement's subset, canonicalised by `canonical_subset()`.
That covers the paper's typo 'Chymotrysin' and the glued 'ProteinaseK'.
CrossNovo prints 'HC Trypsin'; π-PrimeNovo prints 'Trypsin' in "the
IgG1-Human-HC dataset". `SUBSET_PREFIX` adds the 'HC' that dataset name
states, which is what lets the two papers' numbers meet on one subset.

**A per-residue table is refused (C4).** Precision on M(O), Q, F and K one
residue at a time is a different grain from a table's amino-acid precision.
Stored, "Q" would sit in the subset column beside *S. cerevisiae*.

**A crop beside running text starts at the caption.** A table set in a
wrapfigure shares its lines with prose, and the crop's left edge used to come
from that prose. When prose sits left of the 'Table N' label on the caption's
own line, the crop is bounded at the label, or at the table's own stub words
just left of it, since a centred label can be indented from its stub. Over
the whole library it moved six other crops, every one an improvement.

**Four parser faults, found on one table.** PLMNovo's Table 1 was refused
three times over, and each cause was general:
- A number is never a label with a year glued on. `0.1983` matched as `0.`
  plus the year 1983.
- A glued `ClassificationLoss` counts as a loss column, which is not recorded.
- Row groups separated by a RULE rather than by a wider gap are read by
  attaching each row to its nearest group label. That reading is kept only
  when every label sits at the middle of its group.
- **Never reuse `near` as a local name inside `emit()`.** That is its page
  text parameter, and shadowing it crashed dataset resolution on the next
  table.

Its MSKB rows are MassIVE-KB, with no version, under the split published with
Melendez et al.'s enzyme-bias data.

**A one-line centred caption has no pitch to stop on.** The caption walk ends
at a paragraph gap measured against the caption's own line spacing, so a
single centred line ran on into the paragraph under the table. That garbled
ReNovo's Table 8 caption and clipped its crop on the prose's right edge. Once
the caption read so far ends a sentence, a line with a word straddling the
caption's left edge stops the walk: that is the text block, not a caption
line. And a header wider than its numbers ('Peptide AUC') now widens the
crop, counting only header rows, since the prose underneath starts inside the
last column too. Over the library: no data changed and 14 crops moved, all
improvements.

**Three tables brought in eleven general rules, and two of the rules went
too far first.** SeqNovo's Table V, GA-Novo's Table 5 and the "abc to xyz"
Table 1 had all been refused. The rules that fixed them:
- **IEEE layout.** A bare centred 'TABLE V' over a wider centred caption
  takes that line whole. Small capitals arrive split ('T HE B EST') and are
  rejoined, but only in a caption with no lower-case letter. A block cannot
  run through a bare label line, so two IEEE tables set close together stay
  two tables.
- **Fake bold.** Text drawn three times arrives as '333000...999333'. It is
  collapsed and counted as the paper's bold.
- **Spaced spreads.** '0.89', '±', '0.03' becomes one cell.
- **Marker rows.** A row of significance markers ('(+) (=)') is read past.
- **Label below.** A label-only line just under an unlabelled value row
  labels it.
- **Not data.** A caption line is never data. Neither is a continuous line of
  prose.
- **Not-recorded columns.** An upper header line counts toward the
  not-recorded column test. `NOT_RECORDED_COLUMNS` names a column the header
  cannot.
- **Own method.** `TABLE_SELF` names a table's own method where the catalog
  link names another.

The two overreaches, both caught by a full re-parse before anything was
committed:
- **The label rule.** Breaking a block at ANY label line split approved
  tables on two-column pages, where the other column's 'Table 4: ...' shares
  a line with this table's rows. It is now bare labels only.
- **The prose rule.** Ten words with few numbers counted LIPNovo's PEAKS row
  (ten '-' markers) and AdaNovo's two-column row as prose, and five approved
  multi-dataset tables lost their header. It now counts only lettered words,
  and only on a line with no gap wider than a word space.

**A sentence that mentions a table is not its caption (C0).** A caption never
continues with a lower-case word. All five such "captions" in the library
were paragraphs opening "Table 3 reports...", refused before for whatever
the prose's numbers happened to trip.

**What the miner does not record is still in the printed layer.** A count
row, a BLEU row, a year or speed column, a difference row: each is a row or
column with role `not_recorded` and a `why`, holding its printed cells, so
the table renders as printed and none of it is mistaken for a measurement.
Getting there meant extending the miner, which used to throw those away
before the review payload was built.

**The header is read from the page, except where the page cannot be read.**
Most headers come straight from the miner's header walk, which needed four
fixes to be good enough to store:
- the walk is now bounded on the RIGHT by the table's own last number;
- empty rows no longer count against its four-row limit;
- touching one- or two-letter shreds are joined (`P r e c .`);
- a stub row keeps its reading order.

Six tables defeat it, the same six whose METHODS needed a curated column list:
DiffNovo's Table 1, BiATNovo's Table 2, Casanovo's Table 2, MemNovo's Table 6
and CrossNovo's two antibody tables. Their layout is transcribed from the
page in `LAYOUT_OVERRIDE`, which feeds only the printed layer. DiffNovo's
Table 1 is mis-typeset in the paper itself, with its method names wrapped
inside the metric spanners, and it is stored that way rather than tidied.

What remains imperfect is the TEXT, not the structure: where a PDF drops the
spaces, a label is stored as extracted (`Aminoacid-levelprecision(%)`). The
review page re-inserts them for display, and the stored string is never
silently "corrected".

**Our bold and underline are not stored.** They are a ranking of the stored
values within a measurement, recomputed wherever a table is drawn; storing
them would be a second copy that could disagree. The PAPER's marks are
stored, and the notes record each disagreement.

**One writer, rebuilt whole.** `review_comparisons.py --write-db` replaces
all seven tables from the parsed items and the committed sign-offs in
`paper_comparison_review.json`, and writes only signed-off tables. So the
result depends on the PDFs, the code and that file, and nothing else. These
tables are deliberately NOT in `.github/actions/commit-refreshed-db`: there
is no PDF library on a runner, and a sign-off is a human's.

**The basis search reads the PDF in reading order** (`reading_text`, via
pdftotext), not pdfplumber's page text. That text dropped narrow spaces and
read straight across two columns, so the cited sentences arrived as
'TheNine-speciesdataset,themost 6 LIPNovo:...' and appeared that way as page
tooltips. Clean text also exposed wrong bases. 'published results' had matched
the *released* rule, giving AdaNovo's quoted DeepNovo and PointNovo, and its
reproduced Casanovo, all 'released'. Pairwise's Casanovo got 'released' from
an unrelated sentence where its caption says the numbers are quoted. Those
are pinned in `TABLE_BASIS` from the paper's own words. Only the basis search
reads this text; method and dataset resolution keep theirs, and a full
re-parse changed no value, layout or verdict.

**The basis follows the paper, and every non-`unclear` basis cites its
sentence.** Invariants that must read zero, registered in `check_counts.py`:
0 verified tables without a result for the paper's own method, 0 duplicate
measurements within a table, 0 quoted results without a cue, and
0 results with a printed subset and no canonical one -- a subset that is not
a species (OC, UTI, a pNovo run) keeps its printed form as its canonical
name. That last one read 174 when first registered: left empty, a
standardised table built from the view lost its dataset column. A basis set by a
cell's legend marker cites that marker.

**A proceedings paper inherits its preprint's registry entries.**
`PROCEEDINGS_OF` in `build_paper_comparisons.py` maps 438, 439 and 440 to
their arXiv rows, and every entry keyed on the preprint (aliases, bases,
metric layouts, veto overrides) is copied to the proceedings row unless that
row has its own. Where the proceedings renumber, a label map says how, and a
label outside it is not copied: AdaNovo's NeurIPS paper drops the preprint's
PTM Table 2, so the preprint's Tables 3 to 5 are its Tables 2 to 4. The ICML
tables come out identical, cell for cell, to the preprints' approved ones;
AdaNovo's NeurIPS tables do not, since they add two baselines and ± spreads.

### Supplements, Extended Data, and a paper's own results

Many of the field's most-cited methods print no comparison table in the main
text at all: DeepNovo, PointNovo, InstaNovo, DeepNovo-DIA, pi-PrimeNovo, PepNet
and GraphNovo keep their headline results in figures and their tables in a
supplement. Three changes reach them:

- **Labels may carry a prefix.** `LABEL_ROW` accepts `Extended Data Table N`,
  `Supplementary Table N` and `Table SN`, glued or spaced. Before, a label had
  to START with 'Table', so InstaNovo's Extended Data tables were never read as
  captions. The page locator also takes a page with a table caption, one known
  method and six decimals, because a paper's own-results table names nothing
  else.
- **Supplementary PDFs are fetched and read.** `build_pdf_library.py
  supplements --ids ...` takes what a Springer Nature article page lists as
  Supplementary Information / Results / Tables / Data, skipping the Reporting
  Summary, peer-review file and source data, and files it in `supplements/`
  as `<paper's library name> - Supplementary N.pdf`. The SUBFOLDER is the
  point: `pdfs()` reads only the root, so coverage, rename and dedupe can never
  mistake a supplement for its paper and rename it onto, or deduplicate it
  against, the main PDF. Both the miner and the review page read a paper's
  main PDF and then each supplement, and a supplement table's id carries its
  source (`p21-si1-pg26-...`), because page numbers restart. PNAS and OUP answer
  scripted requests with 403 and are reported, not worked around.
- **A paper's own results are a kind of their own.** InstaNovo's results tables
  are datasets down the side and metrics across, with the method named only in
  the caption. `orientation()` accepts such a table as `kind = 'own_results'`
  when neither axis names a method, the caption names exactly one catalog
  method (longest name first, so 'InstaNovo+' is not 'InstaNovo') and the paper
  describes it. Own results feed the measurement view, and are kept OUT of the
  method pages' Reported comparisons, because they compare nothing.

**Tables that are images are read by two vision models, and kept only where
they agree.** `image_tables.find_image_tables()` takes a table caption with a
text-free gap of 60 pt or more beside it, the gap holding images or vector
drawings: the shape of InstaNovo's published Extended Data tables, whose text
layer has a caption and a footnote and nothing between. The review page crops
each one and lists it in `image_tables.json`;
`read_table_images.py --reader glm` and `--reader paddle` read the crops, each
in its own environment, into `vlm-cache/`. The table is resolved only when the
two grids have the same shape and EVERY cell matches (whitespace and case
aside), and then it goes through `emit()` like any text table, marked
`extraction = 'image'` all the way into `paper_comparison`. A disagreement is a
refusal naming the cells; a missing reading is `V0`, waiting. Either way a
person signs it off against the crop.

Two environment facts, both measured. GLM-OCR runs on transformers 5.
PaddleOCR-VL's own code calls `create_causal_mask(inputs_embeds=...)`, the
transformers-5 name, while needing `ROPE_INIT_FUNCTIONS['default']`, which
transformers 5 removed: no released version runs it as shipped (4.55, the
version its config names, and 4.57 both fail). `read_table_images.paddle_overlay()`
builds `~/.cache/paddleocr-vl-tf4` -- symlinks to the clone plus a copy of the
modeling file with that one argument renamed -- and it runs on 4.57.

The models also CHECK the text tables: `read_table_images.py --crosscheck`
reads every accepted or signed-off text table's crop, and
`crosscheck_tables.py` compares the parse with both readings ROW BY ROW, in
column order. Numbers are compared by value, so GLM's '0.7540' is the
printed '0.754'. Rows the miner DERIVED, not printed (TSARseqNovo's
Casanovo rows), are skipped. Both models agreeing with each other and not
with the parse (`CHECK`) points a person at a cell; it never edits anything.

Run over all 100 accepted and signed-off text tables: **86 AGREE, 14
ONE-READER, 0 CHECK, 0 DIFFER**. Every extracted number is confirmed by at
least one model and contradicted by neither. Each one-reader case is a model's
slip: PaddleOCR-VL dropping columns or reading 1.1 for 0.1, or GLM merging two
rows. A PaddleOCR-VL pass over all of them takes over two hours on the 8 GB
card, longer than one background job may run, but readings are cached per
table, so a restart picks up where it stopped.

**A chart that prints its values is read like a table, from the text layer.**
`FIGURE_TABLES` registers such a figure by its LAYOUT only:
- the page;
- the legend order, which is the bar order within each category;
- each panel's x-range and metric;
- the categories to leave out.

`bar_figure_grid()` reads the bar labels, assigns each label to its nearest
x-axis category, and orders the labels left to right. A category with the
wrong number of labels refuses the figure (F1), since one missing label would
shift every later value into the wrong series. Rotated labels come out of the
text layer reversed (`1489.0` is 0.9841) and are turned back. Nothing is
estimated from bar heights, and the result is marked `extraction = 'figure'`.

One entry so far: Deep Novo A+'s Fig. 3, which compares DeepNovo, DeepNovo
with each of A+'s two changes alone, and A+ itself, on three test-length
splits. Its 'train' category is accuracy on the training set and is left out.
It is opt-in per figure because most charts print no values.

That entry is also the worked example of a `veto`. Its data are the
nine-species benchmark's yeast submission (PXD003868), split at random rather
than held out, so the reviewer refused it: it supports only DeepNovo against
Deep Novo A+ inside that paper. A vetoed entry stays in the registry, so the
reading and the reason are both on record, and yields a refusal with no
measurements.

What still cannot be read is the TABLE THAT IS AN IMAGE WITH NO CAPTION IN THE
TEXT LAYER. DeepNovo-DIA's supplementary
tables are not in its Supplementary Information PDF at all: they are six Excel
files labelled "Supplementary Table 1-6". The fetcher now takes an `.xlsx`
whose label says Supplementary Table, saved under that label
(`supplement_sheets()` finds them). Read, none of the six is a comparison:
dataset statistics, per-feature predictions, immunoglobulin and variant
peptides. DeepNovo-DIA's comparisons exist only as figures, so no spreadsheet
reader was built for them; one would be worth writing the day a sheet holds
results.

### On the algorithm pages

**27 methods carry a `## Reported comparisons` section**, placed after
`## Benchmarks` so a reader meets the independently run numbers first. One
STANDARDISED table per printed table (or dataset part): methods down the side,
species then metric across, every value on 0-1 at the
printed precision (`0.530` stays `0.530`; a percentage table gains two
decimals). The dataset heads the table and links to its page; every method
links to its page. Bold and underline are our ranking per column.

It is built from `paper_comparison_result` alone and never from the printed
header, which is why DiffNovo's mis-typeset Table 1 comes out as clean as any
other. Three rules worth knowing:

- **A table lands on every method its paper DESCRIBES** (`is_self`), not just
  the first: LIPNovo+'s paper describes LIPNovo too, so its tables appear on
  both pages.
- **A table printed twice is shown once.** Two tables are the same when every
  method, variant, metric, level, species and value matches; the copy from the
  most authoritative publication is kept (peer-reviewed, conference,
  postprint, preprint, thesis, then the later date) and the other is named
  under it. The BASIS is left out of that comparison, because it is our
  reading of each paper's prose: CrossNovo's two preprints print the same
  Table 2 and only one's prose says 'retrained'. Tables that differ in any
  number are both shown -- BiATNovo's two preprints are, since one adds PepNet.
- **Measure first, then species.** The header's top row is the measure,
  level first ('Amino acid recall', 'Peptide precision'), one merged cell
  over all of its columns. The species sit in the row beneath, in the paper's
  order. Measures follow where each first appears in the printed table.
  Grouping by species first had split one measure across the whole width.
- **A wide table is stacked.** Past 12 data columns, the columns are split at
  measure boundaries into tables stacked one above another, with the same
  rows and corner. A single measure wider than 12 is split on its own.
  Casanovo's Table 2, five measures over nine species and 45 columns, is five
  tables of nine. A row with no value in a part is left out of that part.
- **The paper's marks are named in the table's own terms.** A note reads
  'column amino acid precision: the original table underlined pi-HelixNovo
  (0.765)', with the measure as in the header and the method by its catalog
  name. Before, it was 'precision / amino acid' and the paper's abbreviation
  ('HelixNovo'). The printed form is added only where two rows would
  otherwise read the same.
- **A basis is shown on baselines only.** It says how a paper got a number
  it did not produce. On the paper's own method it said nothing true:
  'LIPNovo · retrained' quoted a sentence about retraining Casanovo.
- **Escape an asterisk in a note.** The page is Markdown around this HTML,
  and Pandoc read the footnote markers in '0.491 / 0.725*' and '* Indicates
  ...' as emphasis: they paired up, vanished, and italicised the text
  between. Notes print `&#42;`.
- **A second metric in one printed cell is a note, not a column.** DiffNovo
  prints PepNet's Plasma cells as '0.491 / 0.725*' and '0.550/0.530*/0.664+',
  the marked numbers being other metrics, some quoted from PepNet's paper.
  Given rows and columns of their own they read as a misaligned table. The
  cell's own value stays in the grid, and a note under the table quotes the
  printed cell and the footnote sentence that explains its markers. The
  database keeps all three measurements; only the page changes.
- **The basis is not a column.** It is our reading of the paper's prose and
  not part of the printed table; as a column of its own it had no header and
  read 'unclear' down most of its length. Where a paper does say how a method
  was run, the word follows the method's name, with the licensing sentence as
  its tooltip, and 'unclear' shows nothing.
- **Bootstrap classes, not custom CSS.** `custom.scss` is in the publish's
  global render key, so styling the tables there would force a full render of
  every page for a section on 22 of them.

    uv run --with pdfplumber python3 review_comparisons.py --write-db            # full re-parse, ~8 min
    uv run --with pdfplumber python3 review_comparisons.py --rewrite --write-db  # from stored items, seconds

## Finding papers the catalog is missing

`build_candidates.py` answers "what should be in here that isn't", which the
database cannot answer by itself: `publication_citation` stores **only**
intra-catalog edges (0 of its rows point outside), so every reference to the
outside world is thrown away at build time. The script fetches the outside from
OpenAlex in both directions and scores each external work by **how many of our
publications link to it**, which is the signal that separates noise from a gap:
linked to one of our papers means nothing, linked to eight means something.

- **Backward** (works our papers cite) surfaces *foundational* gaps. The first
  run found "Fast algorithm for peptide sequencing by mass spectroscopy" (1990,
  29 of our papers cite it) and "PAAS 3: A computer program to determine
  probable sequence of peptides" (1984, 23).
- **Forward** (works citing our papers, scored by how many they cite) surfaces
  *new* work, and is the half worth re-running as the field moves.

Most high-scoring candidates are correctly out of scope: SEQUEST, Mascot,
MaxQuant, X!Tandem and target-decoy top the backward list because every de novo
paper cites a database-search baseline. That is expected, not a bug, and it is
why the script writes `candidates.csv` for review instead of inserting
anything. Apply the bar recorded in `WATCHLIST.md`: for a review, *de novo*
must be the subject; for an application paper, *de novo* must have produced part
of the result.

Already-rejected papers do not come back: the script excludes every DOI in
`WATCHLIST.md` and in `screening_decisions.tsv`, as well as everything already
in the catalog.

**`screening_decisions.tsv` is the bulk screenings' rejection list**, one DOI
per row, `rejected` or `held`, with the screener's reason and which screening
it came from (the 2026-10-04 citation sweep, the 2026-10-05 PRIDE search).
WATCHLIST.md argues about a paper in prose, which suits a dozen hand-made
calls and not 499. Without the file both tools re-proposed every sweep
rejection: `build_candidates.py` monthly, and the denovo-radar harvest, which
takes it as `--decisions`. `held` rows are excluded too, since they already
sit in a person's review queue; a held paper later added to the catalog is
simply catalogued, and its row can go.

## Mining the DNPS-DR feed

`build_dnps_candidates.py` reads the daily literature briefing at
<https://huggingface.co/spaces/yangtingpeng/DNPS-DR>, which is itself
publication 272, and reports which of its papers are not in the catalog. Same
contract as `build_candidates.py`: writes `dnps_candidates.csv`, inserts
nothing, touches no table. `refresh-dnps-candidates.yml` runs it monthly on the
3rd, the day after `refresh-candidates`.

The Space's repo carries `data/summaries.json`, a `{date: [{title, link, date,
summary}]}` dict of 1167 entries over 1030 dates, each `link` a PubMed URL. The
`summary` field is **ignored**: it holds the raw chain-of-thought of whatever
model wrote it, `<think>` blocks and all, in Chinese.

**TWO SENSES OF "DE NOVO", which is most of what the script is for.** The
feed's query is far broader than this catalog's scope. Of 1167 entries, 207 are
already catalogued -- reassuring, the feed does cover the field -- and of the
rest that say "de novo", most mean **de novo DESIGN**: RFdiffusion antibodies,
GLP-1 agonists, taste peptides, generative anything. A different field sharing
a Latin phrase. Others sequence a different analyte: glycans,
oligonucleotides, siRNA, DNA, transcriptomes. So a title must name sequencing
AND a peptide-ish analyte, and must miss both the design and wrong-analyte
vocabularies. **70 survive**, and every rejection is counted in the report
rather than dropped silently, because a filter nobody can see is a filter
nobody can correct.

**THE FEED IS STALE and a repo crawl cannot fix it.** Newest entry 2026-08-24,
file last committed 2026-08-27. The Space's own scheduler writes inside its
running container, so new days reach the git repo only when the author commits.
The script therefore surfaces the BACKLOG, which is large and worth having, and
will show nothing new until upstream commits again -- which is also why the
workflow is monthly rather than daily, and why an unchanged `lastModified`
costs one API call.

Two traps met while writing it. Europe PMC's `journalTitle` is **None** on
every `resultType=core` record; the venue lives at
`journalInfo.journal.title`, and reading the flat field wrote an empty column
for all 70. And resolving every entry's PMID to a DOI would be 1167 lookups,
so only the survivors are resolved -- but a DOI check against the catalog
afterwards still caught 4 papers the title match had missed.

`refresh-candidates.yml` runs it monthly on the 2nd, a day after
`refresh-citation-graph`. It commits nothing and needs only read permission:
`candidates.csv` is gitignored like the other builder audit CSVs, so the
shortlist goes into the **job summary** (readable in the Actions UI without
downloading) and the full file is attached as an artifact. **It must never use
`.github/actions/commit-refreshed-db`**: that action recovers from a push race
by hard-resetting to `origin/main` and replaying the one table it owns, and
this job owns no table.

## Working with the notebook

`plots.ipynb` is kept as an offline exploration / sanity-check tool only. It writes PNGs into `plots/` via `plt.savefig(...)` but those PNGs are **not committed** (the interactive Quarto site (`index.qmd`) replaces them). Use the notebook for ad-hoc SQL exploration or to cross-check what the Quarto site renders.

## The Quarto site

`index.qmd` + `_quarto.yml` produce an interactive site published to GitHub Pages at <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>. Architecture:

- A **single Python chunk** at the top of `index.qmd` queries `denovo.db` and calls `ojs_define(...)` for each dataset (publications, top authors, geography, institutions, co-authorship edges, author affiliations, algorithms, venues).
- **OJS cells** call Quarto's built-in `transpose()` to convert column-oriented data into row-oriented arrays, then render with **Observable Plot** (bars / scatter / timeline) and **d3-force** (co-authorship network). Every counter, axis label, and prose number flows from those datasets; never hardcode anything in the .qmd.
- `.github/workflows/publish.yml` rebuilds on every push to `main` and pushes to the `gh-pages` branch via `quarto-actions/publish@v2`. Cache is via `astral-sh/setup-uv@v3`; no PAT needed (uses `GITHUB_TOKEN`).

### The two network charts filter on papers per author, with a slider

The co-authorship network and the author-to-model graph used to hold every
author with three or more papers. After the backlog and the citation sweep that
was 382 authors and 376 author nodes, and both layouts collapsed into one ball.
Each chart now has a **Min papers per author** slider, 3 to 15, defaulting to
5. The query floor stays at 3 so the slider can go down to it, and
`author_papers` carries each author's count to the page. Measured in headless
Chrome: 180 and 147 circles at the default, 415 and 376 at 3, 54 and 36 at 10,
0 OJS errors. They are two sliders rather than one shared control because the
charts are a section apart.

The author-to-model graph also has **Max authors/paper**, because one
consortium paper links all its authors to one model: at the default it removes
exactly the 58 of 907 links that exist only through papers of more than 20
authors (verified, 907 to 849 lines). It has no **Min strength**, which
measures ties between co-authors, and that graph has none. Both Max sliders
range up to `max_paper_authors`, the largest byline, computed from the data.
That was a constant, 60, written when the largest paper had 53 authors, so
once 62- and 66-author papers arrived the collaboration network's "no cutoff"
default was silently dropping them.

### OJS source is public, Python chunk source is not

`echo: false` hides a cell's source from the rendered *page*, but Quarto still
embeds every **OJS** cell's source into `_site/index.html` and `_site/search.json`,
because the client-side OJS runtime needs it. So an OJS comment ships to the live
site and is full-text searchable. **Python** chunk comments really are dropped
(verified both ways: a distinctive comment from the Python chunk appears 0 times
in both files, one from an OJS cell appears in both).

Consequence: any note you do not want published (an unpublished analysis, a TODO
naming people, a half-finished finding) belongs in the Python chunk, not beside
the chart it describes. The network-statistics notes at the end of the Python
chunk in `index.qmd` are there for exactly this reason and say so.

### Every family gets a lane, and one switch turns it into the history chart

The architectures swim lane draws **every** family, in chronological order of
first appearance, with a lane exactly as tall as its labels need. Getting there
took three separate fixes, all of them still worth knowing:

**Every present family gets a lane.** The lane list used to be intersected with
a hand-written list of the 13 busiest families, so a family outside those 13 was
dropped from the chart entirely. The checkbox list offers all 49, so unchecking
everything and checking one of the other 36 produced an **empty chart**: 36
families and 76 of the 192 methods were unreachable.

**Lane order is chronological**, so the chart reads as the field's history
whether it shows every method or only each family's first. It is computed over **all** entries, not the
filtered ones, so a lane keeps its place as the filters change. The array runs
**latest-first** because lanes stack upward from `y = 0`, which means the array
is walked in order and NOT reversed: reversing put `Sparse autoencoder` on top
and `Heuristic` at the bottom, the opposite of chronological order. Verified in the
rendered SVG, top to bottom: Heuristic, Graph / DP, Sequence tag, Neural
network, Chemical labeling assisted, ... Flow, Palaeoproteomics workflow, Sparse
autoencoder.

**Lane height comes from packing, not from a table.** Both swim lanes now lay
their dots out in PIXELS: a label's width is estimated from its character count,
and a greedy first-fit pass reuses a row only when two labels cannot touch. A
lane is then `rows * 43 px` tall, 43 being two label lines plus the dot. The old
scheme assigned a tier from a date gap against a tier list sized to the BUSIEST
family, which handed a 2-unit lane 38 tier positions ~4 px apart and stacked its
labels on top of each other. `band_heights` and `subdomain_lane_height` are gone
with it; `band_color` and `subdomain_color` remain, as colour registries only.

**Every family also gets its own colour, from ONE scale.** The other 36 used to
share a single neutral grey, so most of the chart read as one undifferentiated
family. There were also three separate family-colour maps on the page, which is
what `family_color_scale` exists to prevent, so all three now defer to it: the
swim lane, the code-activity scatter and the author-model graph.

It has to colour **71** families: 49 real ones plus the 22 pseudo-families the
author-model graph mints for a family-less method (`Reviews`,
`Adjacent tools (misc)`, `Application: venomics`). Ten keep a hand-tuned value.
The rest are assigned from a grid of 24 hues at two CIE lightnesses and two
chromas, 96 candidates, by a farthest-point walk: each family takes the
candidate furthest in Lab from every colour already assigned, with distance to
its neighbours in the chronological walk as a tiebreaker.

Both halves of that were learned by measuring the wrong thing first. A single
24-hue ring at one lightness ran out after 24 and started reusing, and weighting
the walk-neighbour term above the global one gave good lane contrast and a
legend of alternating greens and purples. Now: 50 legend entries, 50 distinct
colours, exactly one pair under 10 deltaE, worst adjacent lane pair 23.9.
`Heuristic`, `Graph / DP`, `Learning-to-rank` and `Flow` lost their pinned
values on purpose -- the first three were near-identical greys at the top of the
chart, and Flow's `#937DC2` sat 5.8 deltaE from CNN's `#8172B3`, the worst pair
on the page.

LCh and not HSL throughout, because HSL at one lightness renders yellow far
paler than blue, and these colours carry a 10 px bold label as well as a dot.
Assigned over ALL families, so a family keeps its colour as filters change.

Measured: the default all-checked view is **49 lanes / 201 dots / 5779 px**, and
filtering to one family renders one lane at the 240 px floor (verified for
`Sequence tag`, 5 dots on one row).

**"The long view" is now a mode of this chart, not a chart of its own.** It
was one row per family at the first publication of its earliest method, which
is this swim lane with every method but the first removed, drawn a second
time. The **First appearance only** toggle does that removal instead:
- each lane keeps its earliest method among whatever the other filters leave;
- lanes shrink to one 26 px row, labelled beside the dot, flipping left near
  the right edge (two `Plot.text` marks, because dx is a constant);
- the dot grows with the number of methods the family went on to collect,
  which needs `r: {type: "identity"}`.

The chart, its table and the `family_firsts` query are gone. Measured: 52
dots in 1,412 px against 231 in 6,768, and 0 collisions in either mode. The
**nine years of quiet between 1981 and 1990** still read as a gap, because
the x axis is still time: `Heuristic` arrives with PAAS in April 1981 and the
second family, `Graph / DP`, not until Bartels in June 1990.

That gap used to be written here as 1984 to 1994, and it moved because the
catalog gained PAAS (1981) and Improved PAAS (1983). **PAAS 3 was the earliest
row in the catalog and was never the earliest program**: its own name says it is
the third, and the Osaka group published PAAS in 1981 and Improved PAAS in 1983,
neither of which was here. Its `short_description` said "one of the earliest",
which was the accurate hedge; promoting it to "the earliest" would have been
wrong twice over. The three are three `algorithm` rows rather than one versioned
row, which keeps PAAS 3's published URL and costs two near-identical pages.

A new family works with no registration at all: it gets a lane, a generated
colour and a packed height. Add it to `band_color` only if you want a specific
colour for it. `SELECT DISTINCT algorithm_family FROM algorithm` is the list.

### A deposit happens once

`role = 'introduces'` on `publication_dataset` says a paper produced the data.
Two depositing papers for one dataset version is legitimate in exactly one
shape: **a preprint and its version of record**, which both introduce it, the
same convention `publication_algorithm.role` uses for `'describes'`. The
nine-species revised benchmark has that shape, publications 113 and 98, and
they are linked in `publication_version`.

More than one introducing WORK is a role error, and there was one. Folding a
duplicate dataset row into nine-species moved its publication links across with
`role = 'introduces'` for ALL of them rather than preserving each role, which
promoted publication 220 -- a benchmarking paper that merely cites the Zenodo
deposit -- to being a depositor of the benchmark. It was invisible on the page
because the entry sat beside a genuine pair.

So the invariant is registered in `check_counts.py` and must read zero, and
does: **0** dataset versions have more than one introducing work once
preprint/version-of-record pairs are collapsed.

The page also prints each paper's `publication_type`, because a preprint and
its version of record share a title and a year, and without it the legitimate
pair renders as two identical lines and reads like duplicated data. Where there
is more than one depositing paper, the section says in one line why.

### Auditing the deposit roles

After the publication-220 error, all 33 `'introduces'` links were re-scored
against the dataset they claim to have deposited. **Every one survives**, and
the two that score low are the hand-set nine-species pair, where the paper says
"multi-species" and the dataset is named "Nine-species benchmark", so a low
title score is expected. The other checks: 0 dataset versions and 0 datasets
with more than one introducing work, and **0 of 544 `'uses'` links** score high
enough to have been a missed promotion.

**326 of 354 deposits have no depositing paper, and that is correct.** A
`'deposit'` is data some study produced, and most of those studies are
third-party proteomics papers that are not in this catalog and should not be.
The audit that matters is the inverse: an orphan deposit whose name matches a
paper that IS here. Five matched, one was real -- NovoBoard deposited
`PXD055277`, scoring 100 on a prefix comparison -- and it is now linked from
both its papers.

**The other four were false, from a one-sided length guard.** `token_set_ratio`
ignores unmatched tokens in EITHER string, so the guard has to require BOTH to
be long enough. Checking only the candidate let the catalog's own Zenodo record,
"Awesome De Novo Peptide Sequencing", match two unrelated deposits at 86, and
"Peptide Sequencing with Deep Learning" match a soil-metaproteomics deposit at
87, purely on the shared words. Re-scoring the written links with the two-sided
guard changed **no** verdict, so this never corrupted the data; it would have,
had the orphan matches been applied without reading them.

### Which dataset a checkpoint was trained on

`checkpoint_dataset` links the two, and it is **curated rather than derived**,
with an `evidence` column saying why each row exists. `checkpoint.trained_on` is
prose and cannot be joined on: its values include `~2M PSMs from MassIVE-KB v1 +
v2.0.15`, `the default --model orbitrap selector from v5.2.0 onward` and
`fine-tuned from model_massive.ckpt on the 2020-Cell-LUAD dataset`. A text match
would link a selector flag to nothing and miss the checkpoints whose only
evidence is a FILENAME.

8 links over 2 datasets, and the evidence differs per row, which is the reason
the column exists: a paper's own words for Casanovo 4.0.0 and 4.2.0, a Zenodo
record title for the two nine-species ones, the filename `model_massivekb.ckpt`
for RefineNovo-30M, and for π-PrimeNovo's phosphorylation model an **inherited**
link, since it was fine-tuned from `model_massive.ckpt` and its own fine-tuning
set, 2020-Cell-LUAD, is not in this catalog.

**Six of the eight assert no VERSION**, because the sources name the dataset and
not which of its versions, which is the same ambiguity the version list on the
page exists to expose. The page prints *not stated* rather than picking one.

The dataset pages show the table and then every evidence string beneath it. A
reader who disagrees with a link can see exactly what it rests on, which is the
point of not deriving them.

### A dataset page per dataset

`build_pages.py` generates one page per `dataset` row, all **377** of them,
reachable from the Dataset column of the Every-dataset table. The page carries
what no other page can: the **version list, each version with its own
addresses**, which is the answer to "which spectra was this number computed
over".

Three things it does deliberately:

- **Addresses hang off the VERSION**, because that is their grain. A page for
  the nine-species benchmark shows `MSV000081382` under `original` and
  `MSV000090982` plus two Zenodo records under `revised`, rather than one
  undifferentiated pile.
- **Provenance is labelled as provenance.** The nine per-species PRIDE
  submissions appear under "Assembled from 9 third-party submissions", named by
  species, not beside the addresses where the benchmark itself lives. They are
  other people's studies.
- **Papers split on the role**: "Deposited by" for `introduces` and "Used by"
  for the rest, and a paper that named no version says *version not stated*
  rather than going bare. Methods come through the DESCRIBING links only, so a
  venomics paper that ran PEAKS on a deposit does not make PEAKS a method of
  that deposit.

Keyed by `dataset.id`, since `dataset.name` is UNIQUE and there is nothing to
collapse. The table's Dataset cell now points at the page and a separate
**Source** column keeps the repository one click away, because the page is the
better default and the repository is still what someone downloading wants.

### A family page needs two methods

`build_pages.py` generates a page per architecture family, but only for the
**33 of 52** families that hold two or more methods. The other **19** hold
exactly one method, and a page for one of those would have carried that method's
papers, that method's authors and that method's dates: a copy of a page that
already exists, on a permanent indexed URL. Those cover **318** of the **337**
methods that carry a family. Contrast the application areas, where five
singletons still got a page each, because even a one-workflow area aggregates
papers, authors and countries that no other page collects.

The threshold is **derived, not curated**: `HAVING COUNT(*) >= 2`, written three
times -- in `slugs.py`'s `ENTITY_QUERIES`, in `build_pages.py`'s loader and in
`index.qmd`'s `_key_sql` -- because each of the three needs the member set for a
different reason and importing one into the others would couple the site's
Python chunk to the generator. So a family reaching two methods gains a page
with no other edit, which `slugs.py --check` reports as an ADDED slug and
passes. A family falling back to one method REMOVES a published URL: `--check`
fails and `render_scope.py` demands a full render. Both are the intended
loudness.

Keyed by `MIN(id)` over the family's `algorithm` rows, the same value-keyed
pattern institutions and venues use. There is deliberately no `family` table to
key on: `algorithm_family` is free text, and a table would have to be
hand-edited on every membership change. The cost is that deleting a family's
earliest algorithm row moves the key and so rewrites the URL.

Everything on the page comes through the `'describes'` links, for the same
reason the algorithm pages split on role: the snake-venom papers that ran
Casanovo are not papers about transformers.

**No lane label is dead.** `family_href` sends a family with a page to that page
and a one-method family straight to its single method, so every label in the
architectures swim lane is clickable, in either mode, and a reader never has to know which of the two kinds of target they
got.

**The one thing the page cannot derive is prose**, so `family_note` holds it:
`(name, blurb)`, one line per family on what its methods share, keyed by the
string `algorithm.algorithm_family` carries. A separate table rather than a
column on `algorithm`, because the fact is about the family and a column would
repeat it on all 37 Transformer (AR) rows with nothing keeping the copies equal.
It holds nothing but prose on purpose: membership is still the HAVING clause and
the URL is still `MIN(id)`, so a note cannot invent a family or move its page.

All 33 families with a page have a note. The two ways that can rot are
registered in `check_counts.py` as invariants that must read zero, the same
shape as the `subdomain` pair: 0 pages without a note, 0 notes without a page.
A missing note is otherwise invisible, because the page simply falls back to the
generated sentence.

The blurb doubles as the page's `<meta name="description">`, which is why each
one is written to fit inside the 250-character clip with the family name
prefixed. It is stored with a plain "de novo" and italicised at render, exactly
like the `subdomain` blurbs, since asterisks in a meta tag render literally.

### Plot's dx, dy and textAnchor are constants, not channels

Passing a function to `dx`, `dy` or `textAnchor` on a `Plot.text` mark applies
**neither**: Plot reads them once as constants, so a function silently degrades
to `dx: 0` and `textAnchor: "middle"`. Nothing warns, because a function is a
perfectly legal value to hand an option.

This is what put a dot in the middle of all 49 names in the former "long view"
chart, now the swim lane's first-appearance mode, which splits its marks the
same way. The
mark meant "labels right of the dot, except late ones, which flip left", written
as `dx: d => flips(d) ? -10 : 10`. What rendered was every label centred on its
own dot: 49 label-over-dot collisions, 9 px each, measured in the DOM. It also
made the marginRight measurement that accompanied it wrong, since a centred
label overflows half as far as a right-anchored one.

**Split the mark in two** and give each a constant, which is what the chart does
now: `Plot.text(rows.filter(d => !flips(d)), {dx: 11, textAnchor: "start"})` and
`Plot.text(rows.filter(flips), {dx: -11, textAnchor: "end"})`. 11 px clears the
largest dot, whose radius is `3 + 9 * 0.55`.

### No chart on the page has an overlapping label

Verified by measurement, not by eye. `check_chart_overlap.py` serves `_site`,
drives it in headless Chrome over CDP and, for all 20 chart SVGs, counts
text-text overlaps, text-over-dot overlaps and text outside the SVG frame from
real `getBoundingClientRect()` boxes. All three are **0**, re-audited after a
further 90 s so the two force simulations are included honestly. Run it after
any chart change:

```bash
uv run quarto render index.qmd          # it measures _site, so render first
uv run --with websockets python3 check_chart_overlap.py
```

It is deliberately NOT in the pre-commit hook or CI: it needs a browser and a
rendered site, and takes about a minute.

Four classes of defect turned up, and each has its own remedy in `index.qmd`:

1. **Swim-lane tiers** that assumed a lane was tall enough. Replaced by the
   pixel row packing described above.
2. **`dx`/`textAnchor` as functions**, above.
3. **An axis label on the tick baseline.** Plot puts the x-axis label level with
   the tick labels, where it touched a tick by 3 px in five charts. Fixed with
   `labelOffset: 40` and `marginBottom: 48`, which gives the label its own line.
4. **Estimated label widths that were too small.** Every greedy placer reserves
   a box from a character count, so an underestimate is an invisible licence to
   overlap: one estimate was even capped at 150 px while the text kept going,
   which hid a 67 px overhang. Estimates are now 6.2-7.0 px per character at
   10-12 px, and the citation-flow chart truncates a label at 26 characters so
   the estimate cannot drift far. If you add a label, re-run the audit rather
   than trusting the constant.

In the two force-directed charts every name cannot be shown at once: measured
123 and 457 overlapping label pairs. A greedy pass in descending degree (model
names first in the bipartite) tries six positions per name -- right of the dot,
left of it, and each nudged a line up or down -- and hides the name if none is
free, so about 100 of 173 authors and 103 of 360 bipartite nodes carry a visible
label. Nothing is lost: every node answers a hover with its full name, and the
hidden ones are the least connected. The pass is O(n^2), so it runs every fourth
tick rather than on all of them.

### Clamping a force layout hides the bug; it does not fix it

Both force charts clamp node positions in the tick handler, which stopped nodes
drifting off-canvas. It also produced a **worse-looking chart**: a ring of nodes
parked on the frame with an empty middle, 23% of the co-authorship nodes and 26%
of the bipartite's sitting within 4 px of an edge. A clamp turns divergence into
a neat pile against the wall, and the pile is the tell.

Two causes, both fixed by making the layout actually converge:

- **`forceManyBody` with no `distanceMax`** repels every pair at any distance, so
  the outward pressure grows with the square of the node count while the
  positional forces stay constant. At 173 and 360 nodes the equilibrium was
  wider than the frame. Capped at 170 px and 200 px, the repulsion only
  separates neighbours, which is all it was ever for.
- **Cluster targets on a ring.** Each affiliation (and each family, in the
  bipartite) was pulled toward `cos/sin(i/n)` at 0.3 of the frame, so all 108
  targets sat on one ellipse and none in the middle. They are now on a
  phyllotaxis disc, `r = sqrt((i + 0.5) / n)` at the golden angle, which is
  area-uniform and so fills the interior.

Measured after: 0 of 173 and 2 of 360 nodes near an edge, x spanning 122-1231 and
240-1299 of 1400. The bipartite also needed a taller frame, 650 to 820 px: at 360
nodes the density alone kept 19 of them pinned to the top and bottom however the
forces were tuned, which is a signal to give a graph more canvas rather than more
force. The clamp stays as a guard, and now almost never binds.

The knobs that matter, in the order worth trying: `distanceMax` on the charge,
then the shape of the positional targets, then the frame size, and only then the
force strengths. `wall_probe.js` (in the scratchpad, not the repo) counts nodes
within 4 px of the frame and reports the position spread, which is the number to
tune against -- the label-overlap audit passes happily on a ring.

### Plot tooltips truncate the value, not the label

`Plot`'s `tip` renders each line as "label value" and truncates it at
`lineWidth`, which defaults to 20em, roughly 40 characters. The ellipsis eats
the END of the line, which is the value. The Publication-lifecycle bars hit
this: the x channel is labelled from the SCALE, "Preprint → peer-reviewed gap
(months)" is 37 characters, and the tooltip showed no number at all, just "…".

`plot_tip_style` now sets `lineWidth: 34`, which is safe because none of the
five charts that share it put free text such as a paper title in a tip. Where a
channel's own name would be wrong or ugly, name it explicitly with `channels`
and switch the raw one off in `format`: the lifecycle bars pass
`channels: { "Method": "label", "Gap (months)": "gap_months" }` with
`format: { x1: false, x2: false, y: false }`, because `y` has no scale label and
Plot otherwise falls back to the field name, giving "label DiffuNovo".

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

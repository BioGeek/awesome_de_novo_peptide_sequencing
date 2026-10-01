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

**28 tables and one view.** Core catalog: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `algorithm_repository`, `publication`, `publication_algorithm`, `publication_author`, `publication_citation`, `publication_version`, `thesis_supervisor`, `subdomain`, `family_note`. Builder-owned tables, one set per refresh workflow: `repository_metrics`, `publication_impact`, `journal_impact`, and the nine `benchmark_*` / `proteobench_*` tables described under **Public benchmarks** below. Plus the `author_display` view, which appends a `disambiguator` in parentheses to the name; **every chart aggregates on `display_name`, not `author.name`**, because distinct researchers share a name (three different people are called Xiang Zhang). The view is defined as `SELECT a.*, ... FROM author a` on purpose: it used to list columns explicitly, which meant every new `author` column had to be hand-added to the view, and forgetting surfaced later as a baffling `no such column` from an unrelated query. `author` carries the external identifiers `orcid`, `openalex_id`, `scholar_id` and `sciprofiles_id`; 1163 of 1327 authors have at least one. One author is **not a person**: `Micromass UK Ltd` carries the vendor manual that documents PepSeq, because vendor documentation has a corporate author and every publication needs at least one (a convention, not a trigger). Both network charts gate on authors with three or more papers, so it stays out of the co-authorship graph and the bipartite chart.

Authors connect to publications via `publication_author` (with `author_order`) and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm` (with `role`, see **Describing a method or using it** below); thesis supervision lives in `thesis_supervisor` (`publication_id`, `author_id`) and deliberately NOT in `publication_author`, since a supervisor is not an author and recording them as one would inflate their publication count and forge a co-authorship edge; a trigger enforces that the publication is a thesis and that the supervisor is not also its author. Intra-catalog citation edges live in `publication_citation` (`citing_id`, `cited_id`, `source` ∈ `{crossref, semanticscholar, both}`). `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, `aliases`, `subdomain`) added after initial schema creation.

`publication.publication_type` is a string and the SQL column comment is stale: it names only `'preprint'` / `'peer-reviewed'`, but the full vocabulary in use is `'peer-reviewed'` (247), `'preprint'` (78), `'thesis'` (17), `'ML conference'` (9), `'resource'` (4, for citable things that are not manuscripts: this catalog's own Zenodo record, a third-party link collection, a daily literature-briefing Space, and a vendor software manual, the Micromass MassLynx NT BioLynx & ProteinLynx Guide, which is the only documentation PepSeq's method has), `'postprint'` (2), `'commentary'` (1) and `'abstract'` (3). Use one of those eight; do not invent a ninth without updating this list, and never leave it empty.

`'abstract'` is for a citable record with a DOI behind which **no full text will ever exist**: a meeting or showcase abstract. Publications 355 and 356 are in the Journal of Student-Scientists' Research (George Mason, ISSN 2689-7679), whose navigation is literally organised as "Abstracts by Department" and whose records carry no `citation_pdf_url` and no galley. Publication 357 is an ASBMB Annual Meeting abstract carried in a Journal of Biological Chemistry supplement: OpenAlex types it `conference-abstract`, Crossref holds no abstract text, and the title itself begins "Abstract 4402", all despite a jbc.org `/fulltext` URL that makes it look like a research article. All three come from the same George Mason host-defence peptide lab. Calling such a record `'peer-reviewed'` would be wrong twice over: it is faculty-mentored rather than peer-reviewed, and it would inflate a count this file and the site both report. The type was added rather than stretched because abstracts are a recurring shape, not a one-off: `WATCHLIST.md` had already parked the Hellbender ASBMB abstract on exactly this blocker, recording that it was "in scope on the merits" and waiting only because "no `publication_type` value fits without inventing an eighth".

`'postprint'` exists for a record posted to a preprint server AFTER the version of record, which is not the same thing as a preprint and must not be counted as one. Two cases so far. Publication 30 is an arXiv posting whose own comment field cites the BIBE 2023 conference paper it came from. Publication 352 is RankNovo's arXiv posting, `10.48550/arXiv.2505.17552`, dated 2025-05-23, which is AFTER ICLR 2025 in April; the conference version is publication 24, from OpenReview. Note the arXiv title differs from the conference one ("Universal Biological Sequence Reranking for Improved De Novo Peptide Sequencing" against "RankNovo: A Universal Reranking Approach for Robust De Novo Peptide Sequencing"), so unlike publication 30 it needs no slug suffix, and it is deliberately NOT linked through `publication_version`. **The arXiv id is the tell: a `25xx` id on a paper whose version of record predates it is a postprint, however the submitter labels it.** Typing it correctly keeps it out of both sides of the Publication lifecycle chart, which measures a preprint-to-journal gap that does not exist here, and out of `n_preprints`. Adding a type means touching four places besides this list: the wave chart's colour domain, the BibTeX `entry_type_of` map and its `note` field, and the slug suffix policy in `slugs.py` (publication 30 shares a title with 120, so without a semantic suffix its URL falls back to `-30`).

## Describing a method or using it

`publication_algorithm.role` says what a paper does with a method: `'describes'`
or `'uses'`. 48 of the 413 are `'uses'`, and they are concentrated rather than
spread: 18 of PEAKS's 21 papers are applications that ran it, mostly snake-venom
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

## Benchmark numbers on the method pages

The 17 methods with benchmark results carry a `## Benchmarks` section on their
page: median AP and median rank over the 84 datasets, plus the ProteoBench AUC
and precision for the 7 that have a submission.

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
column in the full table, which is how to see past the ten the list shows.

Two things to keep straight. The date shown beside each entry is the PAPER's
publication date, not when it was catalogued, and the list says so, because
several additions each month are older work that surfaced in a
`build_candidates.py` sweep. And the highest id can exceed the row count, since
a deleted row does not give its id back -- 362 against 361 publications today --
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
- **Coarser precision.** Month-only sources get `YYYY-MM-01`; 109 rows use day
  `01` and 102 of those are in non-January months, so a first-of-the-month date is
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

Coverage is 336/361. The 25 without one are mostly theses, conference pages and
records with no DOI, where no API has anything to give.

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

## URLs are a lock file

`slugs.lock` records the published URL of every generated page. A slug is
derived from mutable data, so editing a title or a name silently rewrites a URL,
404s the old one and discards its search equity. `python3 slugs.py --check`
fails on a CHANGED or REMOVED entry and passes on an ADDED one; it runs in
`.githooks/pre-commit` and in `check-slugs.yml`.

A **changed or removed** URL is never auto-fixed: it fails the commit, because
rewriting the baseline for those is the very failure being guarded against.
When a rename is intended, run `--write` and let the lock diff record it.

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
`WATCHLIST.md` as well as everything already in the catalog.

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

### Every family gets a lane; "The long view" is still the history chart

The architectures swim lane draws **every** family, in chronological order of
first appearance, with a lane exactly as tall as its labels need. Getting there
took three separate fixes, all of them still worth knowing:

**Every present family gets a lane.** The lane list used to be intersected with
a hand-written list of the 13 busiest families, so a family outside those 13 was
dropped from the chart entirely. The checkbox list offers all 49, so unchecking
everything and checking one of the other 36 produced an **empty chart**: 36
families and 76 of the 192 methods were unreachable.

**Lane order is chronological**, matching "The long view", so the two charts can
be read against each other. It is computed over **all** entries, not the
filtered ones, so a lane keeps its place as the filters change. The array runs
**latest-first** because lanes stack upward from `y = 0`, which means the array
is walked in order and NOT reversed: reversing put `Sparse autoencoder` on top
and `Heuristic` at the bottom, the opposite of the long view. Verified in the
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

**"The long view"** is still the right chart for the field's history, and is not
made redundant by this. It places one row per family at the first publication of
its earliest method, with an x-domain pinned to whole years, so the decade of
quiet between 1984 and 1994 reads as a gap.

A new family works with no registration at all: it gets a lane, a generated
colour and a packed height. Add it to `band_color` only if you want a specific
colour for it. `SELECT DISTINCT algorithm_family FROM algorithm` is the list.

### A family page needs two methods

`build_pages.py` generates a page per architecture family, but only for the
**24 of 49** families that hold two or more methods. The other **25** hold
exactly one method, and a page for one of those would have carried that method's
papers, that method's authors and that method's dates: a copy of a page that
already exists, on a permanent indexed URL. Those cover **173** of the **198**
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
architectures swim lane and every Family cell in the long-view table is
clickable, and a reader never has to know which of the two kinds of target they
got.

**The one thing the page cannot derive is prose**, so `family_note` holds it:
`(name, blurb)`, one line per family on what its methods share, keyed by the
string `algorithm.algorithm_family` carries. A separate table rather than a
column on `algorithm`, because the fact is about the family and a column would
repeat it on all 37 Transformer (AR) rows with nothing keeping the copies equal.
It holds nothing but prose on purpose: membership is still the HAVING clause and
the URL is still `MIN(id)`, so a note cannot invent a family or move its page.

All 24 families with a page have a note. The two ways that can rot are
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

This is what put a dot in the middle of all 49 names in "The long view". The
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

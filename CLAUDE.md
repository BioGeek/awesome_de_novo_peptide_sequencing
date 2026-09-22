# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A curated knowledge base covering the *de novo* peptide sequencing field: algorithms, post-processors, downstream applications, and adjacent tools, deep-learning and classical alike. The "code" is mostly data plumbing around two artifacts:

- `denovo.db`: SQLite database (the source of truth) holding publications, algorithms, authors, affiliations, cities, countries, and the join tables that link them.
- `denovo.sql`: full SQL dump of `denovo.db`, committed alongside the binary so diffs are reviewable in git. Treat `denovo.sql` as the canonical, human-readable representation; regenerate it after any DB write.
- `plots.ipynb`: Jupyter notebook that connects to `denovo.db`, runs SQL, and renders matplotlib figures (offline exploration / sanity-check only, not published).
- `index.qmd` + `_quarto.yml`: the Quarto site that renders interactive charts straight from `denovo.db`.
- `WATCHLIST.md`: tools that belong in the catalog but have no citable manuscript yet, plus things deliberately left out. Check it before concluding a tool is simply missing, and add to it rather than adding a method with no publication.

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
# automatically whenever denovo.db is staged, and check-counts.yml runs the
# bare check in CI, so you rarely need to call this by hand.
python3 check_counts.py           # report stale counts, exit 1 if any
python3 check_counts.py --fix     # rewrite them in place

# Backfill publication abstracts from bioRxiv / arXiv / OpenAlex / Crossref
# (offline, ~10 min). Skips publications that already have one, so it never
# overwrites hand-curated text; pass --force only if you mean to.
uv run python build_abstracts.py
```

### Scheduled refreshes (GitHub Actions)

All four builders also run on a cron in `.github/workflows/`, scoped to the
cadence at which each metric meaningfully moves. Each workflow commits only
when its data actually changed (no quiet-day churn) and then triggers
`publish.yml` to redeploy the site.

| Workflow                      | Script                       | Cadence                          | Cron expression  |
|-------------------------------|------------------------------|----------------------------------|------------------|
| `refresh-repo-metrics`        | `build_repo_metrics.py`      | Daily 06:00 UTC                  | `0 6 * * *`      |
| `refresh-publication-impact`  | `build_publication_impact.py`| Weekly Sun 06:30 UTC             | `30 6 * * 0`     |
| `refresh-citation-graph`      | `build_citations.py`         | Monthly 1st 07:00 UTC            | `0 7 1 * *`      |
| `refresh-journal-metrics`     | `build_journal_metrics.py`   | Semi-annual Jan 1 + Jul 1 08:00 UTC | `0 8 1 1,7 *` |

The slot-per-hour staircase is deliberate: when two workflows are scheduled
on the same calendar day (e.g. daily + weekly on a Sunday, all four on
Jan 1 / Jul 1) the earlier one finishes before the next one starts, so they
never race for `main` and the conditional-commit + `gh workflow run` chain
stays deterministic.

All four are also `workflow_dispatch`-able from the Actions tab if you need an
on-demand refresh (e.g., right after adding a new paper).

### Push races and `.github/actions/commit-refreshed-db`

The staircase only separates the workflows from *each other* — it can't stop a
**human** pushing while a refresh is mid-run. That did happen and broke a daily
run: the job checked out, rebuilt, committed, and by the time it pushed `main`
had moved, so `git push` was rejected and the whole workflow failed.

All four refresh workflows now commit through the shared composite action
`.github/actions/commit-refreshed-db`, which retries a rejected push (5 attempts,
increasing backoff). The interesting part is *how* it rebases, because a plain
`git pull --rebase` is not an option here: `denovo.db` is binary so it conflicts
every time, and a textual merge of `denovo.sql` can't be trusted.

Instead it exploits an invariant of this repo — **each refresh workflow is the
sole writer of exactly one table**:

| Workflow                     | Owns table            |
|------------------------------|-----------------------|
| `refresh-repo-metrics`       | `repository_metrics`  |
| `refresh-publication-impact` | `publication_impact`  |
| `refresh-citation-graph`     | `publication_citation`|
| `refresh-journal-metrics`    | `journal_impact`      |

On a rejected push it dumps just its own table (`sqlite3 denovo.db ".dump
<table>"`), hard-resets to `origin/main` to pick up whatever landed, replays its
rows on top, regenerates `denovo.sql`, and pushes again. Everything the other
side changed survives untouched, and the refreshed rows are not lost — no need
to re-run the (slow, network-bound) builder.

If you add a fifth refresh workflow, give it its own table and pass that table
as the action's `table:` input. If a workflow ever needs to write two tables,
the action needs extending first — replaying one table would silently drop the
other's new rows.

## Schema shape (read before editing data)

**16 tables and one view.** Core catalog: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `algorithm_repository`, `publication`, `publication_algorithm`, `publication_author`, `publication_citation`, `publication_version`, `thesis_supervisor`. Builder-owned metric tables, one per refresh workflow: `repository_metrics`, `publication_impact`, `journal_impact`. Plus the `author_display` view, which appends a `disambiguator` in parentheses to the name; **every chart aggregates on `display_name`, not `author.name`**, because distinct researchers share a name (three different people are called Xiang Zhang). The view is defined as `SELECT a.*, ... FROM author a` on purpose: it used to list columns explicitly, which meant every new `author` column had to be hand-added to the view, and forgetting surfaced later as a baffling `no such column` from an unrelated query. `author` carries the external identifiers `orcid`, `openalex_id`, `scholar_id` and `sciprofiles_id`; 921 of 1259 authors have at least one. One author is **not a person**: `Micromass UK Ltd` carries the vendor manual that documents PepSeq, because vendor documentation has a corporate author and every publication needs at least one (a convention, not a trigger). Both network charts gate on authors with three or more papers, so it stays out of the co-authorship graph and the bipartite chart.

Authors connect to publications via `publication_author` (with `author_order`) and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm`; thesis supervision lives in `thesis_supervisor` (`publication_id`, `author_id`) and deliberately NOT in `publication_author`, since a supervisor is not an author and recording them as one would inflate their publication count and forge a co-authorship edge; a trigger enforces that the publication is a thesis and that the supervisor is not also its author. Intra-catalog citation edges live in `publication_citation` (`citing_id`, `cited_id`, `source` ∈ `{crossref, semanticscholar, both}`). `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, `aliases`, `subdomain`) added after initial schema creation.

`publication.publication_type` is a string and the SQL column comment is stale: it names only `'preprint'` / `'peer-reviewed'`, but the full vocabulary in use is `'peer-reviewed'` (232), `'preprint'` (75), `'thesis'` (15), `'ML conference'` (9), `'resource'` (4, for citable things that are not manuscripts: this catalog's own Zenodo record, a third-party link collection, a daily literature-briefing Space, and a vendor software manual, the Micromass MassLynx NT BioLynx & ProteinLynx Guide, which is the only documentation PepSeq's method has), `'postprint'` (1) and `'commentary'` (1). Use one of those seven; do not invent an eighth without updating this list, and never leave it empty.

`'postprint'` exists for a record posted to a preprint server AFTER the version of record, which is not the same thing as a preprint and must not be counted as one. The single case is publication 30, an arXiv posting whose own comment field cites the BIBE 2023 conference paper it came from. Typing it correctly keeps it out of both sides of the Publication lifecycle chart, which measures a preprint-to-journal gap that does not exist here, and out of `n_preprints`. Adding a type means touching four places besides this list: the wave chart's colour domain, the BibTeX `entry_type_of` map and its `note` field, and the slug suffix policy in `slugs.py` (publication 30 shares a title with 120, so without a semantic suffix its URL falls back to `-30`).

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
- **Coarser precision.** Month-only sources get `YYYY-MM-01`; 104 rows use day
  `01` and 98 of those are in non-January months, so a first-of-the-month date is
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

Five rows legitimately keep 1 January (146, 158, 187, 286 and 310: Mass Spectrometry
Reviews 34(1), Mol Cell Proteomics 8(1), AIChE Journal 53(1), J Biol Chem 279(1),
Biomedical Chemistry: Research and Methods 1(1)) because each really is a
January issue. Publication 196 keeps a year-only `2013-01-01` because its source,
a Digital Commons ETD record, publishes "Date of Award 2013" with no month.

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

Coverage is 280/337. The 57 without one are mostly theses, conference pages and
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

## Citation graph

`build_citations.py` is the offline builder: it walks every publication, queries Crossref (by DOI) and Semantic Scholar (by DOI or title search), resolves references back to local publication ids by DOI-exact or fuzzy-title match (token-set ratio ≥ 92), and inserts edges into `publication_citation`. Fuzzy matches are also logged to `citation_audit.csv` for human review. The script is intentionally NOT run by CI; it's ~30 min of network I/O and Semantic Scholar rate-limits hard. Re-run locally when new papers are added, eyeball the audit CSV, then commit the regenerated `denovo.db` + `denovo.sql`.

When adding rows by hand, always check whether the entity already exists before inserting: author names and affiliation `(name, department)` pairs are the natural keys, not the surrogate IDs. A typical insert path for a new paper is: `country` → `city` → `affiliation` → `author` → `author_affiliation` → `algorithm` → `publication` → `publication_author` (with `author_order` set per author) → `publication_algorithm`. See any of the previously committed paper insertions (e.g. the `CausalNovo` commit) for the standard `INSERT … SELECT id FROM …` pattern.

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

### Editorial conventions

- **Italicize *de novo*** in every piece of user-facing copy (page title, subtitle, prose, chart titles, README). In markdown: `*de novo*`. In HTML cells: `<em>de novo</em>`. Don't italicize it inside copied paper titles, DB string literals, or identifiers.
- **Scope is comprehensive**: frame the site as a map of the whole field (algorithms + post-processors + downstream apps + adjacent tools, DL and classical alike). Do **not** re-introduce a "deep-learning only" disclaimer; previous versions had one and it's been removed.

### Classification taxonomy

Every `algorithm` row carries three classifier columns:

- `kind`: `'algorithm'`, `'post-processor'`, `'downstream-application'`, `'adjacent'`, `'review'`, `'benchmark'`, or `'meta'` (residual catch-all for commentaries / theses-without-method).
- `is_deep_learning`: `1` (TRUE), `0` (FALSE), or NULL.
- `acquisition_mode`: `'DDA'`, `'DIA'`, `'both'`, or NULL.

When adding a new entry, fill all three. The site's filters (and the hero counters) depend on them.

`algorithm.subdomain` is a free-text slug used only by `kind='downstream-application'`
rows (17 values in use: `venomics`, `palaeoproteomics`, `immunopeptidomics`,
`antibodyomics`, `glycoproteomics`, `astrobiology` and others). **A new subdomain
must also be registered in the four `subdomain_*` OJS cells in `index.qmd`**
(`subdomain_order`, `subdomain_label`, `subdomain_color`, `subdomain_lane_height`),
which feed both the Application-areas swim lanes and the Sankey diagram. All four
must carry the same key set.

Forgetting used to be fatal: an unregistered subdomain made the timeline
dereference a missing lane and throw `TypeError: Cannot read properties of
undefined (reading 'y0')`, which kills that OJS cell and every one after it on
the page. Both charts now fall back to the raw slug and a neutral grey instead,
and the timeline appends unregistered subdomains rather than dropping them, so a
missing registration degrades visibly instead of breaking the page. Register it
anyway: the fallback is a safety net, not the intended appearance. Pick a colour
at least ~20 CIE Lab deltaE from the existing ones, and dark enough to read as a
small dot (the existing set's own minimum pairwise distance is 11.1).

### Local dev

```bash
uv run quarto preview        # live-reload at http://localhost:4200
uv run quarto render         # one-shot build into _site/
```

### Updating data → updating the site

Edit `denovo.db` directly (sqlite3 CLI / DB Browser / any SQLite tool) → `sqlite3 denovo.db .dump > denovo.sql` → commit both `denovo.db` and `denovo.sql` → push to `main`. The Action rebuilds and republishes within ~2-3 minutes. **No manual `plt.savefig` step anymore.**

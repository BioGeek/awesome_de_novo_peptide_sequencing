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

**Fourteen tables and one view.** Core catalog: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `algorithm_repository`, `publication`, `publication_algorithm`, `publication_author`, `publication_citation`. Builder-owned metric tables, one per refresh workflow: `repository_metrics`, `publication_impact`, `journal_impact`. Plus the `author_display` view, which appends a `disambiguator` in parentheses to the name; **every chart aggregates on `display_name`, not `author.name`**, because distinct researchers share a name (three different people are called Xiang Zhang).

Authors connect to publications via `publication_author` (with `author_order`) and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm`; intra-catalog citation edges live in `publication_citation` (`citing_id`, `cited_id`, `source` ∈ `{crossref, semanticscholar, both}`). `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, `aliases`, `subdomain`) added after initial schema creation.

`publication.publication_type` is a string and the SQL column comment is stale: it names only `'preprint'` / `'peer-reviewed'`, but the full vocabulary in use is `'peer-reviewed'` (183), `'preprint'` (69), `'ML conference'` (9), `'thesis'` (6), `'resource'` (3, for field resources that have no manuscript: this catalog's own Zenodo record, a third-party link collection, and a daily literature-briefing Space) and `'commentary'` (1). Use one of those six; do not invent a seventh without updating this list, and never leave it empty.

## Abstracts

`build_abstracts.py` fills `publication.abstract`, trying bioRxiv, arXiv,
OpenAlex and Crossref in that order and recording which one won in
`publication.abstract_source`. A NULL `abstract_source` alongside a non-empty
`abstract` means the text was entered by hand and is authoritative: the script
skips those rows unless `--force`, so don't pass `--force` casually.

Coverage is 219/267. The 48 without one are mostly theses, conference pages and
records with no DOI, where no API has anything to give.

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

## Working with the notebook

`plots.ipynb` is kept as an offline exploration / sanity-check tool only. It writes PNGs into `plots/` via `plt.savefig(...)` but those PNGs are **not committed** (the interactive Quarto site (`index.qmd`) replaces them). Use the notebook for ad-hoc SQL exploration or to cross-check what the Quarto site renders.

## The Quarto site

`index.qmd` + `_quarto.yml` produce an interactive site published to GitHub Pages at <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>. Architecture:

- A **single Python chunk** at the top of `index.qmd` queries `denovo.db` and calls `ojs_define(...)` for each dataset (publications, top authors, geography, institutions, co-authorship edges, author affiliations, algorithms, venues).
- **OJS cells** call Quarto's built-in `transpose()` to convert column-oriented data into row-oriented arrays, then render with **Observable Plot** (bars / scatter / timeline) and **d3-force** (co-authorship network). Every counter, axis label, and prose number flows from those datasets; never hardcode anything in the .qmd.
- `.github/workflows/publish.yml` rebuilds on every push to `main` and pushes to the `gh-pages` branch via `quarto-actions/publish@v2`. Cache is via `astral-sh/setup-uv@v3`; no PAT needed (uses `GITHUB_TOKEN`).

### Editorial conventions

- **Italicize *de novo*** in every piece of user-facing copy (page title, subtitle, prose, chart titles, README). In markdown: `*de novo*`. In HTML cells: `<em>de novo</em>`. Don't italicize it inside copied paper titles, DB string literals, or identifiers.
- **Scope is comprehensive**: frame the site as a map of the whole field (algorithms + post-processors + downstream apps + adjacent tools, DL and classical alike). Do **not** re-introduce a "deep-learning only" disclaimer; previous versions had one and it's been removed.

### Classification taxonomy

Every `algorithm` row carries three classifier columns:

- `kind`: `'algorithm'`, `'post-processor'`, `'downstream-application'`, `'adjacent'`, `'review'`, `'benchmark'`, or `'meta'` (residual catch-all for commentaries / theses-without-method).
- `is_deep_learning`: `1` (TRUE), `0` (FALSE), or NULL.
- `acquisition_mode`: `'DDA'`, `'DIA'`, `'both'`, or NULL.

When adding a new entry, fill all three. The site's filters (and the hero counters) depend on them.

### Local dev

```bash
uv run quarto preview        # live-reload at http://localhost:4200
uv run quarto render         # one-shot build into _site/
```

### Updating data → updating the site

Edit `denovo.db` directly (sqlite3 CLI / DB Browser / any SQLite tool) → `sqlite3 denovo.db .dump > denovo.sql` → commit both `denovo.db` and `denovo.sql` → push to `main`. The Action rebuilds and republishes within ~2-3 minutes. **No manual `plt.savefig` step anymore.**

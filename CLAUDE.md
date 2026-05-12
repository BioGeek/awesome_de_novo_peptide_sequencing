# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A curated knowledge base of deep-learning de novo peptide sequencing models. The "code" is mostly data plumbing around two artifacts:

- `denovo.db` — SQLite database (the source of truth) holding publications, algorithms, authors, affiliations, cities, countries, and the join tables that link them.
- `denovo.sql` — full SQL dump of `denovo.db`, committed alongside the binary so diffs are reviewable in git. Treat `denovo.sql` as the canonical, human-readable representation; regenerate it after any DB write.
- `plots.ipynb` — Jupyter notebook that connects to `denovo.db`, runs SQL, and renders matplotlib figures (offline exploration / sanity-check only — not published).
- `index.qmd` + `_quarto.yml` — the Quarto site that renders interactive charts straight from `denovo.db`.

## Common commands

```bash
# Environment (Python >=3.12, managed by uv)
uv sync                          # install deps from pyproject.toml / uv.lock
uv run jupyter notebook plots.ipynb

# Regenerate the SQL dump after any change to denovo.db — commit BOTH files together
sqlite3 denovo.db .dump > denovo.sql

# Rebuild denovo.db from the dump (e.g. after pulling a commit that changed denovo.sql)
rm denovo.db && sqlite3 denovo.db < denovo.sql

# Quick inspection
sqlite3 denovo.db ".tables"
sqlite3 denovo.db "SELECT name, algorithm_family FROM algorithm ORDER BY name;"
```

## Schema shape (read before editing data)

Nine tables: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `publication`, `publication_algorithm`, `publication_author`. Authors connect to publications via `publication_author` and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm`. `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`) added after initial schema creation. `publication.publication_type` is a string constrained by convention to `'preprint'` or `'peer-reviewed'`.

When adding rows by hand, always check whether the entity already exists before inserting — author names and affiliation `(name, department)` pairs are the natural keys, not the surrogate IDs. A typical insert path for a new paper is: `country` → `city` → `affiliation` → `author` → `author_affiliation` → `algorithm` → `publication` → `publication_author` (with `author_order` set per author) → `publication_algorithm`. See any of the previously committed paper insertions (e.g. the `CausalNovo` commit) for the standard `INSERT … SELECT id FROM …` pattern.

## Working with the notebook

`plots.ipynb` is kept as an offline exploration / sanity-check tool only. It writes PNGs into `plots/` via `plt.savefig(...)` but those PNGs are **not committed** — the interactive Quarto site (`index.qmd`) replaces them. Use the notebook for ad-hoc SQL exploration or to cross-check what the Quarto site renders.

## The Quarto site

`index.qmd` + `_quarto.yml` produce an interactive site published to GitHub Pages at <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>. Architecture:

- A **single Python chunk** at the top of `index.qmd` queries `denovo.db` and calls `ojs_define(...)` for each dataset (publications, top authors, geography, institutions, co-authorship edges, author affiliations, algorithms, venues).
- **OJS cells** call Quarto's built-in `transpose()` to convert column-oriented data into row-oriented arrays, then render with **Observable Plot** (bars / scatter / timeline) and **d3-force** (co-authorship network). Every counter, axis label, and prose number flows from those datasets — never hardcode anything in the .qmd.
- `.github/workflows/publish.yml` rebuilds on every push to `main` and pushes to the `gh-pages` branch via `quarto-actions/publish@v2`. Cache is via `astral-sh/setup-uv@v3`; no PAT needed (uses `GITHUB_TOKEN`).

### Editorial conventions

- **Italicize *de novo*** in every piece of user-facing copy (page title, subtitle, prose, chart titles, README). In markdown: `*de novo*`. In HTML cells: `<em>de novo</em>`. Don't italicize it inside copied paper titles, DB string literals, or identifiers.
- **State the scope** ("deep learning only, not a historical survey") in the page subtitle, the hero scope banner, the meta description, and the footer.

### Local dev

```bash
uv run quarto preview        # live-reload at http://localhost:4200
uv run quarto render         # one-shot build into _site/
```

### Updating data → updating the site

Edit `denovo.db` directly (sqlite3 CLI / DB Browser / any SQLite tool) → `sqlite3 denovo.db .dump > denovo.sql` → commit both `denovo.db` and `denovo.sql` → push to `main`. The Action rebuilds and republishes within ~2-3 minutes. **No manual `plt.savefig` step anymore.**

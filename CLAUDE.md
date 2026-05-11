# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A curated knowledge base of deep-learning de novo peptide sequencing models. The "code" is mostly data plumbing around two artifacts:

- `denovo.db` — SQLite database (the source of truth) holding publications, algorithms, authors, affiliations, cities, countries, and the join tables that link them.
- `denovo.sql` — full SQL dump of `denovo.db`, committed alongside the binary so diffs are reviewable in git. Treat `denovo.sql` as the canonical, human-readable representation; regenerate it after any DB write.
- `related_literature.csv` — the raw paper list that seeds the DB.
- `plots.ipynb` — Jupyter notebook that connects to `denovo.db`, runs SQL, and renders the figures in `plots/`.
- `populate_remaining.py` — one-shot ingestion script that reads `related_literature.csv` and inserts new rows into `denovo.db` via idempotent `get_or_create_*` helpers (country → city → affiliation → author → algorithm → publication, with join-table linking).

## Common commands

```bash
# Environment (Python >=3.12, managed by uv)
uv sync                          # install deps from pyproject.toml / uv.lock
uv run jupyter notebook plots.ipynb

# Regenerate the SQL dump after any change to denovo.db — commit BOTH files together
sqlite3 denovo.db .dump > denovo.sql

# Rebuild denovo.db from the dump (e.g. after pulling a commit that changed denovo.sql)
rm denovo.db && sqlite3 denovo.db < denovo.sql

# Re-run the ingestion script (idempotent — safe to re-run)
uv run python populate_remaining.py

# Quick inspection
sqlite3 denovo.db ".tables"
sqlite3 denovo.db "SELECT name, algorithm_family FROM algorithm ORDER BY name;"
```

## Schema shape (read before editing data)

Nine tables: `author`, `country`, `city`, `affiliation`, `author_affiliation`, `algorithm`, `publication`, `publication_algorithm`, `publication_author`. Authors connect to publications via `publication_author` and to affiliations via `author_affiliation`; publications connect to algorithms via `publication_algorithm`. `algorithm` has extra denormalized columns (`algorithm_family`, `short_description`) added after initial schema creation. `publication.publication_type` is a string constrained by convention to `'preprint'` or `'peer-reviewed'`.

When adding rows by hand, always go through `populate_remaining.py`'s `get_or_create_*` pattern (or replicate it) so existing entities are reused — author names and affiliation `(name, department)` pairs are matched as natural keys, not by ID.

## Working with the notebook

`plots.ipynb` writes PNGs into `plots/` via `plt.savefig(...)`. Re-running the notebook overwrites them; commit the regenerated PNGs alongside any data change so the rendered figures stay in sync with the DB.

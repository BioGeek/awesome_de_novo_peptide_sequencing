# Awesome *De Novo* Peptide Sequencing

An interactive, data-driven map of **deep-learning** approaches to *de novo* peptide sequencing.

🌐 **Live site:** https://biogeek.github.io/awesome_de_novo_peptide_sequencing/

## Scope

This repository tracks papers that apply **deep learning** to *de novo* peptide sequencing. It is **not** a historical survey of the field — pre-deep-learning approaches (PEAKS, Lutefisk, PepNovo, NovoHMM, …) are deliberately out of scope.

## What's here

- **`denovo.db`** — SQLite database of papers, models, authors, affiliations, cities, countries, and venues. The source of truth.
- **`denovo.sql`** — committed SQL dump of `denovo.db` so diffs are reviewable in git.
- **`related_literature.csv`** — raw paper list that seeds the DB.
- **`populate_remaining.py`** — idempotent ingestion script.
- **`index.qmd` + `_quarto.yml`** — the Quarto site (single-page narrative with interactive charts powered by Observable JS).
- **`plots.ipynb`** — Jupyter notebook for offline exploration / sanity checks (static matplotlib figures, not published).

## Contributing a paper

1. Add a row to `related_literature.csv` (model name, title, authors, date, publisher, repository, model weights, PDF).
2. Run the ingestion script and regenerate the SQL dump:
   ```bash
   uv run python populate_remaining.py
   sqlite3 denovo.db .dump > denovo.sql
   ```
3. Commit both `denovo.db` and `denovo.sql` (plus the CSV row) and open a PR. The GitHub Action rebuilds the site and pushes to `gh-pages` on merge.

## Preview locally

```bash
uv sync
uv run quarto preview
```

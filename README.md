# Awesome *De Novo* Peptide Sequencing

An interactive, data-driven map of **deep-learning** approaches to *de novo* peptide sequencing.

🌐 **Live site:** https://biogeek.github.io/awesome_de_novo_peptide_sequencing/

## Scope

This repository tracks papers that apply **deep learning** to *de novo* peptide sequencing. It is **not** a historical survey of the field — pre-deep-learning approaches (PEAKS, Lutefisk, PepNovo, NovoHMM, …) are deliberately out of scope.

## What's here

- **`denovo.db`** — SQLite database of papers, models, authors, affiliations, cities, countries, and venues. **The source of truth.**
- **`denovo.sql`** — committed SQL dump of `denovo.db` so diffs are reviewable in git.
- **`index.qmd` + `_quarto.yml`** — the Quarto site (single-page narrative with interactive charts powered by Observable JS).
- **`plots.ipynb`** — Jupyter notebook for offline exploration / sanity checks (static matplotlib figures, not published).

## Contributing a paper

Edit `denovo.db` directly — any SQLite tool works (`sqlite3` CLI, [DB Browser for SQLite](https://sqlitebrowser.org/), DataGrip, …). A new paper typically needs:

- One row in **`algorithm`** if the model is new (set `name`, `repository`, `algorithm_family`, `short_description`).
- One row in **`publication`** (`title`, `publication_date`, `doi`, `publisher`, `url`, `journal`, `publication_type`).
- One row per author in **`publication_author`** with the `author_order` field set.
- One row in **`publication_algorithm`** connecting the new publication to its model(s).
- Affiliations: insert into **`country` → `city` → `affiliation`** and link each author with **`author_affiliation`** (re-use existing rows where possible — author names and `(affiliation.name, department)` are the natural keys).

Then regenerate the human-readable dump so the diff is reviewable:

```bash
sqlite3 denovo.db .dump > denovo.sql
git add denovo.db denovo.sql
```

Open a PR with both files. The GitHub Action rebuilds the site and pushes to `gh-pages` on merge — typically live within ~3 minutes.

## Preview locally

```bash
uv sync
uv run quarto preview
```

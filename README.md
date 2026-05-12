# Awesome *De Novo* Peptide Sequencing

A comprehensive, interactive map of the *de novo* peptide sequencing field — algorithms, post-processors, downstream applications, and adjacent tools, deep-learning and classical alike.

🌐 **Live site:** <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>

## Scope

The repository tracks the *de novo* peptide sequencing field broadly. Every paper is classified by:

- **`kind`** — `algorithm` (core sequencer), `post-processor` (re-ranker / FDR / refinement), `downstream-application` (uses de novo output for biology, e.g. neoantigen discovery), `adjacent` (DB search hybrids, glycopeptide tools), or `meta` (reviews / theses / benchmarks).
- **`is_deep_learning`** — `TRUE` / `FALSE` — so readers can compare DL-based and classical approaches side by side.
- **`acquisition_mode`** — `DDA`, `DIA`, `both`, or *not applicable*.

Classical methods (PEAKS, Lutefisk, PepNovo, NovoHMM, pNovo 3, …) belong here as much as the modern Transformer-based sequencers. Filtering on the live site lets you drill into any subset.

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

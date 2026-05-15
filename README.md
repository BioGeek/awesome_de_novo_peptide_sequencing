# Awesome *De Novo* Peptide Sequencing

A comprehensive, interactive map of the *de novo* peptide sequencing field — algorithms, post-processors, downstream applications, and adjacent tools, deep-learning and classical alike.

🌐 **Live site:** <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>

## Scope

The repository tracks the *de novo* peptide sequencing field broadly. Every paper is classified by:

- **`kind`** — one of: `algorithm` (core sequencer), `post-processor` (re-ranker / FDR / refinement), `downstream-application` (uses de novo output for biology, e.g. neoantigen discovery), `adjacent` (DB search hybrids, glycopeptide tools), `review` (literature survey), `benchmark` (evaluation framework / dataset), or `meta` (everything else: commentaries, theses without a method, …).
- **`is_deep_learning`** — `TRUE` / `FALSE` — so readers can compare DL-based and classical approaches side by side.
- **`acquisition_mode`** — `DDA`, `DIA`, `both`, or *not applicable*.


## What's here

- **`denovo.db`** — SQLite database of papers, models, authors, affiliations, cities, countries, and venues. **The source of truth.**
- **`denovo.sql`** — committed SQL dump of `denovo.db` so diffs are reviewable in git.
- **`index.qmd` + `_quarto.yml`** — the Quarto site (single-page narrative with interactive charts powered by Observable JS).
- **`plots.ipynb`** — Jupyter notebook for offline exploration / sanity checks (static matplotlib figures, not published).

## Contributing

**Easiest:** [open an issue](https://github.com/BioGeek/awesome_de_novo_peptide_sequencing/issues/new) with a link to the paper (DOI, arXiv, bioRxiv, OpenReview, …) and I'll wire it into the database for you. Corrections — wrong author lists, missing affiliations, mis-classified `kind` / `is_deep_learning` / `acquisition_mode`, broken links, you name it — are equally welcome via issue.

### Advanced: edit the database directly

If you're comfortable with SQLite, the source of truth is `denovo.db` and you can edit it with any tool — `sqlite3` CLI, [DB Browser for SQLite](https://sqlitebrowser.org/), DataGrip. A new paper typically needs:

- One row in **`algorithm`** if the model is new (set `name`, `repository`, `algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`).
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

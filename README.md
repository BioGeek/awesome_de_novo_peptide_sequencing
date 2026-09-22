
# Awesome *De Novo* Peptide Sequencing

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.20825737-blue.svg)](https://doi.org/10.5281/zenodo.20825737)

A comprehensive, curated, and interactive map of the *de novo* peptide sequencing field. Algorithms, post-processors, downstream applications and adjacent tools, covering both deep-learning and classical approaches. Includes a SQLite database of papers, models, authors, affiliations, and venues, alongside a Quarto-based interactive website with Observable JS visualisations tracking publication impact, journal metrics, and GitHub activity across the field, plus a generated page for every paper, author, method, institution and venue.

🌐 **Live site:** <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>

## Scope

The repository tracks the *de novo* peptide sequencing field broadly. Every catalogued entry carries three classifier columns on its `algorithm` row, which its papers inherit through `publication_algorithm`:

- **`kind`**: one of: `algorithm` (core sequencer), `post-processor` (re-ranker / FDR / refinement), `downstream-application` (uses de novo output for biology, e.g. neoantigen discovery), `adjacent` (DB search hybrids, glycopeptide tools), `review` (literature survey), `benchmark` (evaluation framework / dataset), or `meta` (everything else: commentaries, theses without a method, …).
- **`is_deep_learning`**: `TRUE` / `FALSE`, so readers can compare DL-based and classical approaches side by side.
- **`acquisition_mode`**: `DDA`, `DIA`, `both`, or *not applicable*.


## What's here

- **`denovo.db`**: SQLite database of papers, models, authors, affiliations, cities, countries, and venues. **The source of truth.**
- **`denovo.sql`**: committed SQL dump of `denovo.db` so diffs are reviewable in git.
- **`index.qmd` + `_quarto.yml`**: the Quarto site. `index.qmd` is the interactive overview, charts powered by Observable JS; `pages/` holds a generated detail page for every publication, author, algorithm, institution and venue, written by `build_pages.py` at build time and not committed.
- **Offline refresh scripts**, each rebuilding one slice of the database from an external API: `build_citations.py` (citation graph), `build_publication_impact.py` (OpenAlex citation counts), `build_journal_metrics.py` (venue metrics), `build_repo_metrics.py` (GitHub activity), `build_author_ids.py` (ORCID / OpenAlex ids), `build_abstracts.py` (abstracts), `build_versions.py` (preprint-to-published links). The first four also run on a cron; see `.github/workflows/`.
- **`build_pages.py` + `slugs.py`**: generate the per-entity detail pages and their URLs.
- **`check_counts.py`**: verifies that the row counts quoted in the documentation still match `denovo.db`.
- **`WATCHLIST.md`**: methods that belong in the catalog but have nothing citable yet, plus things deliberately left out and why.
- **`plots.ipynb`**: Jupyter notebook for offline exploration / sanity checks (static matplotlib figures, not published).

## Contributing

**Easiest:** [open an issue](https://github.com/BioGeek/awesome_de_novo_peptide_sequencing/issues/new) with a link to the paper (DOI, arXiv, bioRxiv, OpenReview, …) and I'll wire it into the database for you. Corrections: wrong author lists, missing affiliations, mis-classified `kind` / `is_deep_learning` / `acquisition_mode`, broken links, you name it, are equally welcome via issue.

### Advanced: edit the database directly

If you're comfortable with SQLite, the source of truth is `denovo.db` and you can edit it with any tool: `sqlite3` CLI, [DB Browser for SQLite](https://sqlitebrowser.org/), DataGrip. A new paper typically needs:

- One row in **`algorithm`** if the method is new (set `name`, `algorithm_family`, `short_description`, `kind`, `is_deep_learning`, `acquisition_mode`, and `subdomain` for a `downstream-application`). Code repositories are **not** a column here: add one row per repo to **`algorithm_repository`** (`algorithm_id`, `url`, `sort_order`).
- One row in **`publication`** (`title`, `publication_date`, `doi`, `publisher`, `url`, `journal`, `publication_type`).
- One row per author in **`author`** if they are new, then one row per author in **`publication_author`** with `author_order` set to the byline position.
- One row in **`publication_algorithm`** connecting the new publication to its model(s).
- Affiliations: insert into **`country` → `city` → `affiliation`** and link each author with **`author_affiliation`** (re-use existing rows where possible: author names and `(affiliation.name, department)` are the natural keys).

Then regenerate the human-readable dump so the diff is reviewable:

```bash
sqlite3 denovo.db .dump > denovo.sql
git add denovo.db denovo.sql
```

Open a PR with both files. The GitHub Action rebuilds the site and pushes to `gh-pages` on merge; typically live within ~10-15 minutes, most of it spent generating and rendering the per-entity detail pages.

**One-time setup per clone**: run `git config core.hooksPath .githooks` to activate the tracked pre-commit hook. Whenever you stage `denovo.db` it regenerates `denovo.sql`, so the two cannot drift apart in a commit, and it refreshes the row counts quoted in the documentation.

## Preview locally

```bash
uv sync
uv run quarto preview
```

## License

Dual-licensed so the data and the code each get the convention of their own community:

- **Curated catalog + prose** (`denovo.db`, `denovo.sql`, `index.qmd`, README and other docs) → [**CC BY 4.0**](LICENSE). Use it for anything, commercial or otherwise; the only ask is appropriate credit (see [Citation](#citation)).
- **Python scripts** (every `.py` file in the repository root: the `build_*.py` refreshers, `check_counts.py`, `slugs.py`) → [**MIT**](LICENSE-CODE). Standard permissive terms; copyright notice must be preserved when redistributing.

The Zenodo deposit is archived under CC BY 4.0 (the umbrella that covers the bulk of the artefact). The MIT terms on the helper scripts ride along for anyone who wants to lift the scripts into a permissively-licensed downstream tool.

## Citation

If you use this catalog, please cite it:

> Van Goey, J. *Awesome De Novo Peptide Sequencing.* Zenodo. <https://doi.org/10.5281/zenodo.20825737>

Machine-readable metadata lives in [`CITATION.cff`](CITATION.cff); GitHub renders a "Cite this repository" button in the sidebar that exposes BibTeX / APA / RIS / EndNote conversions.


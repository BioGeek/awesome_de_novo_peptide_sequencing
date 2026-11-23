# The metrics page

Source of the site page [How *de novo* sequencing is scored](https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/pages/analyses/metrics.html).
`scripts/build_pages.py` copies `page.qmd` to `pages/analyses/metrics.qmd`, inlines
`site_data.json` as the OJS value `ns`, and turns every `{{n:<id>}}` into a link to its entry
in `page_numbers.json`.

| File | What it is | Written by |
|---|---|---|
| `papers.jsonl` | one row per deep-learning method paper: every metric it reports and how it defines it, quoted | a person or agent who read the paper |
| `implementations.json` | one row per tool, or per version range where its code changed, pinned to the commit read | a person or agent who read the code |
| `site_data.json` | the page's tables and charts | `build_site_data.py` |
| `page_numbers.json` | every number on the page, with the papers or rows it counts | `page_numbers.py` |
| `run_all.sh` | rebuilds the two generated files | |
| `check_metrics.py` | holds the folder in step with `denovo.db` (pre-commit with `--fix`, CI bare) | |
| `pending.py` | lists the papers not read yet, with their full text | |
| `watch_code.py` | reports pinned code that changed upstream (weekly workflow `watch-metric-code`) | |

## What keeps it current

- **A new paper.** When a deep-learning method paper enters `denovo.db`, the pre-commit hook
  (`check_metrics.py --fix`) gives it a row marked `"skipped_reason": "not yet read for this
  page"` and rebuilds the data, so the page counts it as unread and lists it under that number.
  CI fails if a commit arrives without the row. The row is filled in by hand: see below.
- **A changed catalog.** A method renamed or a date corrected changes the page's data; the hook
  rebuilds it, and CI fails if a commit carries stale data.
- **Changed upstream code.** Every Monday `watch-metric-code` compares each tool's current row
  (the last one listed for it) with the default branch, or with the branch named in the row's
  optional `watch` field, and keeps one issue open listing what changed.

## Adding a paper

Run `python3 analysis/metrics/pending.py`. For each paper it prints the full text it found
(the denovo_wiki extraction, else the library PDF). Read the paper's evaluation and methods
sections, then replace its row in `papers.jsonl` (one JSON object per line, sorted by
`pub_id`):

```json
{"pub_id": 1005, "methods": ["EncNovo"], "title": "...",
 "metrics": [{"name_as_printed": "Peptide recall", "normalised": "peptide_recall",
              "definition_quote": "the sentence that defines it, verbatim", "section": "3.2 Metrics",
              "aa_tol_da": 0.1, "prefix_tol_da": 0.5, "matching": "prefix_suffix_mass",
              "il_equal": null, "ptm_handling": null, "notes": "anything the quote does not say"}],
 "new_metrics": [], "follows_code_of": null, "skipped_reason": null, "benchmarks": ["..."]}
```

- `normalised` is one of `peptide_recall`, `peptide_precision`, `aa_precision`, `aa_recall`,
  `precision_coverage_curve`, `AUC_of_precision_coverage`, `FDR-based`, `PTM_precision/recall`,
  `calibration metric`, `mass-based metric`, `sequence similarity`, or `other: <what>`.
  Classify by the DENOMINATOR the paper states, not by its name: a "peptide precision" over
  all spectra is `peptide_recall`, and the name goes in `name_as_printed`.
- `matching` is `prefix_suffix_mass`, `exact_string`, `positional` or `unstated`.
- Never invent a tolerance: `null` and `"unstated"` when the paper does not say.
- `new_metrics` holds `{"name", "formula", "quote"}` for a metric the paper introduces.
- A paper that calls a recall "precision", or a precision "recall", and STATES the
  denominator, also goes into `PRECISION_NAMED_RECALL` or `RECALL_NAMED_PRECISION` in
  `build_site_data.py`.
- Leave `skipped_reason` set, with the reason, when the full text cannot be had.

Then commit; the hook rebuilds the page's data.

## When the watcher opens an issue

Read the diff it links. If the behaviour changed (the tolerance, the argument order, which
spectra count, the denominator, the curve), add a row for the new version range after the
current one, with the new commit as `ref`. If it did not, move the current row's `ref` to the
commit the issue names. Then `run_all.sh`. The next weekly run closes the issue when every
tracked file matches.

# The nine-species benchmark, version by version

The full analysis is a page on the site,
[The nine-species benchmark, version by version](https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/pages/analyses/nine-species-versions.html).
Its source is [`analysis/nine_species/page.qmd`](analysis/nine_species/page.qmd)
and the numbers its charts draw are in
[`analysis/nine_species/site_data.json`](analysis/nine_species/site_data.json);
`scripts/build_pages.py` copies both into `pages/analyses/` on every publish.
The code that produced the numbers, and how to rerun it, is in
[`analysis/nine_species/`](analysis/nine_species/). Edit the page source, not
this file: the text lives in one place.

What to know before attaching a number to "nine-species", in short:

- **MSV000090982 is not the published benchmark.** It holds three builds (2022,
  2023, 2024); the published one is a fourth, Zenodo main, and even the 2024
  build is 47,640 spectra away from it. InstaNovo's `ninespecies_updated` copy
  is the 2023 build. denovo_benchmarks runs on a selection of its own
  (MSV000096182): 101 raw files relabelled by its own search, half its spectra
  not in Zenodo main.
- **The InstaNovo split, which NovoBench trains on, overlaps the newer test
  sets**: 17% of ProteoBench's spectra, 22% of dIon's test set and 22% of
  denovo_benchmarks' nine-species sets are scans from its training split.
- **17% of the revised spectra have a precursor on an isotope peak** (none in
  the original or the InstaNovo split), and denovo_benchmarks labels 39,011 of
  those as an extra deamidation instead, most of them wrongly.
- **Zenodo main holds no spectrum without a precursor charge**, a filter the
  paper does not describe; it explains the paper's "13 MGF files with fewer
  than 100 accepted PSMs".
- **The original writes every leucine as I**, and its folds are disjoint on the
  modified peptide but not on the backbone (29 to 36% shared).

The catalog records each of these versions as a `dataset_version` of the
nine-species benchmark, with `revised (build not stated)` for a paper that
names the revised benchmark or MSV000090982 without saying which build.

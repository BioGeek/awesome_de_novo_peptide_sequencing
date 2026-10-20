# Nine-species versions, spectrum by spectrum

The code behind the site page *The nine-species benchmark, version by version*,
whose source is [`page.qmd`](page.qmd) in this folder. It puts every public
version of the nine-species benchmark on one spectrum key, `(species, raw file,
scan)`, and compares them.

It is **not** part of the catalog's pipeline: nothing here reads or writes
`denovo.db`, nothing runs in CI, and `scripts/check_counts.py` does not look in
this folder. It needs about 110 GB of downloads and 30 GB of RAM, so it was run on
a workstation, under `~/data/ninespecies/` (every path below is relative to that):
`raw/` for downloads, `tables/` for one parquet per source file, `results/` for
the outputs. Python 3.12 with polars, pyarrow, numpy, requests and pyteomics.

## Run order

| step | script | what it does |
|---|---|---|
| list | `massive_ls.py`, `zip_ls.py`, `ftp_head.py` | list MSV000081382 and MSV000090982 over FTPS (TLS is required), list a remote zip's members over HTTP range requests, peek at a file's first bytes |
| jobs | `make_jobs.py`, `make_jobs_zip.py` | one extraction job per MGF: the original's species files, 10k subsets and fold files, the three MassIVE releases, Zenodo main and balanced |
| download | `pdl.py`, `start_zenodo.sh` | segmented parallel download; Zenodo gives about 0.7 MB/s to one connection and 40 MB/s to sixteen |
| extract | `run_jobs.py` → `extract.py` | one parquet per MGF: label (raw and normalised), precursor, charge, RT, peak count, TIC, top-10 peak m/z |
| extract | `run_tabular.sh` → `extract_tabular.py` | the same schema for the non-MGF copies: InstaNovo's parquet split and IPC files, dIon's parquet, Percolator's PSM tables |
| extract | `datazip.py` | spectrum headers of Zenodo's 59 GB `nine-species-data.zip`, streamed one request per member, never written to disk |
| key | `percolator_keyed.py` | Percolator's `PSMId` to (raw file, scan), via the file-index lines in `percolator.log.txt` |
| compare | `a_*.py` | one script per question; `ns.py` (loading, label normalisation, masses) and `compare.py` (spectrum-level diff) are shared |
| denovo_benchmarks | `make_jobs_dnb.py` → `run_jobs.py`, then `a_dnb.py` | its nine-species MGF chunks in MSV000096182, keyed through its own `9_species_*_labels.csv` (`<raw>_<chunk>:<index>`) |
| site | `export_site_data.py` | the small `site_data.json` the site page reads; `page.qmd` is that page's source, copied to `pages/analyses/` by `scripts/build_pages.py` |

The order within `a_*.py` matters in one place: `a_flow.py build` writes
`results/registry.parquet`, one row per spectrum seen in any version, and
`a_leak.py`, `a_fate.py`, `a_converted.py` and `a_dnb.py` read it. `a_dnb.py`
writes `dnb_keyed.parquet`, which a second run of `a_leak.py` picks up.

## Three things worth knowing before changing anything

- **Labels go through one normaliser** (`extract.norm_seq`, re-applied at load
  time by `ns.renorm`), because the versions spell the same peptide six ways:
  `C(+57.02)`, `C+57.021`, `+42.011ABC`, `[+42.0106]-ABC`, denovo_benchmarks'
  `n[42.0106]ABC` and Tide's
  `A[42.0106]BC`, which puts an N-terminal modification on the first residue,
  after that residue's own modification if it has one. Static
  carbamidomethylation is written out everywhere, because Tide and DeepNovo omit
  it. A difference the normaliser does not absorb shows up as a fake label
  change, so check one before believing a count.
- **The original deposit spells two species "bacteria"**, so species come from
  the PXD in each TITLE (`ns.original`), never from the PEAKS project name.
- **Join versions that lack a raw file through one that has it.** Zenodo main
  carries only species and scan; it is keyed through MassIVE 2024 on (species,
  scan, precursor string, RT), and through Percolator's PSM for the 20,123
  spectra the 2024 release does not hold. Balanced and dIon are keyed through
  main.

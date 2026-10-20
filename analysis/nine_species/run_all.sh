#!/bin/sh
# Rebuild the whole nine-species analysis from public sources, in order.
#
#   cp -r analysis/nine_species ~/data/ninespecies/scripts
#   sh ~/data/ninespecies/scripts/run_all.sh
#
# Needs about 110 GB of disk, 30 GB of RAM, Python 3.12 and a day: MassIVE's FTPS is the slow part.
# Every step skips work it finds done, so a rerun resumes. The last step, page_numbers.py, prints every
# number on the site page and writes page_numbers.json; `page_numbers.py --check` compares against the
# committed copy.
set -eu
B="$HOME/data/ninespecies"; S="$B/scripts"; P="$B/.venv/bin/python"
mkdir -p "$B/raw" "$B/tables" "$B/results"
[ -x "$P" ] || { python3 -m venv "$B/.venv" && "$B/.venv/bin/pip" install -q -r "$S/requirements.txt"; }
cd "$S"

# 1. what is in the three MassIVE deposits
for acc in MSV000081382 MSV000090982 MSV000096182; do
  [ -s "$B/ls_$acc.tsv" ] || "$P" massive_ls.py "$acc" > "$B/ls_$acc.tsv"
done

# 2. the HTTP sources (Zenodo, ProteoBench, Hugging Face) and denovo_benchmarks' labels
"$P" download_sources.py

# 3. one parquet per source file
"$P" make_jobs.py      && "$P" run_jobs.py "$B/jobs_ftp.jsonl" 8   # v1, v1 folds, the three MassIVE builds
"$P" make_jobs_zip.py  && "$P" run_jobs.py "$B/jobs_zip.jsonl" 4   # v2 main, v2 balanced, ProteoBench
"$P" make_jobs_dnb.py  && "$P" run_jobs.py "$B/jobs_dnb.jsonl" 8   # denovo_benchmarks' MGF chunks
for what in instanovo_split instanovo_ipc dion percolator; do "$P" extract_tabular.py "$what"; done
"$P" datazip.py                                                    # the converted MGFs' headers, streamed
"$P" percolator_keyed.py

# 4. the comparisons, in dependency order
"$P" a_original.py
"$P" a_zenodo.py            # needs percolator_keyed.parquet
"$P" a_instanovo.py         # writes instanovo_split_keyed.parquet
"$P" a_instanovo_split.py
"$P" a_dion.py
"$P" a_dion_rows.py
"$P" a_rawnames.py
"$P" a_versions.py releases orig_vs_r2022 r2024_vs_zenodo instanovo_ipc
"$P" a_flow.py build flows  # writes registry.parquet, read by everything below
"$P" a_swing.py
"$P" a_fate.py              # needs datazip.py's tables
"$P" a_converted.py
"$P" a_profile.py
"$P" a_isotope.py
"$P" a_peaks.py             # fetches mouse's v1 file and MassIVE 2024 MGFs itself
"$P" a_dnb.py               # writes dnb_keyed.parquet
"$P" a_leak.py              # reads dnb_keyed.parquet

# 5. what the page reads
"$P" export_site_data.py
"$P" page_numbers.py

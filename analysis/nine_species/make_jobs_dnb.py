"""Jobs for denovo_benchmarks' nine-species MGFs in MSV000096182: one per chunk.

Its MGFs are named <raw file>_<chunk>.mgf, at most 20,000 charged spectra each, and its labels key a
spectrum as <raw file>_<chunk>:<index in chunk>, so the chunk goes in `fold` and the index is `row`.
"""
import glob, json, os, re
import polars as pl
B = os.path.expanduser("~/data/ninespecies"); T = f"{B}/tables"
# Raw files are taken from denovo_benchmarks' OWN label files, named per species, not from the revised
# conversion: it also used honeybee BM-2, which the revised search never had.
LABEL_SPECIES = {"apis_mellifera": "Apis-mellifera", "bacillus_subtilis": "Bacillus-subtilis",
                 "candidatus_thiodiazotropha": "Candidatus-endoloripes", "human": "H.-sapiens",
                 "methanosarcina_mazei": "Methanosarcina-mazei", "mus_musculus": "Mus-musculus",
                 "saccharomyces_cerevisiae": "Saccharomyces-cerevisiae",
                 "solanum_lycopersicum": "Solanum-lycopersicum", "vigna_mungo": "Vigna-mungo"}
species_of = {}
for f in glob.glob(f"{B}/raw/dnb_labels/9_species_*_labels.csv"):
    sp = LABEL_SPECIES[re.match(r".*/9_species_(.+)_labels\.csv$", f).group(1)]
    for raw in pl.read_csv(f)["spectrum_id"].str.extract(r"^(.*)_\d+:\d+$", 1).unique():
        species_of[raw] = sp
jobs = []
for line in open(f"{B}/ls_MSV000096182.tsv"):
    size, path = line.rstrip("\n").split("\t")[:2]
    m = re.match(r"(.+)_(\d+)\.mgf$", path.split("/")[-1])
    if "/peak/mgf/" not in path or "ccms_metadata" in path or not m or m.group(1) not in species_of: continue
    raw, chunk = m.groups()
    jobs.append(dict(kind="ftp_mgf", path=path, source="dnb", species=species_of[raw], raw_file=raw, fold=chunk,
                     size=int(size), out=f"{T}/dnb__{species_of[raw]}__{raw}_{chunk}.parquet"))
jobs.sort(key=lambda j: -j["size"])
with open(f"{B}/jobs_dnb.jsonl", "w") as f:
    for j in jobs: f.write(json.dumps(j) + "\n")
print(len(jobs), "jobs", f"{sum(j['size'] for j in jobs) / 1e9:.1f} GB")

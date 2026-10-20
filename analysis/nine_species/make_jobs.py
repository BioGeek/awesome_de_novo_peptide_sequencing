"""Write jobs.jsonl: one extraction job per source file."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from extract import canon_species
B = os.path.expanduser("~/data/ninespecies"); T = f"{B}/tables"; os.makedirs(T, exist_ok=True)
jobs = []
def ls(acc):
    for line in open(f"{B}/ls_{acc}.tsv"):
        size, path = line.rstrip("\n").split("\t")[:2]
        if size != "LINK": yield int(size), path
# 1. original 2017 deposit, high-resolution part
for size, p in ls("MSV000081382"):
    if "/HighResolution/data/" not in p: continue
    folder, name = p.split("/")[-2], p.split("/")[-1]
    if folder.startswith("high."):
        sp = canon_species(folder.split(".")[1])
        src = "orig_species10k" if name == "peaks.db.10k.mgf" else "orig_species"
        jobs.append(dict(kind="ftp_mgf", path=p, source=src, original=True, fold=sp, size=size,
                         out=f"{T}/{src}__{sp}.parquet"))
    elif folder.startswith("cross."):
        held = canon_species(folder.split("exclude_")[1])
        split = {"cross.cat.mgf.train.repeat": "train", "cross.cat.mgf.valid.repeat": "valid",
                 "cross.cat.mgf.test.repeat": "test", "cross.cat.mgf": "pool"}[name]
        jobs.append(dict(kind="ftp_mgf", path=p, source="orig_fold", original=True, fold=held, split=split,
                         size=size, out=f"{T}/orig_fold__{held}__{split}.parquet"))
# 2. the three MassIVE releases of the revised benchmark
for size, p in ls("MSV000090982"):
    if not p.endswith(".mgf"): continue
    rel = "r2024" if "2024-05-14" in p else "r2023" if "2023-03-08" in p else "r2022"
    sp, raw = p.split("/")[-2], p.split("/")[-1][:-4]
    jobs.append(dict(kind="ftp_mgf", path=p, source=rel, species=sp, raw_file=raw, size=size,
                     out=f"{T}/{rel}__{sp}__{raw}.parquet"))
jobs.sort(key=lambda j: -j["size"])
with open(f"{B}/jobs_ftp.jsonl", "w") as f:
    for j in jobs: f.write(json.dumps(j) + "\n")
from collections import Counter
print(Counter(j["source"] for j in jobs), f"{sum(j['size'] for j in jobs)/1e9:.1f} GB")

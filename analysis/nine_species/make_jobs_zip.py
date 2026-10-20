import json, os, zipfile
B = os.path.expanduser("~/data/ninespecies"); T = f"{B}/tables"
jobs = []
for z in ["main", "balanced"]:
    path = f"{B}/raw/nine-species-{z}.zip"
    for m in zipfile.ZipFile(path).namelist():
        if m.endswith(".mgf"):
            sp = m.split("/")[-1][:-4]
            jobs.append(dict(kind="zip_mgf", zip=path, member=m, source=f"zenodo_{z}", species=sp,
                             out=f"{T}/zenodo_{z}__{sp}.parquet"))
# ProteoBench's MGF is gzipped and stands alone; its job used to be written by hand.
jobs.append(dict(kind="gz_mgf", path=f"{B}/raw/nine_species_balanced_De_Novo.mgf.gz", source="proteobench",
                 split="test", out=f"{T}/proteobench__all.parquet"))
with open(f"{B}/jobs_zip.jsonl", "w") as f:
    for j in jobs: f.write(json.dumps(j) + "\n")
print(len(jobs), "zip jobs")

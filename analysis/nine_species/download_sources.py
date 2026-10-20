"""Fetch every HTTP source in sources.HTTP_DOWNLOADS into ~/data/ninespecies/raw/, plus denovo_benchmarks'
nine label CSVs over FTPS. Finished files are skipped, so a rerun resumes.

Files over 1 GB go through pdl.py's segmented download: Zenodo gives about 0.7 MB/s to one
connection. nine-species-data.zip (59 GB) is NOT downloaded: datazip.py streams it.
"""
import os, subprocess, sys
import requests
from sources import HTTP_DOWNLOADS
from extract import ftp_download

B = os.path.expanduser("~/data/ninespecies"); R = f"{B}/raw"
os.makedirs(R, exist_ok=True)
HERE = os.path.dirname(os.path.abspath(__file__))

for url, name in HTTP_DOWNLOADS:
    out = f"{R}/{name}"
    if os.path.exists(out):
        print("have", name); continue
    size = int(requests.head(url, allow_redirects=True, timeout=60).headers.get("Content-Length", 0))
    if size > 1 << 30:
        subprocess.run([sys.executable, f"{HERE}/pdl.py", url, out, "16"], check=True)
    else:
        with requests.get(url, stream=True, timeout=600) as r, open(out + ".part", "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20): f.write(chunk)
        os.replace(out + ".part", out)
    print("ok", name, flush=True)

# denovo_benchmarks' labels, named in the MSV000096182 listing that massive_ls.py wrote
os.makedirs(f"{R}/dnb_labels", exist_ok=True)
for line in open(f"{B}/ls_MSV000096182.tsv"):
    size, path = line.rstrip("\n").split("\t")[:2]
    name = path.split("/")[-1]
    if "/other/9_species_" in path and name.endswith("_labels.csv") and not os.path.exists(f"{R}/dnb_labels/{name}"):
        ftp_download(path, f"{R}/dnb_labels/{name}.part"); os.replace(f"{R}/dnb_labels/{name}.part", f"{R}/dnb_labels/{name}")
        print("ok", name, flush=True)

"""Full peak lists of the same spectra, original (PEAKS export) against revised (ThermoRawFileParser).

Mouse only: its original species file (60 MB) and its 2024 MGFs (58 MB) are small enough to fetch whole,
and the processing difference under test is the same for every species.
"""
import os, re, sys, glob, bisect
import numpy as np, polars as pl
from extract import ftp_download, mgf_records, text_lines, scan_of, ORIG_TITLE, norm_seq

D = os.path.expanduser("~/data/ninespecies/peaksample"); os.makedirs(D, exist_ok=True)
ORIG = "/v01/MSV000081382/peak/DeepNovo/HighResolution/data/high.mouse.PXD004948/peaks.db.mgf"
LS = os.path.expanduser("~/data/ninespecies/ls_MSV000090982.tsv")
PROTON = 1.007276

def fetch():
    out = f"{D}/orig_mouse.mgf"
    if not os.path.exists(out + ".done"): ftp_download(ORIG, out); open(out + ".done", "w").close()
    for line in open(LS):
        size, path = line.rstrip("\n").split("\t")
        if "2024-05-14" in path and "/Mus-musculus/" in path and path.endswith(".mgf"):
            dest = f"{D}/r2024__{os.path.basename(path)}"
            if not os.path.exists(dest + ".done"): ftp_download(path, dest); open(dest + ".done", "w").close()

def load(path, original):
    recs = {}
    with open(path, "rb") as f:
        for rec in mgf_records(text_lines(f)):
            if original:
                m = ORIG_TITLE.search(rec.get("TITLE", "")); raw, scan = m.group(2), int(m.group(3))
            else:
                raw, scan = re.sub(r"^r2024__|\.mgf$", "", os.path.basename(path)), scan_of(rec)
            pk = np.array(rec["_peaks"], dtype=float).reshape(-1, 2) if rec.get("_peaks") else np.zeros((0, 2))
            recs[(raw, scan)] = (float(rec["PEPMASS"].split()[0]), int(str(rec.get("CHARGE", "0")).rstrip("+") or 0), pk, rec.get("SEQ"))
    return recs

def match(a, b, tol_ppm=10):
    """Fraction of a's peaks with a peak in b within tol_ppm, and of a's intensity."""
    if len(a) == 0 or len(b) == 0: return 0.0, 0.0
    bm = np.sort(b[:, 0]); idx = np.searchsorted(bm, a[:, 0])
    lo = np.abs(a[:, 0] - bm[np.clip(idx - 1, 0, len(bm) - 1)]); hi = np.abs(a[:, 0] - bm[np.clip(idx, 0, len(bm) - 1)])
    hit = np.minimum(lo, hi) / a[:, 0] * 1e6 <= tol_ppm
    return hit.mean(), a[hit, 1].sum() / a[:, 1].sum()

if __name__ == "__main__":
    fetch()
    o = load(f"{D}/orig_mouse.mgf", True)
    r = {}
    for p in glob.glob(f"{D}/r2024__*.mgf"): r.update(load(p, False))
    keys = [k for k in o if k in r]
    print(f"original mouse spectra {len(o)}, revised {len(r)}, both {len(keys)}")
    rows = []
    for k in keys:
        (mo, zo, po, so), (mr, zr, pr, sr) = o[k], r[k]
        neutral = (mo - PROTON) * zo if zo else None
        f_or, i_or = match(po, pr); f_ro, i_ro = match(pr, po)
        # peaks above the precursor's neutral mass, impossible for a fragment of that precursor
        above = int((po[:, 0] > neutral + 2).sum()) if neutral else None
        # PEAKS deconvolution: an original peak with no revised partner that IS a revised peak moved to charge 1
        un = po[[match(po[i:i + 1], pr)[0] == 0 for i in range(len(po))]] if len(po) < 2000 else po[:0]
        dec = 0
        if len(un) and len(pr):
            for zf in (2, 3):
                moved = np.column_stack([pr[:, 0] * zf - (zf - 1) * PROTON, pr[:, 1]])
                dec += int(sum(match(un[i:i + 1], moved)[0] > 0 for i in range(len(un))))
        rows.append({"raw_file": k[0], "scan": k[1], "charge_o": zo, "charge_r": zr, "dmz_ppm": (mr - mo) / mo * 1e6,
                     "dneutral_da": ((mr - PROTON) * zr - (mo - PROTON) * zo) if zo and zr else None,
                     "peaks_o": len(po), "peaks_r": len(pr), "orig_peaks_in_revised": f_or, "orig_intensity_in_revised": i_or,
                     "revised_peaks_in_orig": f_ro, "revised_intensity_in_orig": i_ro,
                     "orig_peaks_above_precursor": above, "orig_unmatched": len(un), "orig_unmatched_explained_by_deconvolution": dec,
                     "orig_max_mz": float(po[:, 0].max()) if len(po) else None, "rev_max_mz": float(pr[:, 0].max()) if len(pr) else None,
                     "intensity_ratio_median": float(np.median(po[:, 1]) / np.median(pr[:, 1])) if len(po) and len(pr) else None})
    df = pl.DataFrame(rows); df.write_parquet(f"{D}/peak_compare_mouse.parquet")
    with pl.Config(tbl_cols=-1, tbl_width_chars=300):
        print(df.describe())
        print("dneutral (Da) rounded:", df.select(pl.col("dneutral_da").round(1)).to_series().value_counts(sort=True).head(8))

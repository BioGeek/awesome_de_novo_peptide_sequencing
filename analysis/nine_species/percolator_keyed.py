"""Percolator target PSMs keyed by (species, raw_file, scan), with the file index resolved from the log."""
import zipfile, re, polars as pl
from ns import T, OUT
from extract import norm_seq
z = zipfile.ZipFile(f"{T}/../raw/nine-species-intermediate.zip")
frames = []
for m in z.namelist():
    if not m.endswith("percolator.log.txt"): continue
    sp = m.split("/")[0]
    idx = {}
    for l in z.read(m).decode(errors="replace").splitlines():
        mm = re.match(r"INFO: Assigning index (\d+) to .*/([^/]+)\.mgf\.$", l)
        if mm: idx[int(mm.group(1))] = mm.group(2)
    d = pl.read_parquet(f"{T}/percolator__{sp}.parquet")
    parts = d["PSMId"].str.split("_")
    d = d.with_columns(parts.list.get(1).cast(pl.Int64).alias("file_index"), parts.list.get(2).cast(pl.Int64).alias("scan"), parts.list.get(3).cast(pl.Int8).alias("charge"),
                       parts.list.get(0).alias("psm_prefix"))
    d = d.with_columns(pl.col("file_index").replace_strict(idx, default=None).alias("raw_file"))
    frames.append(d)
    print(sp, d.height, "PSMs;", len(idx), "files;", d["raw_file"].null_count(), "unresolved; prefixes", d["psm_prefix"].unique().to_list()[:3])
p = pl.concat(frames)
norm = p["peptide"].map_elements(lambda s: norm_seq(s)[0], return_dtype=pl.String)
p = p.with_columns(norm.alias("seq_norm"), pl.col("q-value").alias("q"))
# Percolator can hold several PSMs per scan (one per charge state): rank them by score
p = p.with_columns(pl.col("score").rank("ordinal", descending=True).over(["species", "raw_file", "scan"]).alias("rank_in_scan"))
p.write_parquet(f"{OUT}/percolator_keyed.parquet", compression="zstd")
print(p.height, "PSMs;", p.filter(pl.col("q") < 0.01).height, "at q<0.01;", p.filter(pl.col("q") <= 0.01).height, "at q<=0.01;",
      "scans with >1 PSM:", p.filter(pl.col("rank_in_scan") > 1).height)

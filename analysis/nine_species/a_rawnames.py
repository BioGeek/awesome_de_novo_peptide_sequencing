import polars as pl
from ns import original, OUT
o = original("orig_species", ["species", "raw_file", "scan", "title"]).collect()
p = pl.read_parquet(f"{OUT}/percolator_keyed.parquet").select("species", "raw_file").unique()
on = o.group_by(["species", "raw_file"]).len()
j = on.join(p.with_columns(pl.lit(True).alias("in_revised")), on=["species", "raw_file"], how="left")
print(j.group_by("species").agg(pl.len().alias("orig_raw_files"), pl.col("in_revised").sum().alias("name_in_revised"),
      pl.col("len").filter(pl.col("in_revised").is_null()).sum().alias("orig_spectra_in_unmatched_files")).sort("species"))
print(j.filter(pl.col("in_revised").is_null()).head(12))
print(p.group_by("species").len().sort("species"))

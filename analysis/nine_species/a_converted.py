"""The converted MGFs (Zenodo nine-species-data.zip) against everything downstream of them.

  * how many MS2 spectra each species has, and how many carry no precursor charge
  * whether main ever holds a spectrum whose MGF had no charge
  * what the charge-less spectra that Percolator nonetheless accepted (fate 4c) look like
  * whether the original's 'no target PSM' spectra were in the conversion at all
  * which converted MGFs never reached main
"""
import polars as pl
from ns import OUT, KEY, T

conv = pl.scan_parquet(f"{T}/datazip__*.parquet").select("species", "raw_file", "scan", pl.col("charge").alias("mgf_charge"),
                                                         pl.col("n_peaks").alias("mgf_peaks")).collect()
print("converted spectra:", conv.height, " duplicate keys:", conv.height - conv.select(KEY).unique().height)
conv = conv.unique(subset=KEY, keep="first").with_columns(pl.col("mgf_charge").is_not_null().alias("has_charge"))
reg = pl.read_parquet(f"{OUT}/registry.parquet").select(KEY + ["lab_orig", "lab_zenodo_main", "lab_r2024"])
perc = pl.read_parquet(f"{OUT}/percolator_keyed.parquet").sort("score", descending=True).unique(subset=KEY, keep="first").select(KEY + ["q"])

c = conv.join(perc, on=KEY, how="left").join(reg, on=KEY, how="left")
summ = c.group_by("species").agg(
    pl.len().alias("converted MS2"), (~pl.col("has_charge")).sum().alias("no charge"),
    pl.col("q").is_not_null().sum().alias("target PSM"),
    (pl.col("q") < 0.01).sum().alias("q<0.01"),
    ((pl.col("q") < 0.01) & ~pl.col("has_charge")).sum().alias("q<0.01, no charge"),
    pl.col("lab_zenodo_main").is_not_null().sum().alias("in main"),
    (pl.col("lab_zenodo_main").is_not_null() & ~pl.col("has_charge")).sum().alias("in main, no charge")).sort("species")
summ = pl.concat([summ, summ.select(pl.lit("ALL").alias("species"), pl.exclude("species").sum())])
summ.write_csv(f"{OUT}/converted__summary.csv")
with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=300): print(summ)

# main spectra not in the conversion at all?
m = reg.filter(pl.col("lab_zenodo_main").is_not_null()).join(conv.select(KEY + ["has_charge"]), on=KEY, how="left")
print("main spectra absent from the conversion:", m["has_charge"].is_null().sum())

# original fates against the conversion
fate = pl.read_parquet(f"{OUT}/fate_orig.parquet").select(KEY + ["fate"]).join(conv.select(KEY + ["has_charge", "mgf_charge"]), on=KEY, how="left")
ft = fate.group_by("fate").agg(pl.len().alias("spectra"), pl.col("has_charge").is_null().sum().alias("scan not in conversion"),
                              (pl.col("has_charge") == False).sum().alias("converted without charge"),
                              (pl.col("has_charge") == True).sum().alias("converted with charge")).sort("fate")
ft.write_csv(f"{OUT}/converted__orig_fate.csv")
with pl.Config(tbl_rows=-1, tbl_width_chars=300, fmt_str_lengths=70): print(ft)
print(fate.filter(pl.col("fate").str.starts_with("2")).group_by("species").agg(pl.len(), pl.col("has_charge").is_null().sum().alias("not converted"),
      (pl.col("has_charge") == False).sum().alias("no charge")).sort("species"))

# converted MGFs that never reached main, with their PSM yield
files = c.group_by("species", "raw_file").agg(pl.len().alias("spectra"), (pl.col("q") < 0.01).sum().alias("q<0.01"),
                                              pl.col("lab_zenodo_main").is_not_null().sum().alias("in main"))
gone = files.filter(pl.col("in main") == 0).sort("species", "raw_file")
gone.write_csv(f"{OUT}/converted__files_not_in_main.csv")
with pl.Config(tbl_rows=-1): print("MGFs with nothing in main:", gone.height); print(gone)

"""The revised benchmark's own pipeline: Percolator -> main -> balanced -> ProteoBench."""
import polars as pl
from ns import renorm, release, il, OUT, mass_error_expr, SHORT
C = ["species", "scan", "row", "pepmass_str", "pepmass", "charge", "rt", "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz"]
zm = release("zenodo_main", C).collect().pipe(renorm); zb = release("zenodo_balanced", C).collect().pipe(renorm)
pb = release("proteobench", C).collect().pipe(renorm)
perc = pl.read_parquet(f"{OUT}/percolator_keyed.parquet")
def save(df, name): df.write_csv(f"{OUT}/{name}.csv"); print(f"--- {name}"); print(df)
def counts(df, name):
    return df.group_by("species").agg(pl.len().alias(f"{name}_psms"), il(pl.col("seq_bare")).n_unique().alias(f"{name}_peptides"))
save(counts(zm, "main").join(counts(zb, "balanced"), on="species", how="left").sort("species"), "zenodo__counts")
print("main total", zm.height, "balanced total", zb.height, "proteobench total", pb.height)

# 1. peptides still shared between species, the thing 'main' exists to prevent
for name, d in [("main", zm), ("balanced", zb)]:
    sh = d.with_columns(il(pl.col("seq_bare")).alias("p")).group_by("p").agg(pl.col("species").n_unique().alias("n_species"))
    print(f"{name}: peptides (I/L folded, mods stripped) in more than one species:", sh.filter(pl.col("n_species") > 1).height)
    sh2 = d.with_columns(il(pl.col("seq_norm")).alias("p")).group_by("p").agg(pl.col("species").n_unique().alias("n"))
    print(f"{name}: modified peptides in more than one species:", sh2.filter(pl.col("n") > 1).height)

# 2. give main a raw file, through the search results: (species, scan, label) is the search's own key
acc = perc.filter(pl.col("q") < 0.01).select("species", "raw_file", "scan", "seq_norm", pl.col("charge").alias("psm_charge"), "q", "rank_in_scan")
acc_u = acc.unique(subset=["species", "scan", "seq_norm"], keep="none")
zmk = zm.join(acc_u, on=["species", "scan", "seq_norm"], how="left")
print("main spectra resolved to one raw file through Percolator:", zmk["raw_file"].is_not_null().sum(), "of", zm.height)
amb = acc.group_by(["species", "scan", "seq_norm"]).len().filter(pl.col("len") > 1)
print("(species, scan, label) keys shared by >1 accepted PSM (unresolvable this way):", amb.height)
print("main labels whose PSM charge differs from the MGF charge:", zmk.filter(pl.col("psm_charge").is_not_null() & (pl.col("psm_charge") != pl.col("charge"))).height)
zmk.write_parquet(f"{OUT}/zenodo_main_keyed.parquet")

# 3. balanced is whole raw files of main?
zbk = zb.join(zmk.select("species", "scan", "pepmass_str", "rt", "raw_file", "seq_norm").rename({"seq_norm": "main_seq"}),
              on=["species", "scan", "pepmass_str", "rt"], how="left")
print("balanced spectra found in main:", zbk["main_seq"].is_not_null().sum(), "of", zb.height,
      "; same label:", (zbk["main_seq"] == zbk["seq_norm"]).sum())
files_b = zbk.select("species", "raw_file").unique().drop_nulls()
per_file = zmk.join(files_b.with_columns(pl.lit(True).alias("in_bal")), on=["species", "raw_file"], how="left")
fb = per_file.group_by(["species", "raw_file"]).agg(pl.col("in_bal").first().fill_null(False).alias("selected"), pl.len().alias("main_psms"))
sel = fb.group_by(["species", "selected"]).agg(pl.len().alias("files"), pl.col("main_psms").sum().alias("psms")).sort(["species", "selected"])
save(sel, "zenodo__balanced_file_selection")
bal_counts = zbk.group_by(["species", "raw_file"]).len().rename({"len": "bal_psms"})
cmp = fb.filter(pl.col("selected")).join(bal_counts, on=["species", "raw_file"], how="left")
print("selected files whose PSMs are all in balanced:", cmp.filter(pl.col("bal_psms") == pl.col("main_psms")).height, "of", cmp.height)

# 4. ProteoBench = balanced?
pbk = pb.join(zb.select("pepmass_str", "rt", "species", "seq_norm").rename({"seq_norm": "bal_seq"}), on=["pepmass_str", "rt"], how="left")
print("ProteoBench spectra matched to balanced:", pbk["bal_seq"].is_not_null().sum(), "of", pb.height,
      "; same label:", (pbk["bal_seq"] == pbk["seq_norm"]).sum(), "; ambiguous joins:", pbk.height - pb.height)
print("ProteoBench label notation example:", pb["seq_raw"].head(3).to_list(), "vs balanced", zb["seq_raw"].head(3).to_list())

# 5. does the label's mass fit the precursor?
for name, d in [("main", zm), ("balanced", zb), ("proteobench", pb)]:
    e = d.with_columns(mass_error_expr())
    bad = e.filter(pl.col("mass_err_ppm") > 30)
    print(f"{name}: label mass vs precursor, median {e['mass_err_ppm'].median():.2f} ppm; >30 ppm: {bad.height}")
    if bad.height: print(bad.select("species", "scan", "pepmass", "charge", "seq_raw", "mass_err_ppm").head(5))

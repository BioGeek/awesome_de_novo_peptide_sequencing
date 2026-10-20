"""Why is a spectrum of the original deposit absent from Zenodo main? Walk Wen & Noble's pipeline.

  1. its raw file was not in the revised search
  2. the revised search reported no target PSM for that scan (no peptide within tolerance, or a decoy won)
  3. the best target PSM failed the 1% FDR (q >= 0.01), split by whether it is the original's peptide
  4. it passed, with the original's peptide or another, and was dropped afterwards:
       a. its converted MGF gives no precursor charge (main holds no such spectrum, although Tide
          searched them and Percolator accepted 75,693; Wen & Noble do not mention this filter)
       b. its MGF yielded too few PSMs to keep (4 mouse files; 13 B. subtilis files are wholly
          charge-less and fall under a)
       c. its peptide was found in more than one species and the random draw gave it to another
       d. anything else (reported, not guessed)
"""
import polars as pl
from ns import il, OUT, KEY, T

reg = pl.read_parquet(f"{OUT}/registry.parquet")
bare = lambda e: il(e.str.replace_all(r"\[[^\]]*\]-?", "").str.replace_all(r"[^A-Z]", ""))
o = reg.filter(pl.col("lab_orig").is_not_null()).select(KEY + ["lab_orig", "lab_zenodo_main", "lab_r2022", "lab_r2023", "lab_r2024"])
o = o.with_columns(bare(pl.col("lab_orig")).alias("pep_o"))

perc = pl.read_parquet(f"{OUT}/percolator_keyed.parquet").select(KEY + ["peptide", "q", "score", "charge"])
perc = perc.with_columns(bare(pl.col("peptide").str.replace(r"^[A-Z-]\.", "").str.replace(r"\.[A-Z-]$", "")).alias("pep_p"))
best = perc.sort("score", descending=True).unique(subset=KEY, keep="first")
npsm = perc.group_by(KEY).agg(pl.len().alias("n_psm"), pl.col("pep_p").n_unique().alias("n_pep"))
searched = perc.select("species", "raw_file").unique().with_columns(pl.lit(True).alias("searched"))
passing = perc.filter(pl.col("q") < 0.01)
per_file = passing.group_by("species", "raw_file").agg(pl.col(KEY[2]).n_unique().alias("file_psms"))
in_main_files = reg.filter(pl.col("lab_zenodo_main").is_not_null()).select("species", "raw_file").unique().with_columns(pl.lit(True).alias("file_in_main"))
main_pep_species = (reg.filter(pl.col("lab_zenodo_main").is_not_null()).select("species", bare(pl.col("lab_zenodo_main")).alias("pep"))
                    .unique().group_by("pep").agg(pl.col("species").alias("main_species")))
pass_pep_species = passing.select("species", pl.col("pep_p").alias("pep")).unique().group_by("pep").agg(pl.col("species").n_unique().alias("n_species_q01"))

conv = pl.scan_parquet(f"{T}/datazip__*.parquet").select(KEY + [pl.col("charge").is_not_null().alias("has_charge")]).collect()
f = (o.join(conv, on=KEY, how="left").join(searched, on=["species", "raw_file"], how="left").join(best.select(KEY + ["pep_p", "q"]), on=KEY, how="left")
      .join(npsm, on=KEY, how="left").join(per_file, on=["species", "raw_file"], how="left")
      .join(in_main_files, on=["species", "raw_file"], how="left")
      .join(main_pep_species, left_on="pep_p", right_on="pep", how="left")
      .join(pass_pep_species, left_on="pep_p", right_on="pep", how="left"))
same = pl.col("pep_p") == pl.col("pep_o")
fate = (pl.when(pl.col("lab_zenodo_main").is_not_null()).then(pl.lit("0 kept in main"))
        .when(pl.col("searched").is_null()).then(pl.lit("1 raw file not in the revised search"))
        .when(pl.col("pep_p").is_null()).then(pl.lit("2 no target PSM for the scan"))
        .when((pl.col("q") >= 0.01) & same).then(pl.lit("3a failed 1% FDR, same peptide"))
        .when(pl.col("q") >= 0.01).then(pl.lit("3b failed 1% FDR, other peptide"))
        .when(~pl.col("has_charge")).then(pl.lit("4a passed, MGF gives no precursor charge"))
        .when(pl.col("file_in_main").is_null()).then(pl.lit("4b passed, raw file dropped (too few PSMs)"))
        .when(pl.col("n_species_q01") > 1).then(pl.lit("4c passed, peptide shared and given to another species"))
        .otherwise(pl.lit("4d passed, removed for another reason")).alias("fate"))
f = f.with_columns(fate, same.alias("same_peptide"))
tot = f.group_by("fate").agg(pl.len().alias("spectra"), pl.col("same_peptide").sum().alias("of which revised PSM = original peptide")).sort("fate")
tot = tot.with_columns((100 * pl.col("spectra") / f.height).round(1).alias("%"))
tot.write_csv(f"{OUT}/fate__orig_to_main.csv")
with pl.Config(tbl_rows=-1, tbl_width_chars=300, fmt_str_lengths=70): print(tot)
by = f.group_by("species", "fate").len().pivot(on="fate", index="species", values="len").fill_null(0).sort("species")
by = by.select(["species"] + sorted(c for c in by.columns if c != "species"))
by.write_csv(f"{OUT}/fate__orig_to_main_by_species.csv")
with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=400): print(by)
# Spectra kept in main: did the label change?
k = f.filter(pl.col("lab_zenodo_main").is_not_null())
print("kept: revised label backbone == original backbone:", k.filter(bare(pl.col("lab_zenodo_main")) == pl.col("pep_o")).height, "of", k.height)
# 4c check: is the main species of that peptide really another one?
b = f.filter(pl.col("fate").str.starts_with("4c"))
print("4c with the peptide in main under another species:", b.filter(pl.col("main_species").is_not_null() & ~pl.col("main_species").list.contains(pl.col("species"))).height, "of", b.height)
c = f.filter(pl.col("fate").str.starts_with("4d"))
print("4d:", c.height); print(c.select("species", "raw_file", "scan", "lab_orig", "pep_p", "q", "n_psm", "n_pep", "file_psms", "main_species").head(12))
# 2: how many 'no target PSM' scans exist in any revised MGF?
n2 = f.filter(pl.col("fate").str.starts_with("2"))
print("2: no-PSM scans present in a MassIVE release anyway:", n2.filter(pl.col("lab_r2022").is_not_null() | pl.col("lab_r2023").is_not_null() | pl.col("lab_r2024").is_not_null()).height)
f.write_parquet(f"{OUT}/fate_orig.parquet")

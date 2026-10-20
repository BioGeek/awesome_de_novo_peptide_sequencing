"""One row of characteristics per version: size, charge, length, modifications, mass error, peaks.

Every version goes through the same normaliser (renorm) first, so a difference in the table is a
difference in the data and not in how its labels are spelled.
"""
import sys, polars as pl
from ns import original, release, renorm, mass_error_expr, il, OUT, T
C = ["species", "split", "pepmass", "charge", "seq_raw", "n_peaks", "mz_min", "mz_max"]

def versions():
    yield "original (species files)", original("orig_species", C)
    yield "original (yeast-held-out fold)", original("orig_fold", C + ["fold"]).filter(pl.col("fold") == "Saccharomyces-cerevisiae").drop("fold")
    for r in ["r2022", "r2023", "r2024"]: yield f"MassIVE {r}", release(r, C)
    # Percolator output has no spectrum, so no mass, peaks or m/z: labels and charges only.
    p = pl.scan_parquet(f"{OUT}/percolator_keyed.parquet").filter((pl.col("q") < 0.01) & (pl.col("rank_in_scan") == 1))
    yield "Percolator q<0.01, rank 1 (Zenodo intermediate)", p.select("species", pl.lit(None, pl.String).alias("split"),
        pl.lit(None, pl.Float64).alias("pepmass"), "charge", pl.col("peptide").alias("seq_raw"),
        pl.lit(None, pl.Int64).alias("n_peaks"), pl.lit(None, pl.Float64).alias("mz_min"), pl.lit(None, pl.Float64).alias("mz_max"))
    yield "Zenodo main", release("zenodo_main", C)
    yield "Zenodo balanced", release("zenodo_balanced", C)
    yield "ProteoBench", release("proteobench", C)
    yield "InstaNovo split", release("instanovo_split", C)
    yield "InstaNovo ninespecies_updated (IPC)", release("instanovo_ipc", C)
    yield "dIon", release("dion", C)

MOD = r"([A-Z]?\[[+-]\d+\.\d+\]-?)"
rows, mods = [], []
for name, lf in versions():
    try:
        df = lf.collect().pipe(renorm)
    except Exception as e:
        print(f"[{name}] skipped: {e}", flush=True); continue
    df = df.with_columns(pl.col("seq_bare").str.len_chars().alias("len"))
    has_mass = df["pepmass"].is_not_null().any() and df["charge"].is_not_null().any()
    if name.startswith("original (yeast"):
        for sp in ("train", "valid", "test"): print(f"   fold split {sp}: {(df["split"] == sp).sum()}")
    if has_mass:
        df = df.with_columns(mass_error_expr())
    r = {"version": name, "spectra": df.height,
         "peptides (as labelled)": df["seq_norm"].n_unique(),
         "peptides (bare, I=L)": df.select(il(pl.col("seq_bare"))).n_unique(),
         "species": df["species"].n_unique() if df["species"].is_not_null().any() else None}
    for z in (1, 2, 3, 4):
        r[f"z={z} %"] = round(100 * (df["charge"] == z).sum() / df.height, 2)
    r["z>=5 %"] = round(100 * (df["charge"] >= 5).sum() / df.height, 2)
    r["z missing"] = df["charge"].is_null().sum()
    r["length median"] = df["len"].median(); r["length min"] = df["len"].min(); r["length max"] = df["len"].max()
    r["length >30 %"] = round(100 * (df["len"] > 30).sum() / df.height, 2)
    r["modified %"] = round(100 * df["seq_norm"].str.contains(r"\[").sum() / df.height, 2)
    if has_mass:
        e = df["mass_err_ppm"].drop_nulls()
        r["mass err median ppm"] = round(e.median(), 2); r["mass err p99 ppm"] = round(e.quantile(0.99), 2)
        r["mass err >20 ppm"] = int((e > 20).sum()); r["mass err >50 ppm"] = int((e > 50).sum())
        r["label mass unknown"] = int(df["mass_err_ppm"].is_null().sum())
    r["peaks median"] = df["n_peaks"].median()
    r["precursor m/z min"] = df["pepmass"].min(); r["precursor m/z max"] = df["pepmass"].max()
    r["fragment m/z min"] = df["mz_min"].min(); r["fragment m/z max"] = df["mz_max"].max()
    rows.append(r)
    m = (df.select(pl.col("seq_norm").str.extract_all(MOD).alias("m")).explode("m").drop_nulls()
           .group_by("m").len().with_columns(pl.lit(name).alias("version")))
    mods.append(m)
    print(f"[{name}] done", flush=True)

prof = pl.DataFrame(rows)
prof.write_csv(f"{OUT}/profile__versions.csv")
with pl.Config(tbl_cols=-1, tbl_rows=-1, tbl_width_chars=400):
    print(prof.transpose(include_header=True, column_names="version"))
    mm = pl.concat(mods).pivot(on="version", index="m", values="len").fill_null(0).sort("m")
    mm.write_csv(f"{OUT}/profile__modifications.csv"); print(mm)

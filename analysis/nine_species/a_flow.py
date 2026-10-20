"""Every version on one spectrum key, and the flows between them.

registry.parquet: one row per (species, raw_file, scan) seen in ANY version, with a column per version
saying whether it is there and, where the version has one, which split it is in.
"""
import sys, polars as pl
from ns import original, release, renorm, il, KEY, OUT, SHORT
from a_versions import attach_raw   # same raw-file attachment the version comparison used

def keyed_release(name):
    return release(name, ["species", "raw_file", "scan", "pepmass_str", "rt", "seq_raw"]).collect().pipe(renorm)

def save(df, name):
    df.write_csv(f"{OUT}/{name}.csv")
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=300): print(f"--- {name}"); print(df)

if "build" in sys.argv:
    o = original("orig_species", ["species", "raw_file", "scan", "seq_raw"]).collect().pipe(renorm)
    reg = o.select(KEY + [pl.col("seq_norm").alias("lab_orig")])
    folds = original("orig_fold", ["species", "raw_file", "scan", "split", "fold"]).collect()
    folds = folds.filter(pl.col("split").is_in(["train", "valid", "test"]))
    fw = folds.pivot(on="fold", index=KEY, values="split", aggregate_function="first")
    fw = fw.rename({c: f"fold_{SHORT[c]}" for c in fw.columns if c not in KEY})
    reg = reg.join(fw, on=KEY, how="full", coalesce=True)
    r24 = None
    for r in ["r2022", "r2023", "r2024"]:
        d = keyed_release(r)
        if r == "r2024": r24 = d
        reg = reg.join(d.select(KEY + [pl.col("seq_norm").alias(f"lab_{r}")]), on=KEY, how="full", coalesce=True)
    # Zenodo main carries no raw file. Take it from MassIVE 2024 where the spectrum is there, else from
    # the Percolator PSM of the same main row (20k main spectra are in no MassIVE release: the shared-
    # peptide draw was re-run). Balanced and dIon are subsets of main, so they are keyed through main.
    zk = ["species", "scan", "pepmass_str", "rt"]
    zm = release("zenodo_main", ["species", "scan", "pepmass_str", "rt", "row", "seq_raw"]).collect().pipe(renorm)
    zm = zm.join(r24.select(zk + ["raw_file"]), on=zk, how="left")
    perc = pl.read_parquet(f"{OUT}/zenodo_main_keyed.parquet").select("species", "row", pl.col("raw_file").alias("raw_perc"))
    zm = zm.join(perc, on=["species", "row"], how="left").with_columns(pl.coalesce("raw_file", "raw_perc").alias("raw_file"))
    print(f"[zenodo_main] raw file from r2024 or Percolator for {zm['raw_file'].is_not_null().sum()} of {zm.height}")
    zm = zm.drop_nulls("raw_file")
    mainrow = zm.select("species", "row", "raw_file", "scan")
    reg = reg.join(zm.select(KEY + [pl.col("seq_norm").alias("lab_zenodo_main")]), on=KEY, how="full", coalesce=True)
    zb = release("zenodo_balanced", zk + ["seq_raw"]).collect().pipe(renorm).join(zm.select(zk + ["raw_file"]), on=zk, how="left")
    print(f"[zenodo_balanced] keyed through main for {zb['raw_file'].is_not_null().sum()} of {zb.height}")
    reg = reg.join(zb.drop_nulls("raw_file").select(KEY + [pl.col("seq_norm").alias("lab_zenodo_balanced")]), on=KEY, how="full", coalesce=True)
    ins = pl.read_parquet(f"{OUT}/instanovo_split_keyed.parquet").select(
        pl.col("o_species").alias("species"), "raw_file", "scan", pl.col("split").alias("instanovo_split")).unique(subset=KEY)
    reg = reg.join(ins, on=KEY, how="full", coalesce=True)
    dion = release("dion", ["species", "split", "extra"]).collect().with_columns(
        pl.col("extra").str.json_path_match("$.source_row_index").cast(pl.Int64).alias("row"))
    dk = dion.join(mainrow, on=["species", "row"], how="left")
    print(f"[dion] keyed {dk['raw_file'].is_not_null().sum()} of {dion.height}")
    reg = reg.join(dk.drop_nulls("raw_file").select(KEY + [pl.col("split").alias("dion_split")]), on=KEY, how="full", coalesce=True)
    reg.write_parquet(f"{OUT}/registry.parquet")
    print(reg.height, reg.columns)

if "flows" in sys.argv:
    reg = pl.read_parquet(f"{OUT}/registry.parquet")
    p = lambda c: pl.col(c).is_not_null()
    # 1. membership patterns across the chain
    pat = reg.with_columns([p(c).alias(c.replace("lab_", "")) for c in reg.columns if c.startswith("lab_")])
    cols = ["orig", "r2022", "r2023", "r2024", "zenodo_main", "zenodo_balanced"]
    save(pat.group_by(cols).len().sort("len", descending=True), "flow__membership_patterns")
    # 2. InstaNovo split -> revised
    ins = reg.filter(p("instanovo_split"))
    dest = (pl.when(p("lab_zenodo_balanced")).then(pl.lit("main + balanced (= ProteoBench)"))
              .when(p("lab_zenodo_main")).then(pl.lit("main only"))
              .when(p("lab_r2024")).then(pl.lit("MassIVE 2024, not in main"))
              .when(p("lab_r2022") | p("lab_r2023")).then(pl.lit("an earlier MassIVE release only"))
              .otherwise(pl.lit("in no revised version")))
    save(ins.with_columns(dest.alias("revised")).group_by("instanovo_split", "revised").len()
            .pivot(on="instanovo_split", index="revised", values="len").fill_null(0), "flow__instanovo_split_to_revised")
    save(ins.with_columns(dest.alias("revised")).group_by("species", "instanovo_split", "revised").len()
            .sort("species", "instanovo_split", "revised"), "flow__instanovo_split_to_revised_by_species")
    # 3. same spectrum, same label?
    for a, b in [("lab_orig", "lab_zenodo_main"), ("lab_orig", "lab_r2022")]:
        both = reg.filter(p(a) & p(b))
        cls = (pl.when(pl.col(a) == pl.col(b)).then(pl.lit("identical"))
                 .when(il(pl.col(a)) == il(pl.col(b))).then(pl.lit("I/L only"))
                 .when(il(pl.col(a).str.replace_all(r"\[[^\]]*\]-?", "")) == il(pl.col(b).str.replace_all(r"\[[^\]]*\]-?", ""))).then(pl.lit("modifications differ"))
                 .otherwise(pl.lit("different peptide")))
        save(both.with_columns(cls.alias("label")).group_by("species", "label").len()
                 .pivot(on="label", index="species", values="len").fill_null(0).sort("species"), f"flow__labels_{a[4:]}_vs_{b[4:]}")
    # 4. each original fold's split -> revised
    rows = []
    for c in [c for c in reg.columns if c.startswith("fold_")]:
        for sp in ("train", "valid", "test"):
            s = reg.filter(pl.col(c) == sp)
            rows.append({"held-out species": c[5:], "split": sp, "spectra": s.height,
                         "in main": s.filter(p("lab_zenodo_main")).height,
                         "in balanced": s.filter(p("lab_zenodo_balanced")).height,
                         "in dIon test": s.filter(pl.col("dion_split") == "test").height})
    save(pl.DataFrame(rows), "flow__original_folds_to_revised")
    # 5. dIon's splits <- where they came from
    d = reg.filter(p("dion_split"))
    save(d.group_by("dion_split", pl.col("instanovo_split").fill_null("not in InstaNovo split"),
                    p("lab_orig").alias("in original")).len().sort("dion_split", "len", descending=[False, True]),
         "flow__dion_origin")

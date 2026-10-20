"""denovo_benchmarks' nine-species datasets (MSV000096182) against every other version.

Its labels key a spectrum as '<raw file>_<chunk>:<index>' into chunked MGFs of at most 20,000
charged spectra (`fold` is the chunk, `row` the index; see make_jobs_dnb.py), so the scan comes from
the MGF and the comparison runs on the same (species, raw file, scan) key as everything else.
"""
import glob, re, polars as pl
from ns import release, renorm, il, KEY, OUT, T, PROTON, C13, peptide_mass
from compare import compare, summarise

def save(df, name):
    df.write_csv(f"{OUT}/{name}.csv")
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=300, fmt_str_lengths=60):
        print(f"--- {name}"); print(df)

lab = pl.concat([pl.read_csv(f, schema_overrides={"seq": pl.String, "spectrum_id": pl.String})
                 for f in glob.glob(f"{T}/../raw/dnb_labels/9_species_*_labels.csv")])
lab = lab.with_columns(pl.col("spectrum_id").str.extract(r"^(.*)_(\d+):\d+$", 1).alias("raw_file"),
                       pl.col("spectrum_id").str.extract(r"_(\d+):\d+$", 1).alias("fold"),
                       pl.col("spectrum_id").str.extract(r":(\d+)$", 1).cast(pl.Int64).alias("row"))
mgf = pl.scan_parquet(f"{T}/dnb__*.parquet").select("species", "raw_file", "fold", "row", "scan", "title", "pepmass",
                                                    "pepmass_str", "charge", "rt", "n_peaks", "top10_mz").collect()
print("MGF spectra in the nine-species chunks:", mgf.height, " labelled:", lab.height,
      " raw files:", mgf.select("species", "raw_file").n_unique())
d = lab.join(mgf, on=["raw_file", "fold", "row"], how="left")
print("labels whose chunk index resolves to a spectrum:", d["scan"].is_not_null().sum(), "of", d.height)
d = d.drop_nulls("scan").rename({"seq": "seq_raw"}).pipe(renorm)
print("duplicate keys:", d.height - d.select(KEY).unique().height)
d.write_parquet(f"{OUT}/dnb_keyed.parquet")

# 1. size per species against the builds
counts = d.group_by("species").agg(pl.len().alias("labelled"), pl.col("raw_file").n_unique().alias("raw files"))
allraw = pl.scan_parquet(f"{T}/datazip__*.parquet").select("species", "raw_file").unique().collect().group_by(
    "species").len().rename({"len": "raw files converted"})
mainraw = (pl.read_parquet(f"{OUT}/registry.parquet").filter(pl.col("lab_zenodo_main").is_not_null())
           .group_by("species").agg(pl.col("raw_file").n_unique().alias("raw files in main"), pl.len().alias("main")))
save(counts.join(allraw, on="species").join(mainraw, on="species").sort("species"), "dnb__counts")

# 2. which raw files it took, and whether main has them
reg = pl.read_parquet(f"{OUT}/registry.parquet")
files = d.select("species", "raw_file").unique()
mf = reg.filter(pl.col("lab_zenodo_main").is_not_null()).select("species", "raw_file").unique().with_columns(pl.lit(True).alias("in_main"))
of = reg.filter(pl.col("lab_orig").is_not_null()).select("species", "raw_file").unique().with_columns(pl.lit(True).alias("in_orig"))
f2 = files.join(mf, on=["species", "raw_file"], how="left").join(of, on=["species", "raw_file"], how="left")
save(f2.group_by("species").agg(pl.len().alias("files"), pl.col("in_main").sum().alias("of which in main"),
                                pl.col("in_orig").sum().alias("of which in original")).sort("species"), "dnb__raw_files")
print("raw files not in main:", f2.filter(pl.col("in_main").is_null()).select("species", "raw_file").rows())

# 3. membership: which other versions hold each denovo_benchmarks spectrum, and with which label
j = d.select(KEY + [pl.col("seq_norm").alias("lab_dnb"), "seq_bare"]).join(reg, on=KEY, how="left")
bare = lambda c: il(pl.col(c).str.replace_all(r"\[[^\]]*\]-?", ""))
for v in ["orig", "r2022", "r2023", "r2024", "zenodo_main", "zenodo_balanced"]:
    c = f"lab_{v}"
    j = j.with_columns(pl.when(pl.col(c).is_null()).then(pl.lit("absent"))
                       .when(pl.col(c) == pl.col("lab_dnb")).then(pl.lit("identical"))
                       .when(il(pl.col(c)) == il(pl.col("lab_dnb"))).then(pl.lit("I/L only"))
                       .when(bare(c) == bare("lab_dnb")).then(pl.lit("modifications differ"))
                       .otherwise(pl.lit("different peptide")).alias(f"vs_{v}"))
rows = []
for v in ["orig", "r2022", "r2023", "r2024", "zenodo_main", "zenodo_balanced"]:
    vc = dict(j.group_by(f"vs_{v}").len().iter_rows())
    rows.append({"version": v, **{k: vc.get(k, 0) for k in ["identical", "I/L only", "modifications differ", "different peptide", "absent"]}})
save(pl.DataFrame(rows), "dnb__vs_versions")
save(j.group_by("species", "vs_zenodo_main").len().pivot(on="vs_zenodo_main", index="species", values="len")
     .fill_null(0).sort("species"), "dnb__vs_main_by_species")
save(j.group_by(pl.col("instanovo_split").fill_null("not in it")).len(), "dnb__in_instanovo_split")
j.write_parquet(f"{OUT}/dnb_vs_registry.parquet")

# 4. modifications no other version has
rare = j.filter(pl.col("lab_dnb").str.contains(r"[DEHKRC]\[\+(?:57.02|21.98|14.02|39.99)\]|\[\+(?:57.02|26.02|12.00)\]-"))
print("labels with a modification the revised search did not allow:", rare.height,
      " of which main labels the spectrum identically up to I/L:",
      rare.filter(pl.col("vs_zenodo_main").is_in(["identical", "I/L only"])).height,
      " absent from main:", rare.filter(pl.col("vs_zenodo_main") == "absent").height)

# 5. the spectrum itself, against MassIVE 2024 (the conversion main also uses)
c24 = ["species", "raw_file", "scan", "pepmass", "charge", "rt", "seq_raw", "n_peaks", "top10_mz"]
r24 = release("r2024", c24).collect().pipe(renorm)
jj, both = compare(d.select(c24 + ["seq_norm", "seq_bare"]), r24, KEY)
pres, labc, phys = summarise(jj, both)
save(phys, "dnb__vs_r2024_physics")

# 6. precursor isotope offset against its own labels
u = d.select("seq_norm").unique().with_columns(pl.col("seq_norm").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm"))
x = d.join(u, on="seq_norm").drop_nulls("pm")
n = (pl.col("pepmass") - PROTON) * pl.col("charge")
x = x.with_columns([((n - pl.col("pm") - k * C13) / pl.col("pm") * 1e6).abs().alias(f"e{k}") for k in (-1, 0, 1, 2, 3)])
best = pl.min_horizontal([pl.col(f"e{k}") for k in (-1, 0, 1, 2, 3)])
x = x.with_columns(pl.when(pl.col("e0") == best).then(0).when(pl.col("e1") == best).then(1)
                   .when(pl.col("e2") == best).then(2).when(pl.col("e3") == best).then(3).otherwise(-1).alias("offset"),
                   best.alias("best"))
# 7. main's spectra whose precursor is the label's M+1 peak, where denovo_benchmarks labels them too
mm = j.filter(pl.col("lab_zenodo_main").is_not_null()).select(KEY + ["lab_dnb", "lab_zenodo_main", "vs_zenodo_main"])
u2 = mm.select(pl.col("lab_zenodo_main").alias("l")).unique().with_columns(
    pl.col("l").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm_main"))
u3 = mm.select(pl.col("lab_dnb").alias("l")).unique().with_columns(
    pl.col("l").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm_dnb"))
mm = mm.join(u2, left_on="lab_zenodo_main", right_on="l").join(u3, left_on="lab_dnb", right_on="l")
mm = mm.join(d.select(KEY + ["pepmass", "charge"]), on=KEY).with_columns(
    ((pl.col("pepmass") - PROTON) * pl.col("charge") - pl.col("pm_main")).round(0).alias("main_offset"),
    (pl.col("pm_dnb") - pl.col("pm_main")).alias("label_dmass"))
save(mm.group_by("main_offset", "vs_zenodo_main").agg(pl.len(), pl.col("label_dmass").median().alias("median label mass difference"))
     .sort("main_offset", "len", descending=[False, True]), "dnb__main_isotope_spectra")
print("isotope offsets:", dict(x.group_by("offset").len().iter_rows()), " best |err| >20 ppm:", (x["best"] > 20).sum(),
      " median:", round(x["best"].median(), 2))
print("charges:", dict(d.group_by("charge").len().sort("charge").iter_rows()))

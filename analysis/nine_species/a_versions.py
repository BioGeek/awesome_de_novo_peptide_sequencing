"""Cross-version analysis: put every version on one spectrum key, compare neighbours, trace flows."""
import sys, os, json, polars as pl
from ns import renorm, original, release, il, KEY, OUT, T, SHORT
from compare import compare, summarise
C = ["species", "raw_file", "scan", "split", "fold", "row", "pepmass_str", "pepmass", "charge", "rt",
     "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz", "extra"]
def load(name):
    if name == "orig": return original("orig_species", C).collect().pipe(renorm)
    if name == "orig_fold": return original("orig_fold", C).collect().pipe(renorm)
    return release(name, C).collect().pipe(renorm)
def save(df, name): df.write_csv(f"{OUT}/{name}.csv"); print(f"--- {name}"); print(df)

def attach_raw(df, ref, on, name):
    """Give a version without raw_file the raw_file of the reference version, on fields unique in both."""
    r = ref.select(on + ["raw_file"]).unique(subset=on, keep="none")       # keys ambiguous in ref are dropped
    out = df.drop("raw_file").join(r, on=on, how="left")
    print(f"[{name}] raw_file attached for {out['raw_file'].is_not_null().sum()} of {df.height}")
    return out

def run_compare(a, b, on, tag):
    j, both = compare(a, b, on)
    pres, lab, phys = summarise(j, both)
    tot = lambda d: d.select(pl.exclude("species").sum()).with_columns(pl.lit("ALL").alias("species")).select(d.columns)
    for nm, d in [("presence", pres), ("labels", lab), ("physics", phys)]:
        d = pl.concat([d, tot(d)]) if nm != "physics" else d
        save(d, f"{tag}__{nm}")
    return j, both

sections = sys.argv[1:] or ["releases"]
if "orig_vs_r2022" in sections:
    o, r = load("orig"), load("r2022")
    # raw-file naming must agree before any join means anything
    names = o.select("species", "raw_file").unique().join(r.select("species", "raw_file").unique().with_columns(pl.lit(1).alias("r")),
                                                         on=["species", "raw_file"], how="full", coalesce=True)
    save(names.group_by("species").agg(pl.col("r").is_not_null().sum().alias("raw_in_both"), pl.len().alias("raw_total")).sort("species"),
         "orig_vs_r2022__raw_file_names")
    j, both = run_compare(o, r, KEY, "orig_vs_r2022")
    both.select(KEY + ["seq_norm", "seq_norm_b", "label", "dmz_ppm", "top10_jaccard"]).write_parquet(f"{OUT}/orig_vs_r2022__both.parquet")
    j.filter(pl.col("presence") != "both").select(KEY + ["presence", "seq_norm", "seq_norm_b"]).write_parquet(f"{OUT}/orig_vs_r2022__unmatched.parquet")
if "releases" in sections:
    prev = None
    for name in ["r2022", "r2023", "r2024"]:
        cur = load(name)
        if prev is not None: run_compare(prev[1], cur, KEY, f"{prev[0]}_vs_{name}")
        prev = (name, cur)

if "r2024_vs_zenodo" in sections:
    r, z = load("r2024"), load("zenodo_main")
    z = attach_raw(z, r, ["species", "scan", "pepmass_str", "rt"], "zenodo_main <- r2024")
    run_compare(r, z.drop_nulls("raw_file"), KEY, "r2024_vs_zenodo_main")

if "instanovo_ipc" in sections:
    ipc = load("instanovo_ipc")
    for rel in ["r2023", "r2024"]:
        r = load(rel)
        for shift in (0, 1, -1):
            k = ipc.with_columns((pl.col("scan") + shift).alias("scan")).drop("raw_file")
            j = k.join(r.select("species", "scan", "pepmass", "raw_file", "seq_norm").rename({"seq_norm": "r_seq", "pepmass": "r_mz"}),
                       on=["species", "scan"], how="inner").filter((pl.col("pepmass") - pl.col("r_mz")).abs() < 1e-6)
            print(f"[ipc vs {rel}] scan shift {shift:+d}: {j.select('species', 'row').unique().height} of {ipc.height} matched; "
                  f"same label {(j['seq_norm'] == j['r_seq']).sum()}", flush=True)

if "fate" in sections:
    # Why is an original spectrum absent from the revised benchmark? Ask the revised search itself.
    o = load("orig").select(KEY + ["seq_norm", "seq_bare", "charge"])
    zm = pl.read_parquet(f"{OUT}/zenodo_main_keyed.parquet").select(KEY).with_columns(pl.lit(True).alias("in_main"))
    perc = pl.read_parquet(f"{OUT}/percolator_keyed.parquet").select(KEY + [pl.col("seq_norm").alias("p_seq"), "q", pl.col("charge").alias("p_charge"), "score"])
    best = perc.sort("score", descending=True).unique(subset=KEY, keep="first")
    raws = perc.select("species", "raw_file").unique().with_columns(pl.lit(True).alias("raw_searched"))
    f = o.join(zm, on=KEY, how="left").join(best, on=KEY, how="left").join(raws, on=["species", "raw_file"], how="left")
    same = il(pl.col("p_seq").str.replace_all(r"\[[^\]]*\]-?", "")) == il(pl.col("seq_bare"))
    f = f.with_columns(pl.when(pl.col("in_main")).then(pl.lit("kept in main"))
                       .when(pl.col("raw_searched").is_null()).then(pl.lit("raw file not in the revised search"))
                       .when(pl.col("p_seq").is_null()).then(pl.lit("no PSM for this scan"))
                       .when((pl.col("q") >= 0.01) & same).then(pl.lit("same peptide, q >= 0.01"))
                       .when(pl.col("q") >= 0.01).then(pl.lit("other peptide, q >= 0.01"))
                       .when(same).then(pl.lit("same peptide, q < 0.01, removed later"))
                       .otherwise(pl.lit("other peptide, q < 0.01, removed later")).alias("fate"))
    save(f.group_by(["species", "fate"]).len().pivot(on="fate", index="species", values="len").fill_null(0).sort("species"), "fate__orig_in_revised")
    save(f.group_by("fate").len().sort("len", descending=True), "fate__orig_in_revised_total")
    f.write_parquet(f"{OUT}/fate_orig.parquet")

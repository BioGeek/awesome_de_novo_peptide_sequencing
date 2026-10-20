"""Where the InstaNovo split comes from. Matched on (charge, label, m/z within 0.002), because its train and
valid precursors were stored as float32; the original is first collapsed to one row per distinct spectrum."""
import polars as pl
from ns import renorm, original, release, il, OUT, KEY
from compare import label_class, top10_jaccard
C = ["species", "raw_file", "scan", "split", "fold", "row", "pepmass_str", "pepmass", "charge", "rt",
     "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz"]
def save(df, name): df.write_csv(f"{OUT}/{name}.csv"); print(f"--- {name}"); print(df, flush=True)
ins = release("instanovo_split", C).collect().pipe(renorm).with_columns(il(pl.col("seq_norm")).alias("pep"))
osp = original("orig_species", C).collect().pipe(renorm).with_columns(pl.lit("species file").alias("member"))
ofo = original("orig_fold", C).collect().filter(pl.col("split") != "pool").with_columns(
    (pl.lit("excl ") + pl.col("fold") + pl.lit(":") + pl.col("split")).alias("member"))
memb = pl.concat([osp.select(KEY + ["member"]), ofo.select(KEY + ["member"])]).group_by(KEY).agg(pl.col("member").unique().sort())
spec = osp.drop("member").join(memb, on=KEY).with_columns(il(pl.col("seq_norm")).alias("pep"), (pl.col("pepmass") * 1000).round(0).cast(pl.Int64).alias("b"))
print("distinct original spectra:", spec.height, flush=True)
ins = ins.with_columns((pl.col("pepmass") * 1000).round(0).cast(pl.Int64).alias("b"))
spec = spec.with_row_index("oi")
o_key = spec.select("oi", "charge", "pep", "b", pl.col("pepmass").alias("o_mz"))
best_parts = []
for split in ["train", "valid", "test"]:
    x = ins.filter(pl.col("split") == split).select("split", "row", "charge", "pep", "pepmass", "b")
    c = pl.concat([x.with_columns(pl.col("b") + d).join(o_key, on=["charge", "pep", "b"], how="inner") for d in (-2, -1, 0, 1, 2)])
    c = c.filter((pl.col("pepmass") - pl.col("o_mz")).abs() < 0.002).select("split", "row", "oi")
    # attach peaks only to the surviving candidates, and keep the best peak match per record
    c = c.join(ins.select("split", "row", "seq_norm", "seq_bare", "n_peaks", "top10_mz"), on=["split", "row"]) \
         .join(spec.select("oi", pl.col("species").alias("o_species"), "raw_file", "scan", "member", pl.col("top10_mz").alias("top10_mz_b"),
                           pl.col("n_peaks").alias("o_peaks"), pl.col("seq_norm").alias("seq_norm_b"), pl.col("seq_bare").alias("seq_bare_b")), on="oi")
    # the best candidate shares the top-10 peaks AND the peak count; record how many candidates a record had
    c = c.with_columns(top10_jaccard(), (pl.col("n_peaks") - pl.col("o_peaks")).abs().alias("_dp"), pl.len().over(["split", "row"]).alias("n_cand")) \
         .sort(["top10_jaccard", "_dp"], descending=[True, False]).unique(subset=["split", "row"], keep="first")
    best_parts.append(c); print(split, c.height, flush=True)
best = pl.concat(best_parts)
print("InstaNovo records matched to an original spectrum:", best.height, "of", ins.height, flush=True)
mem = best.select("split", "member").explode("member").group_by(["split", "member"]).len().sort(["split", "len"], descending=[False, True])
save(mem.filter(pl.col("len") > 100), "instanovo_split__membership")
best = best.with_columns(label_class().alias("label"), (pl.col("n_peaks") - pl.col("o_peaks")).alias("dpeaks"))
save(best.group_by("split").agg(pl.len().alias("matched"), (pl.col("label") != "identical").sum().alias("label_differs"),
     (pl.col("top10_jaccard") < 1).sum().alias("top10_differs"), (pl.col("dpeaks") != 0).sum().alias("peak_count_differs"),
     pl.col("dpeaks").min().alias("min_dpeaks"), pl.col("dpeaks").max().alias("max_dpeaks")).sort("split"), "instanovo_split__fidelity")
print("candidates per record:", dict(best.group_by("n_cand").len().sort("n_cand").head(6).iter_rows()))
print(best.filter(pl.col("dpeaks") != 0).select("split", "n_peaks", "o_peaks", "n_cand", "o_species", "raw_file", "scan", "seq_norm").head(8))
unm = ins.join(best.select("split", "row"), on=["split", "row"], how="anti")
save(unm.group_by("split").len(), "instanovo_split__unmatched")
print(unm.select("split", "charge", "seq_raw").head(5))
best.select("split", "row", "o_species", "raw_file", "scan", "member").write_parquet(f"{OUT}/instanovo_split_keyed.parquet")

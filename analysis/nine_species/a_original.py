"""The original 2017 deposit on its own terms: species files, 10k subsets, folds, leakage."""
import polars as pl
from ns import renorm, original, il, SHORT, KEY, OUT
cols = ["species", "raw_file", "scan", "split", "fold", "seq_norm", "seq_bare", "pepmass", "charge", "n_peaks", "title"]
sp = original("orig_species", cols).collect().pipe(renorm)
k10 = original("orig_species10k", cols).collect().pipe(renorm)
fo = original("orig_fold", cols).collect().pipe(renorm)
print("species files:", sp.height, "spectra;", sp.select(KEY).unique().height, "distinct keys;", "null species:", sp["species"].null_count())
print(sp.group_by("species").agg(pl.len().alias("spectra"), pl.col("raw_file").n_unique().alias("raw_files"),
      il(pl.col("seq_norm")).n_unique().alias("peptides")).sort("species"))
# 10k subsets: subset of the full file?
j = k10.join(sp.select(KEY + ["seq_norm"]).rename({"seq_norm": "full_seq"}), on=KEY, how="left")
print("10k files:", k10.height, "spectra; found in the full species file:", j["full_seq"].is_not_null().sum(),
      "; same label there:", (j["full_seq"] == j["seq_norm"]).sum())
# folds
fo = fo.with_columns(pl.col("fold").alias("held_out"))
print("fold rows:", fo.height)
print(fo.group_by(["held_out", "split"]).len().pivot(on="split", index="held_out", values="len").sort("held_out"))
print("held-out species present in its own fold:", fo.filter(pl.col("species") == pl.col("held_out")).height)
# split disjointness within a fold, and stability of a spectrum's split across folds
nopool = fo.filter(pl.col("split") != "pool")
within = nopool.group_by(["held_out"] + KEY).agg(pl.col("split").n_unique().alias("n"))
print("spectra in more than one split of the same fold:", within.filter(pl.col("n") > 1).height)
stab = nopool.group_by(KEY).agg(pl.col("split").n_unique().alias("n_splits"), pl.col("held_out").n_unique().alias("n_folds"),
                                 pl.col("split").unique().sort().alias("splits"))
print("distinct spectra used by any fold:", stab.height)
print(stab.group_by("n_folds").len().sort("n_folds"))
print(stab.with_columns(pl.col("splits").list.join("+").alias("s")).group_by("s").len().sort("len", descending=True))
# coverage: which share of each species file ever enters a fold, and is the 80k cap the same draw in every fold?
used = stab.select(KEY).with_columns(pl.lit(True).alias("used"))
cov = sp.join(used, on=KEY, how="left").group_by("species").agg(pl.len().alias("in_file"), pl.col("used").sum().alias("used_by_some_fold"))
print(cov.with_columns((pl.col("used_by_some_fold") / pl.col("in_file")).round(3).alias("share")).sort("species"))
per = nopool.group_by(["species", "held_out"]).len()
print("per-species spectra across folds (min/max over folds):")
print(per.group_by("species").agg(pl.col("len").min().alias("min"), pl.col("len").max().alias("max"), pl.len().alias("folds")).sort("species"))
# leakage: held-out species' peptides that the fold trained on
fo2 = fo.with_columns(il(pl.col("seq_norm")).alias("pep"))
sp2 = sp.with_columns(il(pl.col("seq_norm")).alias("pep"))
rows = []
for s in sorted(sp["species"].unique().to_list()):
    tr = fo2.filter((pl.col("held_out") == s) & (pl.col("split") == "train"))["pep"].unique().implode()
    if fo2.filter(pl.col("held_out") == s).height == 0: continue
    test = sp2.filter(pl.col("species") == s)
    hit = test["pep"].is_in(tr)
    rows.append(dict(held_out=s, test_spectra=test.height, test_peptides=test["pep"].n_unique(),
                     spectra_with_trained_peptide=int(hit.sum()), share=round(float(hit.mean()), 3),
                     peptides_also_trained=int(test["pep"].unique().is_in(tr).sum())))
print(pl.DataFrame(rows))
pl.DataFrame(rows).write_csv(f"{OUT}/original_leakage.csv")

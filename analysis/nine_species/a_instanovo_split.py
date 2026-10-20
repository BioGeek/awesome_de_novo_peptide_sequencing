import polars as pl
from ns import T, il
d = pl.read_parquet(f"{T}/instanovo_split__*.parquet", columns=["split", "seq_norm", "seq_bare", "pepmass", "charge", "n_peaks"])
d = d.with_columns(il(pl.col("seq_norm")).alias("pep"))
print(d.group_by("split").agg(pl.len().alias("spectra"), pl.col("pep").n_unique().alias("peptides"),
      pl.col("n_peaks").median().alias("median_peaks"), pl.col("charge").mean().alias("mean_charge")).sort("split"))
train = d.filter(pl.col("split") == "train")["pep"].unique()
for s in ["valid", "test"]:
    x = d.filter(pl.col("split") == s)
    peps = x["pep"].unique()
    shared_peps = peps.is_in(train).sum()
    shared_spec = x["pep"].is_in(train).sum()
    print(f"{s}: {shared_peps} of {len(peps)} peptides ({shared_peps/len(peps):.1%}) also in train; "
          f"{shared_spec} of {x.height} spectra ({shared_spec/x.height:.1%})")
v = d.filter(pl.col("split") == "valid")["pep"].unique(); t = d.filter(pl.col("split") == "test")["pep"].unique()
print("valid/test shared peptides:", v.is_in(t).sum())
mods = d.filter(pl.col("seq_norm").str.contains(r"\["))
print("spectra with a modification per split:", dict(mods.group_by("split").len().iter_rows()))
print(mods.select(pl.col("seq_norm").str.extract_all(r"[A-Z]?\[[^\]]+\]").explode().alias("mod")).group_by("mod").len().sort("len", descending=True).head(8))

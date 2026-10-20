import polars as pl
from ns import renorm, release, il, OUT
from compare import label_class, top10_jaccard
C = ["species", "split", "row", "pepmass", "charge", "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz", "extra"]
d = release("dion", C).collect().pipe(renorm).with_columns(pl.col("extra").str.json_path_match("$.source_row_index").cast(pl.Int64).alias("src_row"))
z = release("zenodo_main", ["species", "row", "pepmass", "charge", "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz"]).collect().pipe(renorm)
zz = z.rename({c: f"{c}_b" for c in ["pepmass", "charge", "seq_raw", "seq_norm", "seq_bare", "n_peaks", "top10_mz"]}).rename({"row": "src_row"})
j = d.join(zz, on=["species", "src_row"], how="left")
j = j.with_columns(label_class().alias("label"), top10_jaccard(), (pl.col("pepmass") - pl.col("pepmass_b")).abs().alias("dmz"),
                   (pl.col("n_peaks") - pl.col("n_peaks_b")).alias("dpeaks"))
print("rows found:", j["seq_norm_b"].is_not_null().sum(), "of", j.height)
print(j.group_by("label").len())
print("charge differs:", (j["charge"] != j["charge_b"]).sum(), "; |dmz|>0.001:", (j["dmz"] > 0.001).sum(),
      "; top10 jaccard<1:", (j["top10_jaccard"] < 1).sum(), "; peak count differs:", (j["dpeaks"] != 0).sum(),
      "; median dpeaks:", j["dpeaks"].median(), "; min/max:", j["dpeaks"].min(), j["dpeaks"].max())
print(j.filter(pl.col("label") != "identical").select("species", "src_row", "seq_raw", "seq_raw_b").head(8))
print(j.filter(pl.col("top10_jaccard") < 1).select("species", "n_peaks", "n_peaks_b", "top10_jaccard").head(5))

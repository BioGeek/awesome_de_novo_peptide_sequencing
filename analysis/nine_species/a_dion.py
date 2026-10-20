"""dIon's nine-species representation benchmark: which release it comes from, what it keeps."""
import polars as pl, json
from ns import renorm, release, il, OUT
C = ["species", "raw_file", "scan", "split", "row", "pepmass_str", "pepmass", "charge", "rt", "seq_raw", "seq_norm", "seq_bare",
     "n_peaks", "top10_mz", "extra"]
def save(df, name): df.write_csv(f"{OUT}/{name}.csv"); print(f"--- {name}"); print(df)
d = release("dion", C).collect().pipe(renorm)
d = d.with_columns(pl.col("extra").str.json_path_match("$.source_row_index").cast(pl.Int64).alias("src_row"),
                   pl.col("extra").str.json_path_match("$.selected_group_size").cast(pl.Int64).alias("group"),
                   pl.col("pepmass").round(3).alias("mz3"), il(pl.col("seq_norm")).alias("pep"))
save(d.group_by(["split", "species"]).agg(pl.len().alias("spectra"), pl.col("pep").n_unique().alias("peptides"),
     pl.col("group").median().alias("median_group")).sort(["split", "species"]), "dion__composition")
print("charge distribution:", dict(d.group_by("charge").len().sort("charge").iter_rows()))
print("valid/test shared peptides:", d.filter(pl.col("split") == "valid")["pep"].unique().is_in(d.filter(pl.col("split") == "test")["pep"].unique().implode()).sum())
# which upstream table: match on content, then test whether src_row is that table's row order
for name in ["zenodo_main", "instanovo_ipc", "r2024", "r2023"]:
    if release(name, ["species"]) is None: print(name, "not extracted yet"); continue
    up = release(name, ["species", "raw_file", "scan", "row", "pepmass", "charge", "seq_norm"]).collect().pipe(renorm)
    up = up.with_columns(pl.col("pepmass").round(3).alias("mz3"), il(pl.col("seq_norm")).alias("pep"))
    j = d.join(up.select("species", "mz3", "charge", "pep", pl.col("row").alias("up_row"), "raw_file", "scan"),
               on=["species", "mz3", "charge", "pep"], how="left")
    hit = j.filter(pl.col("up_row").is_not_null()).unique(subset=["split", "row"])
    same_row = j.filter(pl.col("up_row") == pl.col("src_row")).select("split", "row").unique().height
    print(f"{name}: {hit.height} of {d.height} dIon spectra found; src_row equals that table's per-species row for {same_row}")

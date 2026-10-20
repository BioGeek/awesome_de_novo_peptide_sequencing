"""Spectra that enter or leave between neighbouring revised builds: is the peptide shared between species?

Wen & Noble assign each peptide found in more than one species to ONE species at random and drop its
spectra from the others. Re-running that step with a different draw moves whole peptides between
species files, which would show up here as spectra leaving one build and (other) spectra arriving.
"""
import polars as pl
from ns import release, renorm, il, OUT
c = ["species", "raw_file", "scan", "pepmass_str", "rt", "seq_raw"]
k = ["species", "scan", "pepmass_str", "rt"]
bare = lambda e: il(e.str.replace_all(r"[^A-Z]", ""))

perc = pl.read_parquet(f"{OUT}/percolator_keyed.parquet").filter((pl.col("q") < 0.01) & (pl.col("rank_in_scan") == 1))
perc = perc.with_columns(bare(pl.col("peptide").str.replace(r"^[A-Z-]\.", "").str.replace(r"\.[A-Z-]$", "")).alias("pep"))
nsp = perc.group_by("pep").agg(pl.col("species").n_unique().alias("n_species"))
print("bare peptides at q<0.01:", nsp.height, " in >1 species:", (nsp["n_species"] > 1).sum())

v = {n: release(n, c).collect().pipe(renorm).with_columns(bare(pl.col("seq_bare")).alias("pep")) for n in ["r2022", "r2023", "r2024"]}
v["zenodo_main"] = release("zenodo_main", c).collect().pipe(renorm).drop("raw_file").with_columns(bare(pl.col("seq_bare")).alias("pep"))

for n, d in v.items():
    s = d.group_by("pep").agg(pl.col("species").n_unique().alias("n")).filter(pl.col("n") > 1)
    print(f"{n}: {d.height} spectra, {d['pep'].n_unique()} bare peptides, peptides in >1 species file: {s.height}")

rows = []
for a, b in [("r2022", "r2023"), ("r2023", "r2024"), ("r2024", "zenodo_main")]:
    A, B = v[a].select(k + ["pep"]), v[b].select(k + ["pep"])
    for side, X, Y in [(f"in {a}, not {b}", A, B), (f"in {b}, not {a}", B, A)]:
        gone = X.join(Y.select(k), on=k, how="anti").join(nsp, on="pep", how="left")
        # did the PEPTIDE survive in the other build, and under which species?
        ysp = Y.group_by("pep").agg(pl.col("species").unique().alias("y_species"))
        g = gone.join(ysp, on="pep", how="left")
        rows.append({"transition": side, "spectra": g.height,
                     "peptide shared between species (q<0.01)": int((g["n_species"] > 1).sum()),
                     "peptide unique to one species": int((g["n_species"] == 1).sum()),
                     "peptide not at q<0.01": int(g["n_species"].is_null().sum()),
                     "peptide present in other build, other species only": int(g.filter(pl.col("y_species").is_not_null() & ~pl.col("y_species").list.contains(pl.col("species")))
                                                                            .height),
                     "peptide present in other build, same species": int(g.filter(pl.col("y_species").list.contains(pl.col("species"))).height),
                     "peptide absent from other build": int(g["y_species"].is_null().sum())})
out = pl.DataFrame(rows)
out.write_csv(f"{OUT}/swing__transitions.csv")
with pl.Config(tbl_cols=-1, tbl_width_chars=400, fmt_str_lengths=60): print(out.transpose(include_header=True, column_names="transition"))

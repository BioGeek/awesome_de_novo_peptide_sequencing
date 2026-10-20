"""Is the precursor m/z in each version the monoisotopic peak of its label?

For every labelled spectrum: the isotope offset k in -1..3 that brings the label's mass closest to the
precursor's neutral mass, and the error there. k != 0 means the MGF's precursor sits on an isotope peak,
which a model using the precursor mass as a hard constraint (Casanovo's beam filter, for one) cannot
reconcile with the true sequence.
"""
import polars as pl
from ns import original, release, renorm, peptide_mass, OUT, PROTON, C13

C = ["species", "pepmass", "charge", "seq_raw"]
V = [("original (species files)", lambda: original("orig_species", C)),
     ("MassIVE r2022", lambda: release("r2022", C)), ("MassIVE r2023", lambda: release("r2023", C)),
     ("MassIVE r2024", lambda: release("r2024", C)), ("Zenodo main", lambda: release("zenodo_main", C)),
     ("Zenodo balanced = ProteoBench", lambda: release("zenodo_balanced", C)),
     ("InstaNovo split", lambda: release("instanovo_split", C)), ("dIon", lambda: release("dion", C))]
rows, per_sp = [], []
for name, lf in V:
    df = lf().collect().pipe(renorm)
    u = df.select("seq_norm").unique()
    u = u.with_columns(pl.col("seq_norm").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm"))
    df = df.join(u, on="seq_norm", how="left").drop_nulls("pm")
    neutral = (pl.col("pepmass") - PROTON) * pl.col("charge")
    errs = {k: ((neutral - pl.col("pm") - k * C13) / pl.col("pm") * 1e6) for k in (-1, 0, 1, 2, 3)}
    df = df.with_columns([errs[k].alias(f"e{k}") for k in errs])
    best = pl.min_horizontal([pl.col(f"e{k}").abs() for k in errs])
    off = pl.when(pl.col("e0").abs() == best).then(0)
    for k in (1, 2, 3, -1): off = off.when(pl.col(f"e{k}").abs() == best).then(k)
    df = df.with_columns(off.alias("offset"), best.alias("best_ppm"))
    n = df.height
    r = {"version": name, "labelled spectra": n}
    for k in (0, 1, 2, 3, -1):
        r[f"offset {k:+d}"] = int((df["offset"] == k).sum())
    r["non-monoisotopic %"] = round(100 * (df["offset"] != 0).sum() / n, 2)
    r["best |err| >20 ppm"] = int((df["best_ppm"] > 20).sum())
    rows.append(r)
    s = df.group_by("species").agg(pl.len().alias("n"), (pl.col("offset") != 0).sum().alias("non_mono")).with_columns(
        (100 * pl.col("non_mono") / pl.col("n")).round(2).alias("%"), pl.lit(name).alias("version"))
    per_sp.append(s)
    print(f"[{name}] done", flush=True)
out = pl.DataFrame(rows); out.write_csv(f"{OUT}/isotope__offsets.csv")
with pl.Config(tbl_cols=-1, tbl_width_chars=300): print(out)
ps = pl.concat(per_sp).drop_nulls("species").pivot(on="version", index="species", values="%").sort("species")
ps.write_csv(f"{OUT}/isotope__non_mono_by_species.csv")
with pl.Config(tbl_cols=-1, tbl_width_chars=300): print(ps)

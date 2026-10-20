"""Cross-version leakage: train on one version's training split, evaluate on another version's data.

Two grains, both counted over EVALUATION spectra:
  same spectrum : the evaluation spectrum is itself a training spectrum (same raw file and scan)
  same peptide  : its peptide (bare, I=L) is among the training peptides, whatever the spectrum
"""
import polars as pl
from ns import il, OUT, SHORT

reg = pl.read_parquet(f"{OUT}/registry.parquet")
lab = pl.coalesce("lab_zenodo_main", "lab_r2024", "lab_r2023", "lab_r2022", "lab_orig")
reg = reg.with_columns(il(lab.str.replace_all(r"\[[^\]]*\]-?", "")).alias("pep"),
                       il(pl.col("lab_orig").str.replace_all(r"\[[^\]]*\]-?", "")).alias("pep_orig"))

def train_set(name):
    if name == "InstaNovo / NovoBench train (original, yeast held out)":
        t = reg.filter(pl.col("instanovo_split") == "train"); return t, "pep_orig"
    raise KeyError(name)

TRAIN = ["InstaNovo / NovoBench train (original, yeast held out)"]
EVAL = {
    "InstaNovo test (original yeast file)": (pl.col("instanovo_split") == "test", "pep_orig"),
    "InstaNovo valid": (pl.col("instanovo_split") == "valid", "pep_orig"),
    "Zenodo main, all species": (pl.col("lab_zenodo_main").is_not_null(), "pep"),
    "Zenodo main, yeast only": (pl.col("lab_zenodo_main").is_not_null() & (pl.col("species") == "Saccharomyces-cerevisiae"), "pep"),
    "Zenodo balanced = ProteoBench": (pl.col("lab_zenodo_balanced").is_not_null(), "pep"),
    "dIon test": (pl.col("dion_split") == "test", "pep"),
    "dIon validation": (pl.col("dion_split") == "valid", "pep"),
}
# denovo_benchmarks' selection is keyed by a_dnb.py, which runs after this script's first pass.
import os
if os.path.exists(f"{OUT}/dnb_keyed.parquet"):
    _dnb = pl.read_parquet(f"{OUT}/dnb_keyed.parquet").select("species", "raw_file", "scan", "seq_bare")
    reg = reg.join(_dnb.with_columns(il(pl.col("seq_bare")).alias("pep_dnb")).drop("seq_bare"),
                   on=["species", "raw_file", "scan"], how="full", coalesce=True)
    EVAL["denovo_benchmarks selection"] = (pl.col("pep_dnb").is_not_null(), "pep_dnb")
rows = []
for tn in TRAIN:
    t, tp = train_set(tn)
    tkeys = t.select("species", "raw_file", "scan").with_columns(pl.lit(True).alias("t_spec"))
    tpeps = t.select(pl.col(tp).alias("pep")).unique().with_columns(pl.lit(True).alias("t_pep"))
    for en, (cond, ep) in EVAL.items():
        e = reg.filter(cond).select("species", "raw_file", "scan", pl.col(ep).alias("pep"))
        e = e.join(tkeys, on=["species", "raw_file", "scan"], how="left").join(tpeps, on="pep", how="left")
        n = e.height
        rows.append({"train": tn, "evaluate on": en, "spectra": n,
                     "same spectrum": int(e["t_spec"].sum() or 0),
                     "same spectrum %": round(100 * (e["t_spec"].sum() or 0) / n, 1),
                     "peptide seen in training": int(e["t_pep"].sum() or 0),
                     "peptide seen %": round(100 * (e["t_pep"].sum() or 0) / n, 1),
                     "eval peptides": e["pep"].n_unique(),
                     "eval peptides seen %": round(100 * e.filter(pl.col("t_pep")).select("pep").n_unique() / e["pep"].n_unique(), 1)})
        if en == "Zenodo balanced = ProteoBench":
            by = e.group_by("species").agg(pl.len().alias("spectra"), pl.col("t_spec").sum().alias("same spectrum"),
                                           pl.col("t_pep").sum().alias("peptide seen")).sort("species")
            by.write_csv(f"{OUT}/leak__instanovo_train_to_proteobench_by_species.csv")
            with pl.Config(tbl_rows=-1): print(by)
out = pl.DataFrame(rows)
out.write_csv(f"{OUT}/leak__cross_version.csv")
with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=400, fmt_str_lengths=60): print(out.drop("train"))

# The revised benchmark's own protocol, leave one species out of main: by construction no shared peptide.
m = reg.filter(pl.col("lab_zenodo_main").is_not_null()).select("species", "pep").unique()
shared = m.group_by("pep").agg(pl.col("species").n_unique().alias("n")).filter(pl.col("n") > 1)
print("Zenodo main, peptides (bare, I=L) in more than one species:", shared.height)
# And the original's own protocol, per fold: held-out species' spectra whose peptide the fold trained on
rows = []
for c in [c for c in reg.columns if c.startswith("fold_")]:
    sp = [k for k, v in SHORT.items() if v == c[5:]][0]
    tr = reg.filter(pl.col(c) == "train").select(pl.col("pep_orig").alias("pep")).unique().with_columns(pl.lit(True).alias("seen"))
    ho = reg.filter((pl.col("species") == sp) & pl.col("lab_orig").is_not_null()).select(pl.col("pep_orig").alias("pep")).join(tr, on="pep", how="left")
    ft = reg.filter(pl.col(c) == "test").select(pl.col("pep_orig").alias("pep")).join(tr, on="pep", how="left")
    rows.append({"held-out species": c[5:], "held-out spectra": ho.height, "peptide trained on %": round(100 * (ho["seen"].sum() or 0) / ho.height, 1),
                 "fold test spectra": ft.height, "fold test peptide trained on %": round(100 * (ft["seen"].sum() or 0) / max(ft.height, 1), 2)})
o = pl.DataFrame(rows); o.write_csv(f"{OUT}/leak__original_folds.csv")
with pl.Config(tbl_rows=-1, tbl_width_chars=300): print(o)

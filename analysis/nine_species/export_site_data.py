"""Collect the results the site page draws into one small JSON, committed as site_data.json.

Run on the analysis host after the a_*.py scripts; build_pages.py inlines the file into
pages/analyses/nine-species-versions.qmd, so a change to the numbers changes the page's hash and
the next publish re-renders it.
"""
import json, os, sys
import polars as pl
B = os.path.expanduser("~/data/ninespecies"); R = f"{B}/results"
SHORT = {"Saccharomyces-cerevisiae": "yeast", "Bacillus-subtilis": "B. subtilis", "Candidatus-endoloripes": "C. endoloripes",
         "Apis-mellifera": "honeybee", "H.-sapiens": "human", "Methanosarcina-mazei": "M. mazei",
         "Mus-musculus": "mouse", "Vigna-mungo": "V. mungo", "Solanum-lycopersicum": "tomato"}

def rows(name, rename_species=True):
    df = pl.read_csv(f"{R}/{name}.csv")
    if rename_species and "species" in df.columns:
        df = df.with_columns(pl.col("species").replace(SHORT))
    return df.to_dicts()

from ns import release, KEY

# Spectra per species in each revised build.
counts = []
for build in ["r2022", "r2023", "r2024", "zenodo_main", "zenodo_balanced"]:
    d = release(build, ["species"]).collect().group_by("species").len()
    counts += [(build, sp, n) for sp, n in d.iter_rows()] + [(build, "ALL", d["len"].sum())]
pl.DataFrame(counts, schema=["build", "species", "n"], orient="row").write_csv(f"{R}/build_counts.csv")

# THE SANKEY: every spectrum in any version, once per column. Column 1 is where it started (which
# InstaNovo/NovoBench split, the rest of v1, or not in v1 at all), column 2 which revised version
# holds it, column 3 where it sits inside v2 main.
reg = pl.read_parquet(f"{R}/registry.parquet")
dnb = pl.read_parquet(f"{R}/dnb_keyed.parquet").select(KEY).with_columns(pl.lit(True).alias("dnb"))
reg = reg.join(dnb, on=KEY, how="full", coalesce=True)
p = lambda c: pl.col(c).is_not_null()
reg = reg.with_columns(
    pl.when(pl.col("instanovo_split") == "train").then(pl.lit("InstaNovo train"))
      .when(pl.col("instanovo_split") == "valid").then(pl.lit("InstaNovo valid"))
      .when(pl.col("instanovo_split") == "test").then(pl.lit("InstaNovo test"))
      .when(p("lab_orig")).then(pl.lit("v1, outside the InstaNovo split"))
      .otherwise(pl.lit("not in v1")).alias("c1"),
    pl.when(p("lab_zenodo_main")).then(pl.lit("v2 main"))
      .when(p("lab_r2022") | p("lab_r2023") | p("lab_r2024")).then(pl.lit("a MassIVE build of v2 only"))
      .when(p("dnb")).then(pl.lit("denovo_benchmarks only"))
      .otherwise(pl.lit("no revised version")).alias("c2"),
    pl.when(~p("lab_zenodo_main")).then(None)
      .when(p("lab_zenodo_balanced") & p("dion_split")).then(pl.lit("v2 balanced and dIon"))
      .when(p("lab_zenodo_balanced")).then(pl.lit("v2 balanced (= ProteoBench)"))
      .when(p("dion_split")).then(pl.lit("dIon only"))
      .otherwise(pl.lit("rest of v2 main")).alias("c3"))
# As triples, so the page can colour the second stage by where each spectrum STARTED: that is what
# shows InstaNovo's training scans arriving in v2 balanced, which is ProteoBench.
sankey = [dict(c1=a, c2=b, c3=c, n=n) for a, b, c, n in reg.group_by("c1", "c2", "c3").len().iter_rows()]

out = {
    "sankey": sankey,
    "build_counts": rows("build_counts"),
    "fate_by_species": rows("fate__orig_to_main_by_species"),
    "fate_total": rows("fate__orig_to_main"),
    "converted": rows("converted__summary"),
    "isotope_offsets": rows("isotope__offsets"),
    "isotope_by_species": rows("isotope__non_mono_by_species"),
    "swing": rows("swing__transitions"),
    "leak": rows("leak__cross_version"),
    "leak_proteobench": rows("leak__instanovo_train_to_proteobench_by_species"),
    "leak_folds": rows("leak__original_folds"),
    "instanovo_to_revised": rows("flow__instanovo_split_to_revised"),
    "dnb_counts": rows("dnb__counts"),
    "dnb_vs_versions": rows("dnb__vs_versions"),
    "dnb_isotope": rows("dnb__main_isotope_spectra"),
    "dnb_in_instanovo_split": rows("dnb__in_instanovo_split"),
}
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else f"{R}/site_data.json", "w"), separators=(",", ":"))
print({k: len(v) for k, v in out.items()})

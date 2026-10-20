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

out = {
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

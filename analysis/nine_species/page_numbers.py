"""Every number the site page prints, computed from the pipeline's tables, with where it comes from.

    python page_numbers.py                # compute all, print them, write page_numbers.json
    python page_numbers.py --only v1_psms # one number
    python page_numbers.py --check        # recompute and compare with the committed page_numbers.json

Each number is a function registered with @number(id, description, sources, made_by). `sources`
are keys of sources.SOURCES (the public files it ultimately reads) and `made_by` the pipeline
scripts whose outputs it reads, in run_all.sh's order. The function's own code is copied into
page_numbers.json, and the page's appendix prints it, so a reader can see exactly what was counted.
build_pages.py turns `{{n:<id>}}` in page.qmd into a link to that appendix entry.
"""
import functools, glob, inspect, json, os, re, sys, textwrap
import polars as pl
from ns import original, release, renorm, il, KEY, OUT, T, PROTON, C13, peptide_mass
from sources import SOURCES

HERE = os.path.dirname(os.path.abspath(__file__))
REG = {}


def number(id, desc, sources, made_by=(), fmt=",d", suffix=""):
    def deco(f):
        REG[id] = dict(f=f, desc=desc, sources=list(sources), made_by=list(made_by), fmt=fmt, suffix=suffix)
        return f
    return deco


@functools.cache
def csv(name):
    return pl.read_csv(f"{OUT}/{name}.csv")


@functools.cache
def registry():
    return pl.read_parquet(f"{OUT}/registry.parquet")


@functools.cache
def table(kind, cols=("species", "raw_file", "scan", "seq_raw")):
    """A version's spectra, labels normalised. kind is a release() prefix, or 'v1' for the original."""
    lf = original("orig_species", list(cols)) if kind == "v1" else release(kind, list(cols))
    return lf.collect().pipe(renorm) if "seq_raw" in cols else lf.collect()


def row(df, col, **where):
    for k, v in where.items():
        df = df.filter(pl.col(k) == v)
    return df[col][0]


bare = lambda c: il(pl.col(c).str.replace_all(r"\[[^\]]*\]-?", ""))
SPECIES = ["Apis-mellifera", "Bacillus-subtilis", "Candidatus-endoloripes", "H.-sapiens", "Methanosarcina-mazei",
           "Mus-musculus", "Saccharomyces-cerevisiae", "Solanum-lycopersicum", "Vigna-mungo"]

# ---- the versions ---------------------------------------------------------------------------------

@number("v1_psms", "Labelled spectra in v1's nine species files", ["v1"], ["make_jobs.py", "extract.py"])
def _():
    return table("v1", ("species",)).height

@number("v1_labels_with_l", "v1 labels containing an L (it writes every leucine as I)", ["v1"], ["make_jobs.py", "extract.py"])
def _():
    return int(table("v1")["seq_raw"].str.contains("L").sum())

@number("v1_fold_min", "Spectra in the smallest of v1's nine folds (train + valid + test)", ["v1_folds"], ["make_jobs.py", "extract.py"])
def _():
    f = original("orig_fold", ["fold", "split"]).filter(pl.col("split").is_in(["train", "valid", "test"])).collect()
    return int(f.group_by("fold").len()["len"].min())

@number("v1_fold_max", "Spectra in the largest of v1's nine folds", ["v1_folds"], ["make_jobs.py", "extract.py"])
def _():
    f = original("orig_fold", ["fold", "split"]).filter(pl.col("split").is_in(["train", "valid", "test"])).collect()
    return int(f.group_by("fold").len()["len"].max())

for _split in ("train", "valid", "test"):
    def _f(s=_split):
        return release("instanovo_split", ["split"]).filter(pl.col("split") == s).collect().height
    number(f"instanovo_{_split}", f"InstaNovo split, {_split}", ["instanovo_split"], ["extract_tabular.py"])(_f)

@number("instanovo_all", "InstaNovo split, all three splits", ["instanovo_split"], ["extract_tabular.py"])
def _():
    return release("instanovo_split", ["split"]).collect().height

@number("instanovo_row_aligned", "InstaNovo split records whose precursor and peak count equal the v1 record in the same "
        "position (v1's yeast-excluded fold files for train and valid, v1's yeast file for test)",
        ["instanovo_split", "v1", "v1_folds"], ["extract_tabular.py", "make_jobs.py"])
def _():
    n = 0
    for split in ("train", "valid", "test"):
        a = release("instanovo_split", ["split", "row", "pepmass", "n_peaks"]).filter(pl.col("split") == split).collect()
        if split == "test":
            b = original("orig_species", ["species", "row", "pepmass", "n_peaks"]).filter(
                pl.col("species") == "Saccharomyces-cerevisiae").collect()
        else:
            b = original("orig_fold", ["fold", "split", "row", "pepmass", "n_peaks"]).filter(
                (pl.col("fold") == "Saccharomyces-cerevisiae") & (pl.col("split") == split)).collect()
        j = a.join(b.select("row", pl.col("pepmass").alias("pm_b"), pl.col("n_peaks").alias("np_b")), on="row")
        n += j.filter(((pl.col("pepmass") - pl.col("pm_b")).abs() < 1e-3) & (pl.col("n_peaks") == pl.col("np_b"))).height
    return n

for _b, _src in (("r2022", "massive_2022"), ("r2023", "massive_2023"), ("r2024", "massive_2024"),
                 ("zenodo_main", "v2_main"), ("zenodo_balanced", "v2_balanced"), ("proteobench", "proteobench"),
                 ("instanovo_ipc", "instanovo_ipc")):
    def _f(b=_b):
        return release(b, ["species"]).collect().height
    number(f"n_{_b}", f"Spectra in {_b}", [_src], ["make_jobs.py" if _b.startswith("r20") else "make_jobs_zip.py"
                                                    if _b != "instanovo_ipc" else "extract_tabular.py", "extract.py"])(_f)

@number("ipc_is_2023", "InstaNovo's ninespecies_updated records identical to a MassIVE 2023 record "
        "(same species, scan, retention time, precursor, charge, label, peak count and top-10 peaks)",
        ["instanovo_ipc", "massive_2023"], ["extract_tabular.py", "make_jobs.py"])
def _():
    c = ["species", "scan", "pepmass", "rt", "charge", "seq_raw", "n_peaks", "top10_mz"]
    k = ["species", "scan", "rt3", "mz4"]
    a = release("instanovo_ipc", c).collect().pipe(renorm)
    b = release("r2023", c).collect().pipe(renorm)
    a, b = [x.with_columns(pl.col("rt").round(3).alias("rt3"), pl.col("pepmass").round(4).alias("mz4")) for x in (a, b)]
    j = a.join(b.select(k + ["seq_norm", "charge", "n_peaks", "top10_mz"]), on=k, suffix="_b")
    same = ((j["seq_norm"] == j["seq_norm_b"]) & (j["charge"] == j["charge_b"]) & (j["n_peaks"] == j["n_peaks_b"])
            & j.select(pl.col("top10_mz").list.sort() == pl.col("top10_mz_b").list.sort()).to_series())
    return int(same.sum())

for _s in ("test", "valid"):
    def _f(s=_s):
        return release("dion", ["split"]).filter(pl.col("split") == s).collect().height
    number(f"dion_{_s}", f"dIon representation benchmark, {_s}", ["dion"], ["extract_tabular.py"])(_f)

@number("dion_all", "dIon representation benchmark, both splits", ["dion"], ["extract_tabular.py"])
def _():
    return release("dion", ["split"]).collect().height

@number("dnb_labels", "Labels in denovo_benchmarks' nine 9_species_*_labels.csv files", ["dnb"], ["download_sources.py"])
def _():
    return sum(pl.read_csv(f).height for f in glob.glob(f"{T}/../raw/dnb_labels/9_species_*_labels.csv"))

@number("dnb_raw_files", "Raw files denovo_benchmarks' nine-species labels cite", ["dnb"], ["download_sources.py"])
def _():
    ids = pl.concat([pl.read_csv(f) for f in glob.glob(f"{T}/../raw/dnb_labels/9_species_*_labels.csv")])["spectrum_id"]
    return ids.str.extract(r"^(.*)_\d+:\d+$", 1).n_unique()

@number("converted", "MS2 spectra in v2's converted MGFs (nine-species-data.zip)", ["v2_data"], ["datazip.py", "a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "converted MS2", species="ALL"))

@number("perc_target_psms", "Target PSMs in v2's Percolator output", ["v2_intermediate"], ["extract_tabular.py", "percolator_keyed.py"])
def _():
    return pl.read_parquet(f"{OUT}/percolator_keyed.parquet", columns=["scan"]).height

@number("perc_target_scans", "Converted scans whose best Percolator match is a target", ["v2_data", "v2_intermediate"],
        ["datazip.py", "a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "target PSM", species="ALL"))

# ---- where v1's spectra went ------------------------------------------------------------------------

_FATE = {"fate_kept": "0", "fate_raw_missing": "1", "fate_no_psm": "2", "fate_fdr_same": "3a",
         "fate_fdr_other": "3b", "fate_nocharge": "4a", "fate_shared": "4c"}
for _id, _p in _FATE.items():
    def _f(p=_p):
        f = csv("fate__orig_to_main")
        return int(f.filter(pl.col("fate").str.starts_with(p + " "))["spectra"][0])
    number(_id, f"v1 spectra whose fate in v2 main is step {_p} (see the table)", ["v1", "v2_main", "v2_intermediate", "v2_data"],
           ["a_flow.py", "a_fate.py"])(_f)

@number("fate_kept_pct", "Share of v1's spectra in v2 main", ["v1", "v2_main"], ["a_flow.py", "a_fate.py"], fmt=".1f", suffix="%")
def _():
    f = csv("fate__orig_to_main")
    return 100 * f.filter(pl.col("fate").str.starts_with("0 "))["spectra"][0] / f["spectra"].sum()

for _id, _col in (("nopsm_with_charge", "converted with charge"), ("nopsm_without_charge", "converted without charge")):
    def _f(c=_col):
        f = csv("converted__orig_fate")
        return int(f.filter(pl.col("fate").str.starts_with("2 "))[c][0])
    number(_id, f"v1 spectra with no target PSM, {_col}", ["v1", "v2_data", "v2_intermediate"], ["a_fate.py", "a_converted.py"])(_f)

@number("kept_same", "v1 spectra in v2 main with the same backbone and modifications (I=L)", ["v1", "v2_main"], ["a_flow.py"])
def _():
    f = csv("flow__labels_orig_vs_zenodo_main")
    return int(f["identical"].sum() + f["I/L only"].sum())

@number("kept_mods", "v1 spectra in v2 main with the same backbone, other modifications", ["v1", "v2_main"], ["a_flow.py"])
def _():
    return int(csv("flow__labels_orig_vs_zenodo_main")["modifications differ"].sum())

@number("kept_diff", "v1 spectra in v2 main labelled as another peptide", ["v1", "v2_main"], ["a_flow.py"])
def _():
    return int(csv("flow__labels_orig_vs_zenodo_main")["different peptide"].sum())

@number("orig_label_il_only", "Spectra v1 and v2 main share whose labels differ only by I/L", ["v1", "v2_main"], ["a_flow.py"])
def _():
    return int(csv("flow__labels_orig_vs_zenodo_main")["I/L only"].sum())

# ---- from raw files to main -------------------------------------------------------------------------

@number("nocharge_total", "Converted spectra whose MGF gives no precursor charge", ["v2_data"], ["datazip.py", "a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "no charge", species="ALL"))

for _sp, _id in (("H.-sapiens", "nocharge_human_pct"), ("Candidatus-endoloripes", "nocharge_cendo_pct"),
                 ("Bacillus-subtilis", "nocharge_bsub_pct")):
    def _f(sp=_sp):
        r = csv("converted__summary").filter(pl.col("species") == sp)
        return 100 * r["no charge"][0] / r["converted MS2"][0]
    number(_id, f"Share of {_sp}'s converted spectra with no precursor charge", ["v2_data"], ["datazip.py", "a_converted.py"],
           fmt=".0f", suffix="%")(_f)

@number("nocharge_passing", "Charge-less spectra Percolator accepted at q < 0.01", ["v2_data", "v2_intermediate"],
        ["datazip.py", "a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "q<0.01, no charge", species="ALL"))

@number("nocharge_in_main", "Charge-less spectra in v2 main", ["v2_data", "v2_main"], ["datazip.py", "a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "in main, no charge", species="ALL"))

@number("q01_scans", "Converted scans whose best target PSM has q < 0.01", ["v2_data", "v2_intermediate"], ["a_converted.py"])
def _():
    return int(row(csv("converted__summary"), "q<0.01", species="ALL"))

def _dropped_files():
    return csv("converted__files_not_in_main")

@number("bsub_chargeless_files", "B. subtilis MGFs with nothing in main, every spectrum charge-less", ["v2_data"], ["a_converted.py"])
def _():
    return _dropped_files().filter(pl.col("species") == "Bacillus-subtilis").height

@number("bsub_file_psm_min", "Fewest q < 0.01 PSMs in one of those files, the DIA run aside", ["v2_data", "v2_intermediate"], ["a_converted.py"])
def _():
    d = _dropped_files().filter((pl.col("species") == "Bacillus-subtilis") & ~pl.col("raw_file").str.contains("DIA"))
    return int(d["q<0.01"].min())

@number("bsub_file_psm_max", "Most q < 0.01 PSMs in one of those files", ["v2_data", "v2_intermediate"], ["a_converted.py"])
def _():
    return int(_dropped_files().filter(pl.col("species") == "Bacillus-subtilis")["q<0.01"].max())

@number("mouse_dropped_files", "Mouse MGFs with nothing in main", ["v2_data"], ["a_converted.py"])
def _():
    return _dropped_files().filter(pl.col("species") == "Mus-musculus").height

@number("raw_files_converted", "Raw files in v2's conversion", ["v2_data"], ["datazip.py"])
def _():
    return len(glob.glob(f"{T}/datazip__*.parquet"))

@number("raw_files_main", "Raw files with a spectrum in v2 main", ["v2_main"], ["a_flow.py"])
def _():
    return registry().filter(pl.col("lab_zenodo_main").is_not_null()).select("species", "raw_file").n_unique()

@number("after_filters", "q < 0.01 scans less the charge-less ones and the four mouse files", ["v2_data", "v2_intermediate"], ["a_converted.py"])
def _():
    s = csv("converted__summary").filter(pl.col("species") == "ALL")
    mouse = _dropped_files().filter(pl.col("species") == "Mus-musculus")["q<0.01"].sum()
    return int(s["q<0.01"][0] - s["q<0.01, no charge"][0] - mouse)

@number("shared_draw_removed", "Spectra the shared-peptide draw removes on the way to v2 main", ["v2_main", "v2_data"], ["a_converted.py"])
def _():
    return REG["after_filters"]["f"]() - release("zenodo_main", ["species"]).collect().height

# ---- what changed in the spectra --------------------------------------------------------------------

@functools.cache
def _v1_vs_2022():
    c = ["species", "raw_file", "scan", "pepmass", "charge"]
    j = original("orig_species", c).collect().join(release("r2022", c).collect(), on=KEY, suffix="_r")
    return j.with_columns(((pl.col("pepmass_r") - PROTON) * pl.col("charge_r")
                           - (pl.col("pepmass") - PROTON) * pl.col("charge")).round(0).alias("dn"))

@number("v1_2022_shared", "Spectra v1 shares with MassIVE 2022", ["v1", "massive_2022"], ["make_jobs.py", "extract.py"])
def _():
    return _v1_vs_2022().height

@number("v1_2022_plus1", "Spectra v1 shares with MassIVE 2022 whose 2022 neutral precursor mass is 1 Da heavier", ["v1", "massive_2022"], ["make_jobs.py"])
def _():
    return int((_v1_vs_2022()["dn"] == 1).sum())

@number("v1_2022_plus2", "Spectra v1 shares with MassIVE 2022 whose 2022 neutral precursor mass is 2 Da heavier", ["v1", "massive_2022"], ["make_jobs.py"])
def _():
    return int((_v1_vs_2022()["dn"] == 2).sum())

@number("v1_2022_charge_changed", "Spectra v1 shares with MassIVE 2022 whose charge differs between the two", ["v1", "massive_2022"], ["make_jobs.py"])
def _():
    j = _v1_vs_2022()
    return int((j["charge"] != j["charge_r"]).sum())

def _iso(version):
    return csv("isotope__offsets").filter(pl.col("version") == version)

for _id, _v in (("iso_main", "Zenodo main"), ("iso_2022", "MassIVE r2022"), ("iso_2023", "MassIVE r2023"),
                ("iso_2024", "MassIVE r2024"), ("iso_balanced", "Zenodo balanced = ProteoBench"), ("iso_dion", "dIon"),
                ("iso_v1", "original (species files)"), ("iso_instanovo", "InstaNovo split")):
    def _f(v=_v):
        return int(_iso(v)["offset +1"][0])
    def _p(v=_v):
        return float(_iso(v)["non-monoisotopic %"][0])
    number(_id, f"{_v}: spectra whose precursor is the label's M+1 peak", ["v2_main"], ["a_isotope.py"])(_f)
    number(_id + "_pct", f"{_v}: that, as a share", ["v2_main"], ["a_isotope.py"], fmt=".1f", suffix="%")(_p)

for _id, _sp in (("iso_vmungo_pct", "Vigna-mungo"), ("iso_yeast_pct", "Saccharomyces-cerevisiae")):
    def _f(sp=_sp):
        return float(row(csv("isotope__non_mono_by_species"), "Zenodo main", species=sp))
    number(_id, f"v2 main, {_sp}: share of precursors on the M+1 peak", ["v2_main"], ["a_isotope.py"], fmt=".1f", suffix="%")(_f)

@functools.cache
def _offset_spectra():
    m = table("zenodo_main", ("species", "pepmass", "charge", "seq_raw"))
    u = m.select("seq_norm").unique().with_columns(pl.col("seq_norm").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm"))
    m = m.join(u, on="seq_norm")
    n = (pl.col("pepmass") - PROTON) * pl.col("charge")
    m = m.with_columns(((n - pl.col("pm")) / pl.col("pm") * 1e6).alias("e0"),
                       ((n - pl.col("pm") - C13) / pl.col("pm") * 1e6).alias("e1"),
                       ((n - pl.col("pm") - 0.984016) / pl.col("pm") * 1e6).alias("edeam"))
    return m.filter(pl.col("e1").abs() < pl.col("e0").abs())

@number("iso_c13_median_ppm", "v2 main M+1 spectra: median error assuming a carbon-13 offset", ["v2_main"], ["make_jobs_zip.py"], fmt=".1f", suffix=" ppm")
def _():
    return float(_offset_spectra()["e1"].median())

@number("iso_deam_median_ppm", "v2 main M+1 spectra: median error assuming a missed deamidation instead", ["v2_main"], ["make_jobs_zip.py"], fmt=".1f", suffix=" ppm")
def _():
    return float(_offset_spectra()["edeam"].median())

@number("iso_no_nq", "v2 main M+1 spectra whose peptide has no N or Q to deamidate", ["v2_main"], ["make_jobs_zip.py"])
def _():
    return int((~_offset_spectra()["seq_bare"].str.contains("[NQ]")).sum())

def _profile(version, col):
    return row(csv("profile__versions"), col, version=version)

@number("peaks_v1_median", "Median peaks per spectrum, v1", ["v1"], ["a_profile.py"], fmt=".0f")
def _():
    return float(_profile("original (species files)", "peaks median"))

@number("peaks_v2_median", "Median peaks per spectrum, v2 main", ["v2_main"], ["a_profile.py"], fmt=".0f")
def _():
    return float(_profile("Zenodo main", "peaks median"))

@number("frag_v1_max", "Largest fragment m/z, v1", ["v1"], ["a_profile.py"], fmt=",.0f")
def _():
    return float(_profile("original (species files)", "fragment m/z max"))

@number("frag_v2_max", "Largest fragment m/z, v2 main", ["v2_main"], ["a_profile.py"], fmt=",.0f")
def _():
    return float(_profile("Zenodo main", "fragment m/z max"))

@number("v1_charge1_pct", "Share of v1 spectra at charge 1", ["v1"], ["a_profile.py"], fmt=".2f", suffix="%")
def _():
    return float(_profile("original (species files)", "z=1 %"))

@number("nterm_main_pct", "Share of v2 main spectra with an N-terminal modification", ["v2_main"], ["make_jobs_zip.py"], fmt=".1f", suffix="%")
def _():
    m = table("zenodo_main", ("species", "seq_raw"))
    return 100 * m["seq_norm"].str.contains(r"^\[[^\]]*\]-").sum() / m.height

@functools.cache
def _peaks():
    return pl.read_parquet(os.path.expanduser("~/data/ninespecies/peaksample/peak_compare_mouse.parquet"))

@number("mouse_peak_spectra", "Mouse spectra compared peak by peak (v1 against MassIVE 2024)", ["v1", "massive_2024"], ["a_peaks.py"])
def _():
    return _peaks().height

@number("mouse_peaks_matched_pct", "Median share of v1's peaks found in the revised peak list within 10 ppm", ["v1", "massive_2024"],
        ["a_peaks.py"], fmt=".1f", suffix="%")
def _():
    return 100 * float(_peaks()["orig_peaks_in_revised"].median())

@number("mouse_intensity_matched_pct", "Median share of v1's intensity carried by the peaks found in the revised peak list", ["v1", "massive_2024"], ["a_peaks.py"],
        fmt=".1f", suffix="%")
def _():
    return 100 * float(_peaks()["orig_intensity_in_revised"].median())

@number("mouse_intensity_ratio", "Median ratio of v1's to the revised median intensity", ["v1", "massive_2024"], ["a_peaks.py"], fmt=".1f")
def _():
    return float(_peaks()["intensity_ratio_median"].median())

# ---- between the revised builds ---------------------------------------------------------------------

for _a, _b in (("r2022", "r2023"), ("r2023", "r2024")):
    def _out(a=_a, b=_b):
        return int(row(csv(f"{a}_vs_{b}__presence"), "only_a", species="ALL"))
    def _in(a=_a, b=_b):
        return int(row(csv(f"{a}_vs_{b}__presence"), "only_b", species="ALL"))
    def _lab(a=_a, b=_b):
        r = csv(f"{a}_vs_{b}__labels").filter(pl.col("species") == "ALL")
        return int(r["I/L only"][0] + r["modifications differ"][0] + r["different peptide"][0])
    number(f"{_a}_{_b}_out", f"Spectra in {_a}, not {_b}", ["massive_2022", "massive_2023", "massive_2024"], ["a_versions.py"])(_out)
    number(f"{_a}_{_b}_in", f"Spectra in {_b}, not {_a}", ["massive_2022", "massive_2023", "massive_2024"], ["a_versions.py"])(_in)
    number(f"{_a}_{_b}_relabelled", f"Spectra in both whose label changed", ["massive_2022", "massive_2023", "massive_2024"], ["a_versions.py"])(_lab)

for _id, _t in (("r2024_main_out", "in r2024, not zenodo_main"), ("r2024_main_in", "in zenodo_main, not r2024")):
    def _f(t=_t):
        return int(row(csv("swing__transitions"), "spectra", transition=t))
    number(_id, f"Spectra {_t}", ["massive_2024", "v2_main"], ["a_swing.py"])(_f)

@number("r2024_main_diff", "Spectra in exactly one of MassIVE 2024 and v2 main", ["massive_2024", "v2_main"], ["a_swing.py"])
def _():
    return REG["r2024_main_out"]["f"]() + REG["r2024_main_in"]["f"]()

for _id, _t, _c in (("swing_23_24_shared", "in r2023, not r2024", "peptide shared between species (q<0.01)"),
                    ("swing_main_shared", "in zenodo_main, not r2024", "peptide shared between species (q<0.01)")):
    def _f(t=_t, c=_c):
        return int(row(csv("swing__transitions"), c, transition=t))
    number(_id, f"Spectra {_t} whose peptide Percolator found in more than one species", ["v2_intermediate"], ["a_swing.py"])(_f)

for _id, _t in (("swing_22_23_out_same_pct", "in r2022, not r2023"), ("swing_22_23_in_same_pct", "in r2023, not r2022")):
    def _f(t=_t):
        r = csv("swing__transitions").filter(pl.col("transition") == t)
        return 100 * r["peptide present in other build, same species"][0] / r["spectra"][0]
    number(_id, f"Spectra {_t} whose peptide the other build keeps in the same species", ["massive_2022", "massive_2023"],
           ["a_swing.py"], fmt=".0f", suffix="%")(_f)

@functools.cache
def _relabel_22_23():
    tok = re.compile(r"\[[^\]]*\]-|[A-Z]\[[^\]]*\]")
    a, b = table("r2022"), table("r2023")
    j = a.join(b.select(KEY + [pl.col("seq_norm").alias("s2"), pl.col("seq_bare").alias("b2")]), on=KEY)
    j = j.filter(pl.col("seq_norm") != pl.col("s2"))
    def kind(x, y):
        from collections import Counter
        cx, cy = Counter(tok.findall(x)), Counter(tok.findall(y))
        lost, gained = cx - cy, cy - cx
        if not lost and not gained: return "same set"
        if any(t.endswith("]-") for t in gained.elements()): return "N-terminal gained"
        return "deamidation"
    same_bb = j.filter(il(pl.col("seq_bare")) == il(pl.col("b2")))
    kinds = [kind(x, y) for x, y in same_bb.select("seq_norm", "s2").iter_rows()]
    from collections import Counter
    c = Counter(kinds); c["different peptide"] = j.height - same_bb.height
    return c

for _id, _k in (("relabel_deamidation", "deamidation"), ("relabel_nterm", "N-terminal gained"),
                ("relabel_same_set", "same set"), ("relabel_peptide", "different peptide")):
    def _f(k=_k):
        return int(_relabel_22_23()[k])
    number(_id, f"2022 to 2023 relabellings: {_k}", ["massive_2022", "massive_2023"], ["make_jobs.py", "extract.py"])(_f)

for _b in ("r2022", "r2023", "r2024", "zenodo_main"):
    def _f(b=_b):
        d = table(b, ("species", "seq_raw")).with_columns(il(pl.col("seq_bare")).alias("p"))
        return d.group_by("p").agg(pl.col("species").n_unique().alias("n")).filter(pl.col("n") > 1).height
    number(f"multi_species_{_b}", f"Backbones in more than one species file, {_b}", ["v2_main"], ["make_jobs.py", "make_jobs_zip.py"])(_f)

# ---- denovo_benchmarks ------------------------------------------------------------------------------

def _dnb_counts(sp, col):
    return int(row(csv("dnb__counts"), col, species=sp))

for _id, _sp, _col in (("dnb_mouse_files", "Mus-musculus", "raw files"), ("dnb_bsub_files", "Bacillus-subtilis", "raw files"),
                       ("dnb_bsub_converted", "Bacillus-subtilis", "raw files converted"), ("dnb_yeast_files", "Saccharomyces-cerevisiae", "raw files"),
                       ("dnb_yeast_converted", "Saccharomyces-cerevisiae", "raw files converted")):
    def _f(sp=_sp, col=_col):
        return _dnb_counts(sp, col)
    number(_id, f"denovo_benchmarks, {_sp}: {_col}", ["dnb", "v2_data"], ["a_dnb.py"])(_f)

@number("dnb_keyed", "denovo_benchmarks labels whose chunk index resolves to a deposited spectrum", ["dnb"], ["make_jobs_dnb.py", "a_dnb.py"])
def _():
    return pl.read_parquet(f"{OUT}/dnb_keyed.parquet", columns=["scan"]).height

def _dnb_vs(col):
    return int(row(csv("dnb__vs_versions"), col, version="zenodo_main"))

@number("dnb_in_main", "denovo_benchmarks spectra that v2 main also holds", ["dnb", "v2_main"], ["a_dnb.py"])
def _():
    return sum(_dnb_vs(c) for c in ("identical", "I/L only", "modifications differ", "different peptide"))

@number("dnb_same_label_pct", "Share of the spectra denovo_benchmarks shares with v2 main that carry the same label (I=L)", ["dnb", "v2_main"], ["a_dnb.py"], fmt=".1f", suffix="%")
def _():
    return 100 * (_dnb_vs("identical") + _dnb_vs("I/L only")) / REG["dnb_in_main"]["f"]()

@number("dnb_not_in_main", "denovo_benchmarks spectra v2 main does not hold", ["dnb", "v2_main"], ["a_dnb.py"])
def _():
    return _dnb_vs("absent")

@number("dnb_rare_mods", "denovo_benchmarks labels with a modification the revised search did not allow", ["dnb"], ["a_dnb.py"])
def _():
    j = pl.read_parquet(f"{OUT}/dnb_vs_registry.parquet", columns=["lab_dnb"])
    return int(j["lab_dnb"].str.contains(r"[DEHKRC]\[\+(?:57.02|21.98|14.02|39.99)\]|\[\+(?:57.02|26.02|12.00)\]-").sum())

def _dnb_iso(kind):
    d = csv("dnb__main_isotope_spectra").filter(pl.col("main_offset") == 1.0)
    return int(d.filter(pl.col("vs_zenodo_main") == kind)["len"].sum()) if kind else int(d["len"].sum())

@number("dnb_iso_spectra", "Spectra v2 main labels at the M+1 peak that denovo_benchmarks also labels", ["dnb", "v2_main"], ["a_dnb.py"])
def _():
    return _dnb_iso(None)

@number("dnb_iso_deamidation", "Of those, spectra denovo_benchmarks labels as the same backbone with one more deamidation", ["dnb", "v2_main"], ["a_dnb.py"])
def _():
    return _dnb_iso("modifications differ")

@functools.cache
def _dnb_deam():
    j = pl.read_parquet(f"{OUT}/dnb_vs_registry.parquet").filter(pl.col("lab_zenodo_main").is_not_null())
    d = pl.read_parquet(f"{OUT}/dnb_keyed.parquet").select(KEY + ["pepmass", "charge"])
    j = j.join(d, on=KEY)
    for c in ("lab_dnb", "lab_zenodo_main"):
        u = j.select(pl.col(c).alias("l")).unique().with_columns(pl.col("l").map_elements(peptide_mass, return_dtype=pl.Float64).alias("pm_" + c))
        j = j.join(u, left_on=c, right_on="l")
    n = (pl.col("pepmass") - PROTON) * pl.col("charge")
    j = j.with_columns((n - pl.col("pm_lab_zenodo_main")).round(0).alias("off"),
                       ((n - pl.col("pm_lab_dnb")) / pl.col("pm_lab_dnb") * 1e6).alias("e_dnb"),
                       ((n - pl.col("pm_lab_zenodo_main") - C13) / pl.col("pm_lab_zenodo_main") * 1e6).alias("e_c13"))
    return j.filter((pl.col("off") == 1) & (pl.col("vs_zenodo_main") == "modifications differ"))

@number("dnb_c13_better", "Of those, spectra where v2 main's carbon-13 reading fits the precursor better", ["dnb", "v2_main"], ["a_dnb.py"])
def _():
    x = _dnb_deam()
    return int((x["e_c13"].abs() < x["e_dnb"].abs()).sum())

@number("dnb_c13_ppm", "Of those, the median precursor error of v2 main's carbon-13 reading", ["dnb", "v2_main"], ["a_dnb.py"], fmt=".1f", suffix=" ppm")
def _():
    return float(_dnb_deam()["e_c13"].median())

@number("dnb_deam_ppm", "Of those, the median precursor error of denovo_benchmarks' deamidation reading", ["dnb", "v2_main"], ["a_dnb.py"], fmt=".1f", suffix=" ppm")
def _():
    return float(_dnb_deam()["e_dnb"].median())

@functools.cache
def _dnb_unresolved():
    lab = pl.concat([pl.read_csv(f) for f in glob.glob(f"{T}/../raw/dnb_labels/9_species_*_labels.csv")]).with_columns(
        pl.col("spectrum_id").str.extract(r"^(.*)_(\d+):\d+$", 1).alias("raw_file"),
        pl.col("spectrum_id").str.extract(r"_(\d+):\d+$", 1).alias("fold"),
        pl.col("spectrum_id").str.extract(r":(\d+)$", 1).cast(pl.Int64).alias("row"))
    mg = pl.scan_parquet(f"{T}/dnb__*.parquet").select("raw_file", "fold", "row").collect()
    miss = lab.join(mg, on=["raw_file", "fold", "row"], how="anti")
    return miss, mg

@number("dnb_unresolved", "denovo_benchmarks labels pointing past the end of their deposited MGF chunk", ["dnb"], ["make_jobs_dnb.py"])
def _():
    return _dnb_unresolved()[0].height

@number("dnb_bc2_1_spectra", "Spectra in the deposited chunk BC-2_1.mgf", ["dnb"], ["make_jobs_dnb.py"])
def _():
    mg = _dnb_unresolved()[1]
    return mg.filter((pl.col("raw_file") == "BC-2") & (pl.col("fold") == "1")).height

@number("dnb_bc2_1_max_index", "Highest index the labels cite in BC-2_1", ["dnb"], ["download_sources.py"])
def _():
    miss = _dnb_unresolved()[0]
    return int(miss.filter(pl.col("spectrum_id").str.starts_with("BC-2_1:"))["row"].max())

# ---- splits and leakage -----------------------------------------------------------------------------

@functools.cache
def _fold_backbone():
    f = original("orig_fold", ["species", "split", "fold", "seq_raw"]).filter(pl.col("split").is_in(["train", "valid", "test"])).collect().pipe(renorm)
    out = []
    for fold in f["fold"].unique():
        g = f.filter(pl.col("fold") == fold)
        tr = g.filter(pl.col("split") == "train")["seq_bare"].unique().implode()
        for s in ("valid", "test"):
            e = g.filter(pl.col("split") == s)
            out.append(100 * e["seq_bare"].is_in(tr).sum() / e.height)
    return out

@number("fold_backbone_min", "Lowest share of a v1 fold's valid or test spectra whose backbone the fold trains on", ["v1_folds"],
        ["make_jobs.py"], fmt=".0f", suffix="%")
def _():
    return min(_fold_backbone())

@number("fold_backbone_max", "Highest such share", ["v1_folds"], ["make_jobs.py"], fmt=".0f", suffix="%")
def _():
    return max(_fold_backbone())

for _id, _h in (("heldout_min_pct", "M. mazei"), ("heldout_max_pct", "V. mungo")):
    def _f(h=_h):
        return float(row(csv("leak__original_folds"), "peptide trained on %", **{"held-out species": h}))
    number(_id, f"{_h} spectra whose backbone the fold holding it out trained on", ["v1", "v1_folds"], ["a_leak.py"],
           fmt=".1f", suffix="%")(_f)

for _id, _e in (("leak_pb_pct", "Zenodo balanced = ProteoBench"), ("leak_dion_pct", "dIon test"),
                ("leak_dnb_pct", "denovo_benchmarks selection"), ("leak_main_pct", "Zenodo main, all species")):
    def _f(e=_e):
        return float(row(csv("leak__cross_version"), "same spectrum %", **{"evaluate on": e}))
    def _b(e=_e):
        return float(row(csv("leak__cross_version"), "peptide seen %", **{"evaluate on": e}))
    number(_id, f"{_e}: share of spectra that are InstaNovo/NovoBench training scans", ["instanovo_split"], ["a_leak.py"], fmt=".1f", suffix="%")(_f)
    number(_id + "_backbone", f"{_e}: share whose backbone is in that training split", ["instanovo_split"], ["a_leak.py"], fmt=".1f", suffix="%")(_b)

for _id, _sp in (("leak_pb_mouse_pct", "Mus-musculus"), ("leak_pb_human_pct", "H.-sapiens")):
    def _f(sp=_sp):
        r = csv("leak__instanovo_train_to_proteobench_by_species").filter(pl.col("species") == sp)
        return 100 * r["same spectrum"][0] / r["spectra"][0]
    number(_id, f"ProteoBench, {_sp}: share of spectra that are InstaNovo/NovoBench training scans", ["proteobench", "instanovo_split"],
           ["a_leak.py"], fmt=".0f", suffix="%")(_f)

@number("balanced_files", "Raw files in v2 balanced", ["v2_balanced"], ["a_flow.py"])
def _():
    return registry().filter(pl.col("lab_zenodo_balanced").is_not_null()).select("species", "raw_file").n_unique()


# ---- driver -----------------------------------------------------------------------------------------

def compute(ids):
    out = {}
    for i in ids:
        r = REG[i]
        v = r["f"]()
        text = format(v, r["fmt"]) + r["suffix"]
        src = textwrap.dedent(inspect.getsource(r["f"]))
        # A function defined in a loop takes its case as default arguments; say which.
        bound = {k: p.default for k, p in inspect.signature(r["f"]).parameters.items()
                 if p.default is not inspect.Parameter.empty}
        if bound:
            src += "# called with " + ", ".join(f"{k}={v!r}" for k, v in bound.items()) + "\n"
        out[i] = dict(value=v, text=text, desc=r["desc"], made_by=r["made_by"], code=src,
                      sources=[dict(key=k, label=SOURCES[k][0], url=SOURCES[k][1]) for k in r["sources"]])
        print(f"{i:32s} {text:>14s}  {r['desc']}", flush=True)
    return out


if __name__ == "__main__":
    path = os.path.join(HERE, "page_numbers.json")
    if "--only" in sys.argv:
        compute(sys.argv[sys.argv.index("--only") + 1].split(",")); sys.exit(0)
    got = compute(list(REG))
    if "--check" in sys.argv:
        want = json.load(open(path))
        bad = [i for i in got if i not in want or want[i]["text"] != got[i]["text"]]
        print(f"{len(got) - len(bad)} of {len(got)} numbers agree with page_numbers.json")
        for i in bad:
            print(f"  DIFFERS {i}: {want.get(i, {}).get('text')} -> {got[i]['text']}")
        sys.exit(1 if bad else 0)
    json.dump(got, open(path, "w"), indent=1, ensure_ascii=False, default=float)
    print(f"wrote {len(got)} numbers to {path}")

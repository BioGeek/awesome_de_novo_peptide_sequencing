"""Shared loading for the nine-species version analysis.

Every version becomes a polars frame with the same columns plus a spectrum key
(species, raw_file, scan). Versions that do not carry the raw file are joined to
one that does, on fields that are unique within a species.
"""
import glob, os, re, polars as pl
B = os.path.expanduser("~/data/ninespecies"); T = f"{B}/tables"; OUT = f"{B}/results"
PXD = {"PXD003868": "Saccharomyces-cerevisiae", "PXD004565": "Bacillus-subtilis",
       "PXD004536": "Candidatus-endoloripes", "PXD004467": "Apis-mellifera",
       "PXD004424": "H.-sapiens", "PXD004325": "Methanosarcina-mazei",
       "PXD004948": "Mus-musculus", "PXD005025": "Vigna-mungo", "PXD004947": "Solanum-lycopersicum"}
SHORT = {"Saccharomyces-cerevisiae": "yeast", "Bacillus-subtilis": "B. subtilis", "Candidatus-endoloripes": "C. endoloripes",
         "Apis-mellifera": "honeybee", "H.-sapiens": "human", "Methanosarcina-mazei": "M. mazei",
         "Mus-musculus": "mouse", "Vigna-mungo": "V. mungo", "Solanum-lycopersicum": "tomato"}
COLS = ["source", "species", "raw_file", "scan", "split", "fold", "row", "pepmass_str", "pepmass", "charge",
        "rt", "seq_raw", "seq_norm", "seq_bare", "n_peaks", "tic", "top10_mz", "title", "extra"]

def _scan(pattern, cols=COLS):
    files = sorted(f for f in glob.glob(f"{T}/{pattern}") if f.endswith(".parquet"))
    if not files: return None
    return pl.scan_parquet(files).select(cols)

def original(kind, cols=COLS):
    """kind: 'orig_species', 'orig_species10k' or 'orig_fold'. Species re-derived from the PXD in TITLE,
    because two PEAKS projects (M. mazei and C. endoloripes) were both called 'bacteria'."""
    lf = _scan(f"{kind}__*.parquet", list(dict.fromkeys(cols + ["title"])))
    sp = pl.col("title").str.extract(r"(PXD\d+)").replace_strict(PXD, default=None)
    return lf.with_columns(sp.alias("species"))

def release(name, cols=COLS):
    return _scan(f"{name}__*.parquet", cols)

def il(expr):  # I and L are isobaric: compare labels with them folded
    return expr.str.replace_all("I", "L")

KEY = ["species", "raw_file", "scan"]

# ---- does the label fit the spectrum? ------------------------------------------
AA = {"G": 57.021464, "A": 71.037114, "S": 87.032028, "P": 97.052764, "V": 99.068414, "T": 101.047679,
      "C": 103.009185, "L": 113.084064, "I": 113.084064, "N": 114.042927, "D": 115.026943, "Q": 128.058578,
      "K": 128.094963, "E": 129.042593, "M": 131.040485, "H": 137.058912, "F": 147.068414, "R": 156.101111,
      "Y": 163.06332, "W": 186.079313}
H2O, PROTON, C13 = 18.010565, 1.007276, 1.003355
_tok = re.compile(r"\[([+-]\d+\.\d+)\]-|([A-Z])(?:\[([+-]\d+\.\d+)\])?")

def peptide_mass(seq_norm):
    """Monoisotopic mass of a normalised label. Cysteine is carbamidomethylated in every version, so a
    bare C (Tide and DeepNovo omit the static mod) counts +57.021; mods are read at the 2-decimal
    precision the normaliser keeps, so masses carry up to ~0.01 Da of rounding."""
    if not seq_norm: return None
    m = H2O
    for nterm, aa, mod in _tok.findall(seq_norm):
        if nterm: m += float(nterm); continue
        if aa not in AA: return None
        m += AA[aa]
        if mod: m += float(mod)
        elif aa == "C": m += 57.021464
    return m

def mass_error_expr():
    """Polars expression: smallest |error| in ppm over isotope offsets -1..2, between precursor and label."""
    pm = pl.col("seq_norm").map_elements(peptide_mass, return_dtype=pl.Float64)
    neutral = (pl.col("pepmass") - PROTON) * pl.col("charge")
    errs = [((neutral - pm - k * C13) / pm * 1e6).abs() for k in (-1, 0, 1, 2)]
    return pl.min_horizontal(errs).alias("mass_err_ppm")

def renorm(df):
    """Recompute seq_norm / seq_bare from seq_raw with the current normaliser, once per distinct label,
    so tables extracted before a normaliser fix still compare correctly."""
    from extract import norm_seq
    if "seq_raw" not in df.columns: return df
    u = df.select("seq_raw").unique().drop_nulls()
    pairs = [norm_seq(s) for s in u["seq_raw"].to_list()]
    m = u.with_columns(pl.Series("seq_norm", [p[0] for p in pairs], pl.String), pl.Series("seq_bare", [p[1] for p in pairs], pl.String))
    return df.drop([c for c in ("seq_norm", "seq_bare") if c in df.columns]).join(m, on="seq_raw", how="left")

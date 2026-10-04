#!/usr/bin/env python3
"""Mine the comparison tables out of the local paper PDFs, and report what they say.

Every method paper in this field carries a table setting itself against its
chosen baselines. The catalog records what a method IS, and it records two
independently-run benchmarks, but nothing about what the literature CLAIMS
about itself. This script reads those tables.

READ 'The retrained-versus-released trap' IN BENCHMARKS.md FIRST. A number a
paper reports for somebody else's method is not comparable with a number
another paper reports for the same method: the two groups used different
dataset versions and, more importantly, one may have retrained the baseline
while the other ran its released weights. Measured over this library, the
basis is UNSTATED in most papers. So this script records a claim together with
the evidence for its basis, and refuses to assert a comparison.

PHASE 0: this script is REPORT-ONLY and writes NOTHING but the audit CSV. The
`paper_comparison` tables do not exist yet; landing them is a decision to take
after reading the rejection tally this produces, which is the point of doing it
in this order.

    uv run --with pdfplumber python3 build_paper_comparisons.py
    uv run --with pdfplumber python3 build_paper_comparisons.py --ids 38,21
    uv run --with pdfplumber python3 build_paper_comparisons.py --ids 38 --show

It needs `pdfplumber`, which is deliberately NOT a project dependency, for the
same reason `pypdf` is not: CI would install it for a script CI never runs.
It imports `build_pdf_library` for `rapidfuzz`, so bare `python3` will not work.

**It must never run in CI.** There is no PDF library on a runner.

HOW THE TABLE IS FOUND, and the two things that do not work:

- **Caption wording is the wrong locator.** Measured over the 116 algorithm
  papers with a PDF: a caption regex requiring comparison vocabulary plus a
  metric word finds 15 papers. Locating candidate PAGES by content, a page
  naming three or more known methods alongside at least six decimals, finds
  77. Captions come in three house styles (`Table 1:`, `Table 1.` and Nature's
  `Table 1 |`) and frequently do not describe themselves as comparisons.
- **`pdfplumber.extract_tables()` shreds these tables.** With the text
  strategy on a full page, a full-width table on a two-column page picks up
  column gaps from the surrounding prose, and ContraNovo's Table 1 comes back
  with cells like `'Species Peaks. Deep. Point. Casa. C'`. `extract_words()`
  clustered on `top` reconstructs the same table exactly, with no tuning. So
  the primitive here is word geometry, not Pdfplumber's table finder.

EVERY GUARD REJECTS THE WHOLE TABLE, never part of it. Partial acceptance is
the dangerous failure: silently dropping one column that would not resolve
changes WHICH baselines the paper chose, and that is the single fact this
exercise exists to record. The same reasoning as `build_pdf_abstracts.py`,
which rejects 13 of 18 candidates on purpose: a plausible-looking fragment in
the database is worse than no row at all.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import csv
import pathlib
import re
import sqlite3
import statistics
import sys

import build_pdf_library as bpl

HERE = pathlib.Path(__file__).parent
DB = HERE / "denovo.db"
LIBRARY = pathlib.Path.home() / "Documents" / "De novo peptide sequencing"
AUDIT = HERE / "paper_comparison_audit.csv"


# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #

# A numeric cell. Covers '0.739', '.739', '73.9', '73.9%', '1,234' and the
# '+/-' forms, which are split out into a stddev rather than rejected.
NUM = re.compile(r"^[(\[]?[<>~]?\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d*\.\d+|\d+\.?))\s*%?[)\]]?[*+^†‡¹²³]?$")
PM = re.compile(r"^([+-]?(?:\d*\.\d+|\d+\.?))\s*(?:±|\+/-)\s*(\d*\.\d+|\d+\.?)\s*%?$")
# A cell holding several numbers. By DEFAULT this rejects the table, because
# two numbers in one cell with nothing to tell them apart is a different grain
# and guessing which is the measurement is the error every guard here avoids.
# Where the paper's own footnotes say what each one is, CELL_MARKERS records
# that and all of them are kept.
MULTI = re.compile(r"\d\s*/\s*\d|\d\s*\|\s*\d")
# One number plus an optional footnote marker: '0.530*', '0.664+'.
PART = re.compile(r"^([<>~]?\s*(?:\d*\.\d+|\d+\.?))\s*%?\s*([*+\u2020\u2021\u00a7\u00b6]?)$")


def compound(tok: str) -> list[tuple[float, str]] | None:
    """Split '0.550/0.530*/0.664+' into [(0.550,''), (0.530,'*'), (0.664,'+')].

    Returns None when the token is not a slash-joined run of numbers, so an
    ordinary cell is untouched.
    """
    bits = [b.strip() for b in re.split(r"\s*/\s*", tok.strip()) if b.strip()]
    if len(bits) < 2:
        return None
    out = []
    for b in bits:
        m = PART.match(b)
        if not m:
            return None
        try:
            out.append((float(m.group(1).replace(" ", "")), m.group(2)))
        except ValueError:
            return None
    return out


# A MARKER ON A METHOD'S OWN LABEL, from the paper's legend. Where CELL_MARKERS
# says what a footnote on a NUMBER means, this says what a footnote on a METHOD
# means, which is most often that its scores were not run by this paper at all.
METHOD_MARKERS: dict[tuple[int, str], dict[str, dict]] = {
    # RefineNovo, Table 6. Its legend: "Scores for models marked with * are
    # quoted from the NovoBench paper or original publications." So every
    # starred row is somebody else's measurement, which is precisely what the
    # 'quoted' basis records -- and what BENCHMARKS.md warns against reading
    # beside a number this paper ran itself. Cross-checks: its Casanovo* reads
    # 0.48 and BENCHMARKS.md records NovoBench's retrained Casanovo at 0.481.
    (16, "Table6"): {"*": {"basis": "quoted"}},
    # LIPNovo, Tables 1 and 2. Its legend: "dagger denotes our retrained
    # results, and other results are provided by NovoBench." So the UNMARKED
    # rows are quoted and the marked ones are this paper's own retraining,
    # which is why the empty-marker key is present: it is a statement about
    # every row that carries no dagger.
    (17, "Table1"): {"": {"basis": "quoted"},
                     "\u2020": {"basis": "retrained"}},
    (17, "Table2"): {"": {"basis": "quoted"},
                     "\u2020": {"basis": "retrained"}},
    # CausalNovo, Tables 1 and 2: "dagger denotes our retrained results, and
    # others are provided by NovoBench" -- LIPNovo's legend, word for word.
    # Table 3 says only "dagger denotes our retrained results", so its
    # unmarked rows are left to the prose rather than called quoted.
    (64, "Table1"): {"": {"basis": "quoted"},
                     "\u2020": {"basis": "retrained"}},
    (64, "Table2"): {"": {"basis": "quoted"},
                     "\u2020": {"basis": "retrained"}},
    (64, "Table3"): {"\u2020": {"basis": "retrained"}},
    # LIPNovo, Table 3, the leave-one-out table: its caption says "dagger
    # means our re-trained results", and its only daggered row is Baseline,
    # which is Casanovo.
    (17, "Table3"): {"\u2020": {"basis": "retrained"}},
    # MemNovo, Table 2: "Results marked with dagger are reported from original
    # publications". The unmarked rows are its own evaluations, and how those
    # were run is left to its prose.
    (202, "Table2"): {"\u2020": {"basis": "quoted"}},
}


# WHAT EACH FOOTNOTE MARKER MEANS, per table, from the paper's own legend.
# Keyed by (publication, table label). The empty marker is the plain number and
# inherits the column's metric; a marked one may override the metric, the level
# or the basis. A cell with several numbers and NO entry here is still refused.
CELL_MARKERS: dict[tuple[int, str], dict[str, dict]] = {
    # DiffNovo, Table 1. Its legend: "* Indicates the positional accuracy
    # reported in [10]", where [10] is PepNet (Liu et al., Nat Commun 14:7974,
    # 2023). So the starred number is a different metric AND somebody else's
    # measurement, which is what basis 'quoted' records.
    (13, "Table 1"): {
        "*": {"metric": "positional-accuracy", "basis": "quoted"},
    },
    # DiffNovo, Table 2. Its legend: "+ Indicates the filtered peptide-level
    # accuracy, and * indicates the peptide-level accuracy reported in [10]".
    # Three measurements in one cell: the column's own precision, an accuracy
    # quoted from PepNet's paper, and a filtered accuracy.
    (13, "Table 2"): {
        "*": {"metric": "accuracy", "basis": "quoted"},
        "+": {"metric": "accuracy-filtered"},
    },
}
# A publisher watermark, stamped across the page and landing inside the table's
# row band. pNovo 3's page carries
# 'https://academic.oup.com/bioinformatics/article/35/14/i183/5529238' between
# two data rows, and its slashes-between-digits read as a multi-valued cell,
# which rejected the whole table. A URL is not a measurement in any layout, so
# it is dropped before the cell guards see it rather than being allowed to fail
# them.
URLISH = re.compile(r"(?i)^(?:https?://|www\.|doi:|10\.\d{4,}/)\S*$")
# A row of raw counts is not a row of measurements. pNovo 3's '#TotalPSMs' row
# sits under seven percentages, and its 62,089 against their 64.6 tripped the
# one-unit guard. Matched on the LABEL, and deliberately not on 'Total' alone,
# which is a legitimate aggregate label elsewhere.
# ...and 'number' glued onto the end of a label: BiATNovo's 'Peptide recall
# number' arrives as 'Peptiderecallnumber' and holds 31153, a count of
# peptides, between rows of percentages.
COUNT_ROW = re.compile(r"(?i)^\s*#|\b(psms?|spectra|counts?|size|num\.?)\b|number\b")
# A ROW OF A METRIC OUTSIDE THE CLOSED VOCABULARY, dropped and counted like
# a count row. BiATNovo's preprint follows its precision and recall rows with
# 'Position-BLEU' and 'Alignment score', which no catalog metric means; with
# them in, no metric could be read down the rows and the table was refused.
UNRECORDED_METRIC_ROW = re.compile(
    r"(?i)\bbleu\b|alignment\s*score"
    # ...and a row of SPREAD, which is not a measurement: InstaNovo's
    # results table closes on 'mean' (kept, an aggregate) and 'std' (not).
    r"|^\s*(?:std\.?|s\.?d\.?|standard\s*deviation)\s*$")
NOT_RUN = {"-", "–", "—", "n/a", "na", "--", "nan", "none", "x"}

CAPTION = re.compile(r"(?i)\b(Tab(?:le|\.)\s*(?:S?\d{1,2}|[IVX]{1,4}))\s*(?:[.:]|\||–|—)?\s*(.{0,300})", re.S)

# C2: an ablation compares variants of ONE method. Accepting one would mint a
# comparator row for a method called 'w/o contrastive loss'. ContraNovo's
# Table 3, 'Impact of components on performance', is the worked example.
C2_VETO = re.compile(r"(?i)ablation|impact of (?:the )?(?:component|module|each|different)|"
                     r"effect of (?:the )?(?:component|module|each)|\bw/o\b|without the|"
                     r"variants? of|contribution of (?:each|the)|sensitivity to|hyper-?parameter")
# C3: a runtime or size table has no metric in the vocabulary, and a speed
# comparison dressed as an accuracy one is worse than nothing.
C3_VETO = re.compile(r"(?i)inference time|running time|runtime|throughput|GPU[- ]hours?|"
                     r"\bFLOPs?\b|memory (?:usage|footprint)|model size|#?\s?parameters\b|"
                     r"number of parameters|speed[- ]?up|wall[- ]clock|"
                     # A protein-assembly table is a comparison, and not of the
                     # quantity this catalog records. PowerNovo's Table 2 scores
                     # three tools on mapped contigs, longest contig and
                     # sequence coverage of an antibody chain, with cells like
                     # '42 (19.44%)'. Those are assembly statistics, not
                     # peptide or amino-acid precision or recall, so the table
                     # is refused for what it is rather than for its geometry.
                     r"contigs?\b|protein sequence coverage|sequence coverage of|"
                     # OUT OF SCOPE BY WHAT THEY MEASURE, not by geometry. Each
                     # of these was being refused for a ragged grid or a
                     # missing header, which blamed the parser for a table this
                     # catalog does not record a quantity from, and left the
                     # real geometry failures buried in the tally.
                     #
                     # 'sensitivity' is NOT vetoed on its own: some papers use
                     # it for recall. Only sensitivity TO something, or a
                     # sensitivity analysis, which is a robustness study.
                     r"perplexity|"
                     r"sensitivity (?:analysis|to\b)|robustness (?:analysis|to\b)|"
                     r"perturbations?\b|"
                     r"(?:cosine|pearson|spearman)\b|spectral similarit|"
                     r"similarities on all")

# M1: metric headers, mapped to the closed vocabulary. Longest first.
METRIC_WORDS = [
    # Longest and most specific first. 'Prec. at Cov.=1' must be tested before
    # plain precision, or it reads as precision and the two become one
    # measurement; the pair are different numbers and ProteoBench's design
    # discussion turns on exactly that difference.
    (re.compile(r"(?i)prec\.?\s*(?:at|@)\s*cov\.?\s*=?\s*1|precision\s*(?:at|@)\s*"
                r"(?:full\s+)?cov(?:erage)?\.?\s*=?\s*1?"), "precision@cov1"),
    (re.compile(r"(?i)ptm[- ]?prec"), "ptm-precision"),
    (re.compile(r"(?i)ptm[- ]?rec"), "ptm-recall"),
    (re.compile(r"(?i)\bAUC\b|area under"), "auc"),
    (re.compile(r"(?i)\bAP\b|average precision"), "ap"),
    (re.compile(r"(?i)\bF1\b|f1[- ]score"), "f1"),
    (re.compile(r"(?i)precision|\bprec\b|\bprec\."), "precision"),
    (re.compile(r"(?i)recall|\brec\b|\brec\."), "recall"),
    # Footnoted variants of accuracy, printed as extra numbers inside a cell
    # (see CELL_MARKERS). 'positional' is PepNet's own metric; 'filtered' is
    # accuracy computed after a filtering step. Separate values because they
    # are separate measurements: collapsing them onto 'accuracy' would make
    # two numbers in one cell the same measurement, which G6 would then refuse.
    (re.compile(r"(?i)positional[- ]accuracy"), "positional-accuracy"),
    (re.compile(r"(?i)filtered[- ](?:peptide[- ]level[- ])?accuracy"), "accuracy-filtered"),
    (re.compile(r"(?i)accuracy|\bacc\b|\bacc\."), "accuracy"),
    # After precision and recall, so 'Prec.' does not land here, and after
    # precision@cov1, whose text also contains 'cov'.
    (re.compile(r"(?i)coverage|\bcov\b|\bcov\."), "coverage"),
]
# A bare 'Amino' or 'Peptide' counts, because a row-group label is set
# vertically and arrives one word per row: 'Amino' / 'Acid' / 'Precision'
# down three successive row labels. Requiring the full phrase left the first
# and last rows of such a group with no level at all.
LEVEL_WORDS = [
    # 'AAid' is BiATNovo's preprint's own abbreviation ('Precision_AAid(%)').
    (re.compile(r"(?i)amino[- ]?acid|\bamino\b|\bAA(?:id)?\b|residue[- ]level"), "amino acid"),
    (re.compile(r"(?i)peptide|\bpep\b|\bpept\.|full[- ]sequence"), "peptide"),
    # 'PTMs' and the glued 'PTMsprecision' too: AdaNovo heads its PTM table
    # 'PTMs precision', which the old '\bPTM\b' missed, leaving only the
    # caption's "amino acids" to supply a level -- the wrong one.
    # 'PTMlevel' arrives glued (LIPNovo+'s Table 4), so 'level' may follow.
    (re.compile(r"(?i)(?<![a-z])PTMs?(?:(?![a-rt-z])|(?=level))|modification"), "ptm"),
    (re.compile(r"(?i)spectr(?:um|a)[- ]level"), "spectrum"),
]

# B1: the basis cues. Nothing here is a guess; each requires a sentence, which
# is stored, and a method matched by cues of two different kinds stays
# 'unclear'. Choosing between them is the error BENCHMARKS.md is about.
BASIS_CUES = [
    ("released", re.compile(r"(?i)(?:pre-?trained|released|published|official|provided)\s+"
                            r"(?:model|weights|checkpoint|version)|"
                            r"(?:weights|checkpoint|model)s?\s+(?:released|provided|published)\s+by|"
                            r"publicly available (?:model|weights|checkpoint)|"
                            r"as provided by the (?:authors|original)|"
                            r"(?:use|used|adopt|employ)\w*\s+the\s+official")),
    ("retrained", re.compile(r"(?i)we\s+(?:re-?)?train|re-?trained\s+(?:all|each|every|the|them|it)|"
                             r"trained?\s+from\s+scratch|under the same (?:setting|training|budget|condition)|"
                             r"same training (?:set|data|configuration|protocol)|"
                             r"leave[- ]one[- ]out|for \d+ epochs")),
    ("reimplemented", re.compile(r"(?i)our (?:own )?implementation of|we re-?implement|"
                                 r"no (?:public )?code (?:was |is )?available|re-?implemented")),
    ("quoted", re.compile(r"(?i)(?:numbers|results|values|scores)\s+(?:are\s+)?(?:taken|quoted|copied|cited|reported)\s+(?:from|in|by)|"
                          r"as reported (?:in|by)|reported in (?:their|the original)|"
                          r"we (?:cite|quote|copy) the")),
]

# The residue that context-restricted matching cannot reach. ONE COMMENT PER
# ENTRY giving the reasoning, following TOOL_ALIASES in build_benchmarks.py.
COMPARISON_ALIASES: dict[str, tuple[str, str | None]] = {
    # Printed as one token in most tables; the catalog keeps one Casanovo row
    # covering v1..v5, so the version has to survive as its own field.
    "casanovov2": ("Casanovo", "V2"),
    "casav2": ("Casanovo", "V2"),
    "casanovo2": ("Casanovo", "V2"),
    # PepNet's paper and several tables write the architecture, not the tool.
    "deepnovov2": ("DeepNovo V2", None),
    # 'GCNovo' is the container name denovo_benchmarks uses for Denovo-GCN;
    # same identification, and the same reasoning as build_benchmarks.py's
    # TOOL_ALIASES entry: the code is DeepNovoV2 plus a model_gcn module.
    "gcnovo": ("Denovo-GCN", None),
    # pi-folding happens in norm(), but these two are also written out.
    "pihelixnovo": ("π-HelixNovo", None),
    "piprimenovo": ("π-PrimeNovo", None),
    # Truncations that drop the pi, so no prefix of the catalog name survives:
    # a column reading 'Prime.' cannot reach 'pi-PrimeNovo' by prefix.
    "prime": ("\u03c0-PrimeNovo", None),
    "primenovo": ("\u03c0-PrimeNovo", None),
    "helix": ("\u03c0-HelixNovo", None),
    "helixnovo": ("\u03c0-HelixNovo", None),
}

# A table names the paper's own method 'Ours' at least as often as it names it.
# Resolved against the publication's own describing method rather than by any
# string match, which is the only correct reading.
SELF_WORDS = {"ours", "ourmethod", "ourmodel", "ourapproach", "proposed",
              "proposedmethod", "proposedmodel", "thiswork", "ourwork", "our"}



def norm(s: str) -> str:
    """Normalise a method name for matching.

    IDENTICAL to the nested `norm` in build_benchmarks.py's resolve_tools(), on
    purpose: two normalisers that can disagree would resolve the same printed
    name to two different algorithm rows depending on which script ran.
    """
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("π", "pi"))


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

def numeric(tok: str) -> tuple[float, float | None] | None:
    """Parse a cell. Returns (value, stddev) or None if it is not a number."""
    # A TYPESET MINUS is U+2212, not '-': MemNovo prints its negative
    # differences as '−0.4', which read as text left those rows one value
    # short and refused the table as ragged.
    tok = tok.strip().replace("\u2212", "-")
    # A PRECURSOR CHARGE ('2+', '3+') is a label, not a number: pi-PrimeNovo's
    # Table 10 reports recall per charge, and reading '2+' as 2 turned its label
    # column into a data column headed 'Charge'. Only a single digit with a
    # plus; '0.664+' is a value with a footnote marker and stays one.
    if re.fullmatch(r"[1-9]\+", tok):
        return None
    m = PM.match(tok)
    if m:
        return float(m.group(1)), float(m.group(2))
    # 'value(sd)': Pairwise Attention prints its PA column as '0.463(0.004)',
    # the mean of three seeds with the standard deviation in brackets. Unread,
    # the whole column vanished and BASE and PA merged into one.
    m = re.fullmatch(r"(-?\d*\.\d+)\((\d*\.\d+)\)", tok)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = NUM.match(tok)
    if m:
        try:
            return float(m.group(1).replace(",", "")), None
        except ValueError:
            return None
    return None


LINE_NO = re.compile(r"^\d{1,4}$")


def collapse_fake_bold(words: list[dict]) -> list[dict]:
    """Undo FAKE BOLD: text drawn three times at small offsets to look heavy.

    SeqNovo's Table V bolds its best values that way, and the text layer reads
    '30.93' as '333000...999333' -- every character tripled -- so the cell is
    not a number and the row it sits in stopped being a data row. A word whose
    characters come in identical runs of three is collapsed and marked
    `fake_bold`, which page_emphasis() then counts as the paper's bold, since
    that is what it is on the page. Six characters at least, so a genuine
    'www' or 'III' is left alone.
    """
    out = []
    for w in words:
        t = w["text"]
        if (len(t) >= 6 and len(t) % 3 == 0
                and all(t[i] == t[i + 1] == t[i + 2] for i in range(0, len(t), 3))):
            w = {**w, "text": t[::3], "fake_bold": True}
        out.append(w)
    return out


def strip_line_numbers(words: list[dict]) -> list[dict]:
    """Drop ICLR-style margin line numbers before anything else reads the page.

    A conference preprint prints a line number every fifth line in the margin.
    They are INTEGERS, so they parse as cells: one lands in its own row and
    splits a table in two, and the next is prefixed onto a row label, giving
    '327 Amino Deep.' and a row with one cell too many. On CrossNovo's ICLR
    submission that made a correct 11-column table ragged, while the identical
    table in the camera-ready version parsed cleanly.

    Detection is per page and needs a RUN, not a single token: integers in a
    narrow vertical band whose values increase down the page. Five or more is
    line numbering; fewer is a column of small integers, which a real table may
    legitimately have (charge states, spectrum counts).
    """
    bands: dict[int, list[dict]] = collections.defaultdict(list)
    for w in words:
        if LINE_NO.match(w["text"]):
            bands[int(w["x0"] // 6)].append(w)
    drop: set[int] = set()
    for band in bands.values():
        if len(band) < 5:
            continue
        seq = sorted(band, key=lambda w: w["top"])
        vals = [int(w["text"]) for w in seq]
        rising = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
        if rising >= len(vals) - 2:
            drop.update(id(w) for w in seq)
    return [w for w in words if id(w) not in drop] if drop else words


# A number or the START of one: '0' and '0.' are both joinable, because the
# fragments arrive one character at a time and the intermediate results have
# to be allowed through. Two separate numbers never match it.
PURE_NUMBER = re.compile(r"^-?\d{1,3}(?:[.,]\d*)?$")


# ONE NAME PER SPECIES, whatever a paper prints. Measured across the accepted
# tables: 69 spellings for nine species and an aggregate -- 'B. sub.',
# 'B.sub.', 'Bacillus', 'BacillusSubtilis' are one organism, and 'Clam
# bacteria', 'Clam Ba.', 'C. end.' and 'CandidatusEndoloripes' another. The
# canonical names are the catalog's OWN: the nine-species benchmark's
# provenance submissions record their species in dataset_address.part, so a
# resolved subset also carries the accession its spectra came from.
#
# Common names are explicit, because no rule derives 'Human' from 'Homo
# sapiens'. Abbreviations resolve by genus initial plus the START of the
# epithet ('M. maz.' -> Methanosarcina mazei), typos by a near match on the
# epithet ('B. subtilus'). An unresolved subset keeps its printed form: an
# enzyme, a DIA dataset, a run, is not a species and is not forced into one.
SPECIES_COMMON = {
    "human": "Homo sapiens", "mouse": "Mus musculus",
    "yeast": "Saccharomyces cerevisiae",
    "honeybee": "Apis mellifera", "honey bee": "Apis mellifera",
    "tomato": "Solanum lycopersicum",
    "rice bean": "Vigna mungo", "ricebean": "Vigna mungo",
    "clam bacteria": "Candidatus Thiodiazotropha endoloripes",
    "clambacteria": "Candidatus Thiodiazotropha endoloripes",
    "c bacteria": "Candidatus Thiodiazotropha endoloripes",
    "bacillus": "Bacillus subtilis",
}
# 'Weighted Average' (Prime-DiffNovo) is an aggregate too, and a different
# one: weighted by spectra, so it keeps its own name, like Average and Mean.
AGGREGATE = re.compile(r"(?i)^\s*(weighted\s*average|average|avg\.?|mean|overall)\s*$")
_SPECIES_CACHE: dict | None = None


def species_index(con: sqlite3.Connection) -> dict[str, str]:
    """canonical species name -> its provenance accession, from the catalog."""
    global _SPECIES_CACHE
    if _SPECIES_CACHE is None:
        _SPECIES_CACHE = {part: acc for acc, part in con.execute(
            "SELECT da.accession, da.part FROM dataset_address da "
            "JOIN dataset_version dv ON dv.id = da.dataset_version_id "
            "JOIN dataset d ON d.id = dv.dataset_id "
            "WHERE d.name = 'Nine-species benchmark' AND da.is_provenance = 1 "
            "AND da.part IS NOT NULL")}
    return _SPECIES_CACHE


def canonical_subset(printed: str, con: sqlite3.Connection) -> tuple[str | None, str | None]:
    """(canonical subset, provenance accession) for a printed subset, or (None, None)."""
    if not printed:
        return None, None
    # AN AGGREGATE KEEPS ITS OWN WORD. 'Average' and 'Mean' are NOT merged:
    # a paper's average may be weighted by spectra where another's mean is the
    # plain mean of its per-species values (LIPNovo: "'mean' is computed by
    # averaging across the eight test species"), and one name for both would
    # hide a difference the papers chose to state. Only spellings of one word
    # are unified -- 'Average', 'AVERAGE', 'Avg.'.
    ma = AGGREGATE.match(printed)
    if ma:
        word = re.sub(r"\s+", " ", ma.group(1).lower().rstrip("."))
        if word.startswith("weighted"):
            return "Weighted average", None
        return {"average": "Average", "avg": "Average",
                "mean": "Mean", "overall": "Overall"}[word], None
    species = species_index(con)
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", printed)        # ApisMellifera
    toks = re.findall(r"[a-z]+", spaced.lower())
    if not toks:
        return None, None
    joined = " ".join(toks)
    for key, name in SPECIES_COMMON.items():
        # A common name, or a truncation of one ('Honeyb.', 'Clam Ba.'), but
        # only from four letters, so 'Hum' or 'Ho' cannot pick one.
        if joined == key or (len(joined) >= 4 and key.startswith(joined)):
            return name, species.get(name)
    # A PROTEASE, with its chain if printed: CrossNovo's two antibody tables
    # say 'HC Chymo.' and 'HC Chymotrypsin' for the same digest.
    enz = {"aspn": "AspN", "chymotrypsin": "Chymotrypsin", "gluc": "GluC",
           "lysc": "LysC", "proteinase": "Proteinase K", "trypsin": "Trypsin"}
    chain = toks[0].upper() if toks[0] in ("hc", "lc") else None
    rest = "".join(toks[1:] if chain else toks)
    for key, name in enz.items():
        if rest == key or (len(rest) >= 4 and key.startswith(rest)):
            return (f"{chain} {name}" if chain else name), None
    # 'ProteinaseK' arrives as 'proteinasek'; pi-PrimeNovo prints
    # 'Chymotrysin', a typo for the same enzyme. A near match from six letters.
    if rest == "proteinasek":
        return (f"{chain} Proteinase K" if chain else "Proteinase K"), None
    if len(rest) >= 6:
        try:
            from rapidfuzz import fuzz
            near = [n for k, n in enz.items() if fuzz.ratio(rest, k) >= 88]
        except ImportError:
            near = []
        if len(near) == 1:
            return (f"{chain} {near[0]}" if chain else near[0]), None
    if len(toks) >= 2:
        g, e = toks[0], toks[-1]
        hits = [n for n in species
                if n.lower().split()[0].startswith(g) and n.lower().split()[-1].startswith(e)]
        if not hits:
            try:
                from rapidfuzz import fuzz
                hits = [n for n in species if n.lower().split()[0].startswith(g)
                        and fuzz.ratio(e, n.lower().split()[-1]) >= 80]
            except ImportError:
                hits = []
        if len(hits) == 1:
            return hits[0], species.get(hits[0])
    return None, None


def heal_fragments(row: list[dict], gap: float = 2.0) -> list[dict]:
    """Join word fragments that are one printed number broken by the text layer.

    A SHREDDED ROW IS ONE ROW, NOT A RAGGED ONE. LIPNovo's Table 3 has one
    data row that arrives as '0 . 7 4 3 0. 7 4 5 0 . 5 5 9 ...', 23 fragments
    where there are six numbers, which put several cells in every column and
    refused the whole table as a ragged grid. The fragments are adjacent --
    under 2 pt apart, against a column pitch of 27 -- so they are joined.

    Only where the JOIN IS A PLAIN NUMBER. That is what keeps a real ragged
    row ragged: two separate numbers in one column concatenate into something
    that is not a number and are left alone, and a footnote marker stays
    attached to its own cell rather than being absorbed ('0.725' + '*' does
    not join, because the result is not a plain number).
    """
    # ONLY A ROW THAT IS ACTUALLY SHREDDED. Four or more one- and two-
    # character fragments is what a broken text layer looks like; an ordinary
    # row has none. Without this, a superscript citation beside a value could
    # be absorbed into it -- '0.785' and a raised '7' are 0.4 pt apart. The
    # vertical test below is the second guard against exactly that, since a
    # superscript's top sits about 3 pt higher.
    if sum(1 for w in row if len(w["text"].strip()) <= 2) < 4:
        return list(row)
    out: list[dict] = []
    for w in sorted(row, key=lambda w: w["x0"]):
        if out:
            prev = out[-1]
            joined = prev["text"] + w["text"]
            if (w["x0"] - prev["x1"] <= gap and PURE_NUMBER.match(joined)
                    and abs(w["top"] - prev["top"]) < 2.0):
                out[-1] = {**prev, "text": joined, "x1": w["x1"],
                           "bottom": max(prev["bottom"], w["bottom"])}
                continue
        out.append(dict(w))
    return out


def join_spaced_pm(row: list[dict]) -> list[dict]:
    """Join a spread printed as three words, '0.89', '±', '0.03', into one.

    The text layer splits 'value ± sd' wherever the setter spaced it, and the
    three words then count as two numbers in one column: GA-Novo's Table 5 read
    as ragged. Only number, '±', number, each gap under 12 pt.
    """
    out: list[dict] = []
    i = 0
    row = sorted(row, key=lambda w: w["x0"])
    while i < len(row):
        a = row[i]
        if (i + 2 < len(row) and row[i + 1]["text"].strip() == "\u00b1"
                and PURE_NUMBER.match(a["text"].strip())
                and PURE_NUMBER.match(row[i + 2]["text"].strip())
                and row[i + 1]["x0"] - a["x1"] < 12 and row[i + 2]["x0"] - row[i + 1]["x1"] < 12):
            c = row[i + 2]
            out.append({**a, "text": f"{a['text'].strip()}\u00b1{c['text'].strip()}",
                        "x1": c["x1"], "bottom": max(a["bottom"], c["bottom"])})
            i += 3
            continue
        out.append(a)
        i += 1
    return out


def word_rows(words: list[dict], tol: float = 2.5) -> list[list[dict]]:
    """Cluster words into visual rows on `top`.

    The tolerance is in points and deliberately small: a 2.5 pt window keeps a
    subscript or a superscript with its own line rather than merging two table
    rows, and these tables are set at 8 to 10 pt with 10 to 12 pt leading.
    """
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [join_spaced_pm(sorted(r, key=lambda w: w["x0"])) for r in rows]


def row_kind(row: list[dict]) -> str:
    """'data', 'interstice' or 'other'.

    An INTERSTICE is a short row with no numbers, which is what a row-group
    label looks like when it is set on its own line between data rows:
    CrossNovo's appendix tables put 'AminoAcid', 'Precision', 'Peptide' and
    'Recall' on four such rows. Treated as a block boundary they cut one
    8-row table into fragments, and the fragment below the header then had no
    header at all, which is where 29 of the rejections came from.
    """
    # A CAPTION LINE IS NEVER DATA, whatever numbers it mentions: GA-Novo's
    # 'Table 5. The results of sequencing 120 MS/MS spectra' carries '5.' and
    # '120', joined the block below it and made the grid ragged.
    if row and LABEL_ROW.match(" ".join(w["text"] for w in row)):
        return "other"
    nums = sum(1 for w in row if numeric(w["text"]))
    # A LINE OF PROSE IS NOT DATA because it names two numbers: 'a confidence
    # score level between 0 and 100 [7]' under GA-Novo's Table 5 joined the
    # block and gave the grid seven columns. Eight WORDS or more -- tokens
    # with letters in them, so a not-run dash is not one: LIPNovo's PEAKS row
    # is a label, two numbers and ten '-', and counting the dashes made it a
    # sentence -- and numbers under a quarter of them, is a sentence.
    # ONLY A CONTINUOUS LINE. On a two-column page a table row shares its line
    # with the other column's prose -- AdaNovo's 'CasaNovo 0.582 0.297 thus
    # shows superiority over the re-weighting methods in' -- and that row is
    # data. A sentence has no gap wider than a word space in it.
    words_ = sum(1 for w in row if sum(c.isalpha() for c in w["text"]) >= 2)
    srt = sorted(row, key=lambda w: w["x0"])
    widest = max((b["x0"] - a["x1"] for a, b in zip(srt, srt[1:])), default=0.0)
    if words_ >= 8 and nums < 0.25 * words_ and widest < 15.0:
        return "other"
    if nums >= 2:
        return "data"
    # A ROW OF SIGNIFICANCE MARKERS -- GA-Novo's '(+) (+) (+) (+) (=)', a
    # t-test against PEAKS set on its own line under the values -- is not a
    # row of the table; it is read past like a label line.
    if row and all(re.fullmatch(r"\([+=\-\u2212*]\)", w["text"].strip()) for w in row):
        return "interstice"
    if nums == 0 and len(row) <= 3:
        return "interstice"
    return "other"


def data_blocks(rows: list[list[dict]], min_rows: int = 2) -> list[tuple[int, int]]:
    """Runs of rows that look like table data.

    A block must hold at least `min_rows` data rows. One is almost always
    prose: 'Plasma -- are summarized in Tables 1 and 2' yields two numeric
    tokens on one line and was being offered as a two-column table, which then
    collected a real caption by the pairing rule and was rejected as ragged.
    Two consecutive numeric rows is a much rarer accident in running text.
    """
    kind = [row_kind(r) for r in rows]
    # The page's typical line pitch, used as the scale for "a large gap".
    tops = [r[0]["top"] for r in rows if r]
    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if b > a)
    line_gap = gaps[len(gaps) // 2] if gaps else 12.0
    blocks, i, n = [], 0, len(rows)
    while i < n:
        if kind[i] != "data":
            i += 1
            continue
        j = i
        while True:
            nxt = None
            for k in range(j + 1, min(n, j + 4)):
                if kind[k] == "data":
                    nxt = k
                    break
            if nxt is None:
                break
            between = kind[j + 1:nxt]
            # A BLOCK CANNOT JUMP A LARGE VERTICAL GAP, however few rows sit in
            # it. Counting rows alone merged a table with the FIGURE beneath
            # it: AdaNovo's Table 1 ends on 'Clam bacteria' and the next
            # numeric row is a panel's axis ticks, 1.0 / 0.9 / 0.8, an inch
            # down the page. Two numeric rows and nothing textual between them
            # looked contiguous, and the resulting grid was ragged, which is
            # where a large share of the ragged rejections came from.
            gap = rows[nxt][0]["top"] - rows[j][0]["top"]
            if gap > 3.2 * line_gap:
                break
            # A BLOCK CANNOT RUN THROUGH A BARE TABLE LABEL. SeqNovo prints
            # Table IV and Table V close together, IEEE style, and between them
            # sit only 'TABLE V' alone on its line and a one-word small-caps
            # caption: two short rows with no numbers, which pass as
            # interstices. The two tables became one block, the page had three
            # labels for two grids, and every label shifted onto the table above
            # it. ONLY A BARE LABEL: a label row that carries its caption is
            # what a two-column page puts beside the OTHER column's rows, and
            # breaking there split GyroNovo's Table 2 and PhysNovo's Table 3,
            # both approved, in half.
            if any(re.fullmatch(r"(?i)table\s*[IVXLC\d]+[.:]?",
                                " ".join(w["text"] for w in rows[b]).strip())
                   for b in range(j + 1, nxt)):
                break
            # Any number of interstices may be skipped; at most one 'other',
            # which covers a rule, a continued label or a stray prose line.
            if all(b == "interstice" for b in between) or len(between) == 1:
                j = nxt
            else:
                break
        if sum(1 for k in kind[i:j + 1] if k == "data") >= min_rows:
            blocks.append((i, j))
        i = j + 1
    return blocks


def column_edges(rows: list[list[dict]], lo: int, hi: int) -> list[tuple[float, float]]:
    """Derive column x-intervals FROM THE NUMERIC DATA ROWS ONLY.

    The data rows are the clean signal. Including the header row would smear
    the boundaries, because a header label ('Casa.V2', 'Amino acid precision')
    is far wider than the number under it and routinely overhangs its column.
    So the columns are defined by the numbers and the header is assigned INTO
    them afterwards.

    The intervals TILE the row, meeting at the midpoints between adjacent
    numeric centres, rather than hugging the digits. Hugging them produced
    2 pt wide columns, and against those a centred spanner header overlapped
    only the two columns it physically sat above: 'Amino acid precision',
    centred over six columns of ContraNovo's Table 1, reached columns 2 and 3
    and left the other four with no metric at all.
    """
    pts: list[tuple[float, int]] = []
    for ri, r in enumerate(rows[lo:hi + 1]):
        for w in r:
            if numeric(w["text"]):
                pts.append(((w["x0"] + w["x1"]) / 2, ri))
    if not pts:
        return []
    pts.sort()
    # Single-linkage on the gaps. 9 pt is wider than the inter-digit spacing
    # inside one number and narrower than any inter-column gutter seen here.
    groups: list[list[tuple[float, int]]] = [[pts[0]]]
    for p in pts[1:]:
        if p[0] - groups[-1][-1][0] <= 9.0:
            groups[-1].append(p)
        else:
            groups.append([p])
    # A COLUMN IS SUPPORTED BY MORE THAN ONE ROW. A table set in one page
    # column shares its rows with the other column's prose, and a number in
    # that prose makes a column of its own: ContraNovo's Table 2 picked up
    # '0.46 Da' and '0.33' from the paragraph beside it and was refused as a
    # five-column ragged grid. In a block of four or more numeric rows, a group
    # that only one row supports is not a column.
    # ...and only when that one number sits AMONG WORDS, which is what prose
    # looks like. Dropping every single-row group moved real numbers into a
    # neighbouring column's widened interval and broke PhysNovo's page 8, whose
    # stacked tables put isolated values in rows of their own.
    n_rows = len({ri for _x, ri in pts})
    if n_rows >= 4:
        def prose_stray(g) -> bool:
            if len({ri for _x, ri in g}) != 1:
                return False
            x, ri = g[0]
            near = [w for w in rows[lo + ri]
                    if abs((w["x0"] + w["x1"]) / 2 - x) <= 60
                    and sum(ch.isalpha() for ch in w["text"]) >= 2]
            return len(near) >= 2
        kept = [g for g in groups if not prose_stray(g)]
        groups = kept or groups
    mids = [sum(x for x, _r in g) / len(g) for g in groups]
    if len(mids) == 1:
        half = 12.0
        return [(mids[0] - half, mids[0] + half)]
    bounds = [mids[0] - (mids[1] - mids[0]) / 2]
    bounds += [(mids[i - 1] + mids[i]) / 2 for i in range(1, len(mids))]
    bounds.append(mids[-1] + (mids[-1] - mids[-2]) / 2)
    return [(bounds[i], bounds[i + 1]) for i in range(len(mids))]


def column_groups(edges: list[tuple[float, float]]) -> list[list[int]]:
    """Split a block's columns where a GUTTER separates two tables.

    Rows are detected across the whole page, so two tables printed side by
    side in a two-column layout come out as ONE block: LIPNovo's page 7 gave a
    single ten-column block spanning x 132 to 544, which is both of its tables
    at once, with one caption for the pair and a crop showing the neighbour.
    Merging two different tables is not a cosmetic problem -- their columns
    mean different things.

    The gutter is found as a gap far wider than this block's own typical
    inter-column gap. A table's columns are evenly spaced by construction, so
    a gap several times the usual one is a page gutter and not a column.
    """
    if len(edges) < 4:
        return [list(range(len(edges)))]
    # MEASURED ON THE COLUMN CENTRES. column_edges() returns intervals that
    # TILE the row, meeting at the midpoints between numeric centres, so the
    # gap between consecutive intervals is zero by construction and no gutter
    # could ever be seen in them.
    cen = [(a + b) / 2 for a, b in edges]
    gaps = [cen[i + 1] - cen[i] for i in range(len(cen) - 1)]
    typical = sorted(gaps)[len(gaps) // 2]
    out, cur = [], [0]
    for i, g in enumerate(gaps):
        # 2.5x the typical pitch with a 55 pt floor. Measured: LIPNovo's merged
        # block has centre gaps [27, 27, 26, 26, 63, 99, 65, 28, 25], so the
        # gutter is the 99 against a typical 28, while ContraNovo's and
        # CrossNovo's single tables are uniform at 33 to 40 and must not split.
        if g > max(55.0, 2.5 * max(typical, 1.0)):
            out.append(cur)
            cur = [i + 1]
        else:
            cur.append(i + 1)
    out.append(cur)
    return [g for g in out if len(g) >= 2] or [list(range(len(edges)))]


def spanner_phrases(row: list[dict], gap: float = 3.0) -> list[dict]:
    """Phrases for a SPANNER row: words a normal space apart are one phrase.

    phrases() merges only a shredded row, so on an ordinary row every word is
    its own phrase, and a spanner row (fewer phrases than columns) was then
    distributed word by word: Prime-DiffNovo's 'Nine-species MSV000081382'
    went 'Nine-species' to two columns and the accession to the next two, and
    the version cue reached only half of each dataset. A normal space is 2-3
    pt; separate spanners are far wider apart. Own-header rows keep the word
    level reading, which tightly set column headers need (AdaNovo).
    """
    out: list[dict] = []
    for w in sorted(phrases(row), key=lambda w: w["x0"]):
        if out and 0 <= w["x0"] - out[-1]["x1"] <= gap:
            out[-1] = {**out[-1], "text": out[-1]["text"] + " " + w["text"], "x1": w["x1"]}
        else:
            out.append(dict(w))
    return out


def phrases(row: list[dict], gap: float = 6.0) -> list[dict]:
    """Group a row's words into phrases, so a multi-word header stays one unit.

    'Amino acid precision' arrives as one token from some producers and three
    from others. Treating the three separately sends each to whichever column
    it happens to sit over, which is how a spanner silently becomes part of a
    method name.
    """
    # ONLY A ROW THAT IS ACTUALLY SHREDDED. Four or more one- and two-
    # character fragments is what a broken text layer looks like; an ordinary
    # row has none. Without this, a superscript citation beside a value could
    # be absorbed into it -- '0.785' and a raised '7' are 0.4 pt apart. The
    # vertical test below is the second guard against exactly that, since a
    # superscript's top sits about 3 pt higher.
    if sum(1 for w in row if len(w["text"].strip()) <= 2) < 4:
        return list(row)
    out: list[dict] = []
    for w in sorted(row, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] <= gap:
            out[-1] = {"text": out[-1]["text"] + " " + w["text"],
                       "x0": out[-1]["x0"], "x1": w["x1"], "top": out[-1]["top"]}
        else:
            out.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]})
    return out


def assign(tok: dict, edges: list[tuple[float, float]]) -> int | None:
    """Which column a token belongs to, by centre, then by best overlap."""
    c = (tok["x0"] + tok["x1"]) / 2
    for i, (a, b) in enumerate(edges):
        if a <= c <= b:
            return i
    best, score = None, 0.0
    for i, (a, b) in enumerate(edges):
        ov = min(tok["x1"], b) - max(tok["x0"], a)
        if ov > score:
            best, score = i, ov
    return best if score > 0 else None


class Reject(Exception):
    """A guard fired. The message is the guard id plus what it saw."""


def row_label(toks: list[dict], first_edge: float) -> str:
    """The row's label, which may be several columns wide.

    Everything to the left would, on a two-column page, swallow the adjacent
    column's prose, which sits at the same `top` as the table row. But taking
    only the contiguous run nearest the numbers was too strict in the other
    direction: an ACL-style table puts the metric group label in its own
    leftmost column, so the row reads 'Amino' ... 'Deep.' with a wide gutter
    between, and dropping 'Amino' lost the only statement of what the row
    measures.

    So runs are taken from the right leftwards while the label stays short.
    A prose column runs to many more tokens than four and is cut off; a
    genuine two-column label is three or four tokens and survives.
    """
    left = [w for w in toks if w["x1"] <= first_edge]
    if not left:
        return ""
    # FRAGMENTS THAT TOUCH ARE ONE WORD. Some producers emit a label glyph by
    # glyph with jittered baselines, and the text layer splits it where the
    # baseline jumps: CausalNovo's Table 3 reads '†C a sa N o v o' and
    # '+ C a us a l Novo'. A real space is 2-3 pt; these gaps run from
    # slightly negative (overlapping glyph boxes) to 1.5 pt.
    merged: list[dict] = []
    for w in sorted(left, key=lambda w: w["x0"]):
        # (the LAST FRAGMENT's length, not the merged word's: '+Causal' then
        # 'Novo' joins because the piece before 'Novo' was 'l')
        if (merged and -1.0 <= w["x0"] - merged[-1]["x1"] <= 1.6
                and min(len(w["text"]), merged[-1].get("_frag", len(merged[-1]["text"]))) <= 2):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + w["text"],
                          "x1": w["x1"], "_frag": len(w["text"])}
        else:
            merged.append(w)
    left = merged
    runs: list[list[dict]] = [[left[0]]]
    for w in left[1:]:
        if w["x0"] - runs[-1][-1]["x1"] <= 20.0:
            runs[-1].append(w)
        else:
            runs.append([w])
    kept: list[list[dict]] = []
    total = 0
    for run in reversed(runs):
        if total and total + len(run) > 4:
            break
        kept.insert(0, run)
        total += len(run)
    return " ".join(w["text"] for run in kept for w in run).strip()


def block_bbox(rows: list[list[dict]], lo: int, hi: int,
               cap_end: int, edges: list[tuple[float, float]],
               left_bound: float, max_header: int = 5,
               cap_start: int = -1,
               right_limit: float = float("inf"),
               left_limit: float = 0.0) -> tuple[float, float, float, float]:
    """(x0, top, x1, bottom) covering the caption, the header and the body.

    Used only to CROP THE PRINTED TABLE OUT OF THE PAGE for review, never for
    parsing. Clipped horizontally to the table's own extent, because on a
    two-column page the neighbouring column sits at the same y and would
    otherwise fill half the picture.
    """
    # ONLY REACH UP AS FAR AS THE TABLE. A caption can sit many lines above
    # its table with body prose in between, and starting the crop at the
    # caption then filled the picture with paragraphs and cut off the table:
    # publication 13's crop was mostly text with a fragment of the grid.
    # START AT THE CAPTION'S OWN LABEL ROW, not where the caption ENDS. A
    # caption's continuation runs on into the table's header rows, so starting
    # at its end cut the top off the picture: LIPNovo+'s Table 8 lost its
    # caption and both of its upper header rows, leaving a crop that began
    # mid-table.
    start = cap_start if 0 <= cap_start < lo else cap_end
    # A LONG CAPTION ABOVE THE TABLE IS STILL ITS CAPTION. The row limit on the
    # label exists for a caption far above its table with body prose between;
    # it also refused publication 168's TABLE I, whose ten-line caption ends
    # four rows above the data. What matters is where the caption ENDS.
    # Rows between the caption and the data count only if they hold text in
    # THIS table's width: the other table of a side-by-side pair fills those
    # rows too, and counting them made LIPNovo's Table 5 caption look far
    # from its table, so the crop started below it.
    _tx0, _tx1 = left_bound - 12, min(edges[-1][1] + 14, right_limit)
    between = [i for i in range(cap_end + 1, lo) if 0 <= i < len(rows)
               and any(w["x1"] >= _tx0 and w["x0"] <= _tx1 for w in rows[i])]
    cap_close = 0 <= cap_end < lo and len(between) <= max_header + 1
    if 0 <= start < lo and (lo - start <= max_header + 3 or cap_close):
        first = start
    else:
        # A CAPTION BELOW ITS TABLE gives no upper bound, and a fixed three
        # rows above the data reached into the body text over the header:
        # CrossNovo's Table 2 crop opened on two lines of prose. Walk up from
        # the data instead while the rows stay at the table's own spacing; a
        # paragraph break above the header is the edge. Measured there: header
        # to data 12.5 pt, prose to header 25.9 pt.
        #
        # MEASURED ON THE TABLE'S OWN WIDTH. On a two-column page the other
        # column's lines interleave with the header rows, and the gaps between
        # whole rows are mostly theirs: publication 139's table sits in the
        # right column and the walk stopped on a left-column gap, cutting off
        # its 'Unprocessed / Purified / Deconvoluted (%)' header. Rows with no
        # text inside the table's x-range are stepped over.
        tx0, tx1 = left_bound - 12, min(edges[-1][1] + 14, right_limit)
        def own(i):
            return [w for w in rows[i] if w["x1"] >= tx0 and w["x0"] <= tx1]
        first, below = lo, min(w["top"] for w in own(lo) or rows[lo])
        pitch = None
        for i in range(lo - 1, max(-1, lo - max_header - 3), -1):
            mine = own(i)
            if not mine:
                continue
            gap = below - min(w["top"] for w in mine)
            pitch = pitch or max(gap, 6.0)
            if gap > max(1.7 * pitch, pitch + 7.0):
                break
            first, below = i, min(w["top"] for w in mine)
    last = max(hi, cap_end if cap_end > hi else hi)
    # MEASURE THE HEIGHT FROM WORDS INSIDE THE CROP, not from every word on
    # those rows. A journal's rotated margin watermark is one tall "word":
    # publication 58's page 5 carries 'https://academic.oup.com/...' running
    # from y 250 to y 489 at x 588, outside the table and outside the crop,
    # and its bottom stretched Table 2's picture down over Figure 2's venn
    # diagrams. The horizontal clip is already computed here, so the vertical
    # extent is taken from the words that clip keeps.
    x0 = max(0.0, left_bound - 12, left_limit)
    x1 = min(edges[-1][1] + 14, right_limit)
    # THE PADDING STOPS SHORT OF THE NEXT COLUMN'S TEXT. A fixed 14 pt past the
    # last column reached into the page's other column wherever the gutter is
    # narrower than that: publication 17's Table 2 ends its last column at
    # x 297.6 and the right-hand column's prose starts at 307.4, so the crop
    # (to 311.6) carried a sliver of eight lines of body text. Any word that
    # STARTS beyond the last column within the table's rows bounds the crop;
    # a header word inside the last column starts before it and is kept.
    rows_band = [w for i in range(first, min(last + 1, len(rows))) for w in rows[i]]
    # MEASURED FROM WHERE THE NUMBERS END, not from the last column's tiled
    # interval. Intervals are tiled out to the midpoint of the gutter, so for
    # the left table of a side-by-side pair the last interval runs right up to
    # its neighbour: LIPNovo's Table 3 ends its data at x 316.9 and its last
    # interval at 364, and Table 5's words at 326-358 counted as inside it.
    # Only THIS table's numbers: side-by-side tables share their rows, and
    # counting the neighbour's put the 'end of the data' at its right edge.
    nums = [w["x1"] for i in range(lo, hi + 1) if 0 <= i < len(rows)
            for w in rows[i] if numeric(w["text"])
            and w["x0"] >= edges[0][0] - 2 and w["x1"] <= edges[-1][1] + 2]
    data_right = max(nums) if nums else edges[-1][1]
    # A HEADER WIDER THAN ITS NUMBERS is still the table's. ReNovo's Table 8
    # heads its last column 'Peptide AUC', which runs 33 pt past the values
    # and past the 14 pt pad, so the crop showed 'Peptide AU'. A word that
    # STARTS inside the last column belongs to it, however far it runs.
    # Only the HEADER rows, above the first data row: the prose under a table
    # starts inside its last column just the same, and would widen the crop to
    # the text block's edge.
    tail = [w["x1"] for i in range(first, lo) if 0 <= i < len(rows)
            for w in rows[i] if not numeric(w["text"])
            and edges[-1][0] - 2 <= w["x0"] <= edges[-1][1]]
    if tail:
        x1 = max(x1, min(max(tail) + 6, right_limit))
    beyond = [w["x0"] for w in rows_band
              if w["x0"] > min(edges[-1][1], data_right + 6) + 2]
    if beyond:
        x1 = min(x1, min(beyond) - 2)
    band = [w for w in rows_band if w["x1"] >= x0 and w["x0"] <= x1]
    if not band:
        return (0.0, 0.0, 0.0, 0.0)
    return (x0, min(w["top"] for w in band) - 6, x1,
            max(w["bottom"] for w in band) + 6)


def page_emphasis(page) -> tuple[list[dict], list[tuple[float, float, float]]]:
    """What the PAPER marks: (bold words, underline spans).

    A comparison table's own bold-and-underline is data, not decoration: it
    says which result the authors call best and which second. The review page
    used to derive that from the values instead, and the two disagree whenever
    a paper counts its own variants as one method -- DiffuNovo's Table 2 bolds
    DiffuNovo (MBR) and underlines pi-HelixNovo, the best COMPETITOR, while a
    ranking over the values underlines DiffuNovo (Logits). Reading the marks
    off the page removes the guess, and it is exact rather than heuristic: the
    underline rect under 0.765 spans x 355.0-377.4 and the word spans
    355.0-377.4.

    Bold comes from the font name, which needs `extra_attrs`. That is why this
    is a SEPARATE extraction pass: extract_words splits a word wherever an
    extra attribute changes, so asking for fontname in the parsing pass could
    split a cell and move it into another column.
    """
    try:
        words = collapse_fake_bold(page.extract_words(
            use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname"]))
    except Exception:
        return [], []
    # BOLD IS THE FACE THAT IS NOT THE PAGE'S OWN REGULAR FACE, measured, not
    # matched against a list of names. LaTeX with Times renders \textbf as
    # 'NimbusRomNo9L-Medi', which no pattern for 'bold|black|heavy' catches, and
    # every producer names its faces differently. The regular face is simply the
    # commonest one over the page's numeric cells; anything else, barring an
    # italic, is emphasis.
    # MARGIN LINE NUMBERS ARE NOT CELLS. A review template numbers every line
    # in the margin, and on CausalNovo's page 16 those 54 numbers, set in
    # 'NimbusSanL-Bold', outnumbered the table's own regular face (25) and
    # bold face (24): the line-number font became 'regular' and every cell in
    # the table, plain or bold, read as emphasis.
    nums = [w for w in strip_line_numbers(words) if numeric(w["text"])]
    faces = collections.Counter((w.get("fontname") or "") for w in nums)
    # And a face whose NAME says bold is never the regular one while a plainer
    # face exists: the count decides only among faces that could be regular.
    heavy = re.compile(r"(?i)bold|black|heavy|semi|demi|-medi")
    plain = [(f, n) for f, n in faces.most_common() if not heavy.search(f)]
    regular = (plain[0][0] if plain else faces.most_common(1)[0][0]) if faces else ""
    bold = [w for w in nums
            if w.get("fake_bold") or ((w.get("fontname") or "") != regular
            and not re.search(r"(?i)italic|oblique|-it\b", w.get("fontname") or ""))]
    rules = []
    for o in list(page.lines) + list(page.rects):
        # A thin horizontal span no wider than a cell. A table's full-width
        # booktabs rule is far wider and would underline every cell in the row.
        if abs(o["y0"] - o["y1"]) < 1.6 and 2.0 < (o["x1"] - o["x0"]) < 70.0:
            rules.append((o["top"], o["x0"], o["x1"]))
    return bold, rules


def emphasis_of(w: dict, bold: list[dict],
                rules: list[tuple[float, float, float]]) -> tuple[bool, bool]:
    """(is_bold, is_underlined) for one printed cell."""
    is_bold = any(abs(b["x0"] - w["x0"]) < 1.5 and abs(b["top"] - w["top"]) < 1.5
                  and b["text"].strip() == w["text"].strip() for b in bold)
    # The rect sits at the word's baseline or just below it, and covers most of
    # it. Both bounds matter: a wider span is a column rule, a narrower one is
    # a minus sign or a hyphen.
    is_under = any(-1.5 <= top - w["bottom"] <= 3.5
                   and x0 <= w["x0"] + 2.0 and x1 >= w["x1"] - 2.0
                   and (x1 - x0) <= (w["x1"] - w["x0"]) + 8.0
                   for top, x0, x1 in rules)
    return is_bold, is_under


def page_rules(page) -> list[tuple[float, float, float]]:
    """Horizontal rules on the page, as (top, x0, x1).

    A booktabs \\cmidrule under a spanner covers exactly the columns that
    spanner governs, which is the only unambiguous statement of the grouping a
    PDF contains. Where they are absent the grouping can be genuinely
    undecidable, and this script would rather say so than guess.
    """
    out = []
    for o in list(page.lines) + list(page.rects):
        if abs(o["y0"] - o["y1"]) < 1.5 and (o["x1"] - o["x0"]) > 20:
            out.append((o["top"], o["x0"], o["x1"]))
    return out


def header_model(rows: list[list[dict]], lo: int,
                 edges: list[tuple[float, float]], max_rows: int = 4,
                 left_bound: float | None = None, floor: int = 0,
                 rules: list[tuple[float, float, float]] | None = None,
                 hard_floor: int = 0, data_left: float | None = None,
                 right_bound: float | None = None):
    """Read the header rows above a data block.

    A header row carrying about as many phrases as there are columns is a row
    of COLUMN headers, assigned by position. A row carrying materially fewer
    is a SPANNER row, and its phrases are distributed by NEAREST CENTRE rather
    than by overlap: a spanner is typeset centred over its group and so is
    narrower than the group it governs. 'Amino acid precision' spans x 190-272
    while the six columns it governs span 123-337, which no overlap test can
    reconcile; nearest-centre assigns columns 0-5 to it and 6-11 to
    'Peptide precision', which is what the printed table means.
    """
    own = collections.defaultdict(list)
    span = collections.defaultdict(list)
    stub: list[str] = []
    grouped: set = set()      # columns whose spanner came from a rule
    ambiguous: set = set()    # columns whose spanner was guessed by proximity
    col_c = [(a + b) / 2 for a, b in edges]
    # CLIP EVERY ROW TO THE TABLE'S OWN WIDTH before judging it. On a
    # two-column page the other column's prose sits at the same `top` as the
    # table, so a header row read whole looks like prose and ended the walk:
    # DiffuNovo's Table 3 has its header one row above its data with a line of
    # right-column text between, and the header was never seen. Clipped, that
    # line is empty inside the table and is simply skipped.
    lo_x = edges[0][0] - 150 if left_bound is None else left_bound
    hi_x = edges[-1][1] + 10
    # ...and never into the table beside it. The left side was already bounded
    # by `left_bound`; the right was not, so LIPNovo's Table 3 read Table 5's
    # 'Baseline Impu.' into its header, and CausalNovo's Table 2 read
    # '†CasaNovo' and 'Bacillus' from Table 3.
    if right_bound is not None:
        hi_x = min(hi_x, right_bound)

    def clipped(r):
        # FRAGMENTS THAT TOUCH ARE ONE WORD here too, as in row labels:
        # LIPNovo's Table 3 header arrives letter by letter ('P r e c .'), 30
        # one-letter words that read as a line of prose and ended the walk.
        out: list[dict] = []
        for w in sorted((w for w in r if w["x1"] > lo_x and w["x0"] < hi_x),
                        key=lambda w: w["x0"]):
            # ...only where one side is a SHRED of one or two characters:
            # LIPNovo+'s metric names touch too, and were split apart on
            # purpose (split_metric_runs); joining them again undid that.
            if (out and -1.0 <= w["x0"] - out[-1]["x1"] <= 1.6
                    and min(len(w["text"]), out[-1].get("_frag", len(out[-1]["text"]))) <= 2):
                out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"],
                           "x1": w["x1"], "_frag": len(w["text"])}
            else:
                out.append(w)
        return out

    # NEVER WALK PAST THE CAPTION. Clipping the rows to the table's width made
    # the caption reachable, and a caption line holds few phrases, so it was
    # classified as a spanner and distributed across the columns: the subset of
    # every column became 'Theboldfontindicatesthebestperformance. HC Methods
    # AspN'. `floor` is the row after the caption, which the pairing step has
    # already identified, so this needs no heuristic.
    def header_like(i: int) -> bool:
        """Does row `i` read as a header row of THIS block?

        At least two of its words land in two different columns, and none is a
        number. A squashed caption line is one very wide token and so covers a
        single column, which is what keeps this from reading prose as a header.
        """
        rr = clipped(rows[i])
        if not rr or any(numeric(w["text"]) for w in rr):
            return False
        return len({assign(w, edges) for w in rr} - {None}) >= 2

    # THE FLOOR IS PERMEABLE TO A HEADER-LIKE ROW. Reserving exactly one row
    # above the block for the header is not enough when a table has TWO header
    # rows and the caption sits above them: publication 30 has a level spanner
    # over method names, the caption's continuation absorbed the spanner, and
    # every column then took its level from a caption naming BOTH levels, so
    # the two halves of the table collapsed into one measurement and G6 fired.
    # Up to two rows above the floor are reconsidered, and only if they read as
    # header rows by the test above.
    # THE DESCENT MAY NEVER CROSS THE 'Table N' ROW. Without that bound it
    # reached publication 30's caption, whose words are spaced and so cover
    # several columns and read as a header, and the caption's text was assigned
    # into the column headers: column 0 came back as
    # 'Precision I: Transformer-DIA'. A caption is prose wherever it sits.
    limit = max(hard_floor, (lo - 1) - (max_rows - 1))
    for j in range(floor - 1, limit - 1, -1):
        rr = clipped(rows[j])
        if rr and any(numeric(w["text"]) for w in rr):
            break                     # another table's data; stop here
        # A WRAPPED CAPTION LINE IS NOT A HEADER, though its spaced words
        # cover several columns just as a header's do. InstaNovo-FM's Table
        # S13 caption runs three lines ('... The best model per dataset is in
        # bold.', 'We abbreviate ... to conserve page space.'), and both
        # continuation lines were read into the column headers. A header row
        # carries no function words; two or more of them make it a sentence.
        if sum(1 for w in rr if w["text"].lower().strip(".,;:") in FUNCTION_WORDS) >= 2:
            break
        # ...and a line that ENDS A SENTENCE is caption prose too: Prime-
        # DiffNovo's caption closes 'across nine species on two benchmark
        # datasets.', one function word short of the test above, and its words
        # were read in as spanners ('species', 'two', 'benchmark').
        if len(rr) >= 4 and rr[-1]["text"].rstrip().endswith("."):
            break
        if header_like(j):
            floor = j                 # keep descending: a STUB row may sit
                                      # between the spanner and the header, as
                                      # 'DIADatasets' does in publication 30,
                                      # and it covers no column so it is not
                                      # header-like itself

    # THE HEADER SITS TIGHT AGAINST ITS BODY, so a large vertical gap ends the
    # walk however header-like the row above looks. Without this the walk
    # reached the RUNNING PAGE HEADER of PLMNovo's page -- 'PLM-Aligned Spectra
    # Embeddings for De Novo Peptide Sequencing 7' -- whose words are spread
    # right across the table's width and so covered enough distinct columns to
    # pass as a row of column headers. They were scattered into ten column
    # headers, and the two that ended up carrying a metric word lost their
    # subset and collided.
    tops = [r[0]["top"] for r in rows if r]
    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if b > a)
    pitch = gaps[len(gaps) // 2] if gaps else 12.0

    taken = 0
    for i in range(lo - 1, floor - 1, -1):
        # Measured against the LOCAL pitch, the spacing right at the table,
        # not the page median: the header rows here are 4 to 8 pt apart while
        # the page median is 12, so a 26 pt jump to the running page header
        # cleared a page-median threshold and was taken as a header row.
        # ONLY ONCE SOMETHING HAS BEEN COLLECTED. A page whose other column
        # interleaves prose with the table gives irregular gaps on the way to
        # the header, and breaking on the first of those cost two tables their
        # headers entirely. By the time the running page header is reached the
        # real header rows are already in hand, so the test is applied from
        # there on.
        if (own or span) and rows[i] and rows[i + 1]:
            gap = rows[i + 1][0]["top"] - rows[i][0]["top"]
            local = (rows[i + 2][0]["top"] - rows[i + 1][0]["top"]
                     if i + 2 < len(rows) and rows[i + 2] else pitch)
            # AND larger than the page's own line pitch: a stub label centred
            # beside a two-row header makes a tiny local gap. LIPNovo+'s
            # 'Method Year' sits 4 pt above its dataset row, so the ordinary
            # 12 pt line above it read as a jump and the level spanner
            # ('Amino acid-level performance') was never reached.
            if gap > 2.6 * max(local, 3.0) and gap > 1.5 * pitch:
                break
        r = clipped(rows[i])
        if not r:
            # An EMPTY row, once clipped, is the neighbour's line and costs
            # nothing: counting it stopped LIPNovo's Table 3 one row short of
            # its 'Amino Acid / Peptide / PTM' spanner. The floor and the gap
            # rule still bound the walk.
            continue
        if sum(1 for w in r if numeric(w["text"])) >= 2:
            break                      # another data block, not a header
        if len(r) > max(8, 3 * len(edges)):
            break                      # prose, not a header row
        # THE OWN-VERSUS-SPANNER DECISION USES DISTINCT COLUMN COVERAGE, not a
        # phrase count. Merging adjacent words on a 6 pt gap glued all eight of
        # AdaNovo's column headers into one 71-character phrase, because a
        # tightly-set header row leaves the same gap between two labels as
        # between two words of one label. That phrase then tripped the
        # long-phrase prose guard below and ended the walk, so a textbook
        # 1:1 table came back with no header at all.
        covered = {assign(w, edges) for w in r} - {None}
        # THE PROSE FILTER APPLIES TO THE SPANNER PATH ONLY. A row of column
        # headers is identified by how many distinct columns its WORDS cover,
        # and a tightly-set header row merges into one long phrase that the
        # prose test then rejects -- which skipped RefineNovo's Table 1 header
        # and lost 160 values. Words, not phrases, decide an own-header row.
        is_own_row = len(covered) >= max(2, int(0.8 * len(edges)))
        # An own-header row keeps ALL its phrases, so the stub and the
        # inside/outside split below still work; only the spanner path drops
        # prose, since that is the path a stray line of body text can corrupt.
        ph = (phrases(r) if is_own_row
              else [q for q in spanner_phrases(r) if not prosey(q["text"])])
        if not is_own_row and not ph:
            # Nothing but prose on this line, from the other column of a
            # two-column page. Skipped rather than ending the walk: LIPNovo's
            # PTM table has 'TheHC-PTdatasetcontainsmassspectraof' directly
            # above it, and breaking there meant neither of its two header
            # rows was ever read, so its dataset spanner was lost.
            taken += 1
            if taken >= max_rows:
                break
            continue

        inside = [q for q in ph if q["x1"] > edges[0][0]]
        # Prepended as a group in reading order, like the column headers: one
        # at a time reversed 'Species Method' into 'Method Species'.
        stub[0:0] = [q["text"] for q in sorted(ph, key=lambda q: q["x0"])
                     if q["x1"] <= edges[0][0]]
        if not inside:
            taken += 1
            if taken >= max_rows:
                break
            continue
        if is_own_row:
            # A row of column headers: assign the individual WORDS, so two
            # labels that a merge would have joined stay in their own columns.
            # Prepended as a GROUP, in reading order. The walk goes upward, so
            # each row goes in front of the rows below it -- but prepending
            # word by word also reversed the words WITHIN a row, which turned
            # InstaNovo-FM's 'IN-FM (fine-tuned)' into '(fine-tuned) IN-FM'
            # and 'IN v1.2' into 'v1.2 IN', neither of which resolves.
            row_own = collections.defaultdict(list)
            # A PHRASE THAT ENDS BEFORE THE DATA BEGINS heads the stub, not
            # column 0. The first column's interval is tiled leftwards past
            # its numbers, and CausalNovo's Table 3 headed column 0
            # 'Method Prec.' because 'Method' fell inside that overhang. The
            # test is on the whole PHRASE: InstaNovo-FM's 'IN-FM (fine-tuned)'
            # is wider than its numbers, so 'IN-FM' alone also ends early.
            stubbed: set[int] = set()
            if data_left is not None:
                ordered = sorted(r, key=lambda w: w["x0"])
                phrase: list[dict] = []
                for w in ordered + [None]:
                    if w is not None and phrase and w["x0"] - phrase[-1]["x1"] <= 4.0:
                        phrase.append(w)
                        continue
                    if phrase and max(x["x1"] for x in phrase) <= data_left:
                        stubbed.update(id(x) for x in phrase)
                    phrase = [w] if w is not None else []
            for w in sorted(r, key=lambda w: w["x0"]):
                if id(w) in stubbed:
                    continue
                k = assign(w, edges)
                if k is not None:
                    row_own[k].append(w["text"])
            for k, ws in row_own.items():
                own[k][0:0] = ws
        elif len(inside) == len(edges):
            # One phrase per column: unambiguous whatever the widths.
            for k, q in enumerate(sorted(inside, key=lambda q: q["x0"])):
                span[k].insert(0, q["text"])
        else:
            # A PHRASE THAT FITS INSIDE ONE COLUMN IS THAT COLUMN'S HEADER, not
            # a spanner over everything. PLMNovo's Table 2 puts 'Average' on
            # the row that also carries the stub labels, alone, and a lone
            # phrase was distributed to all ten columns by proximity -- which
            # put 'Average' in the subset of every cell and left two columns
            # with no header of their own, so they collided. A real spanner is
            # WIDER than a column by construction.
            own_wide = []
            row_own = collections.defaultdict(list)
            # ...UNLESS THE ROW IS A ROW OF METRIC SPANNERS. Glued text makes a
            # spanner narrow: pi-PrimeNovo's 'Aminoacidprecision' and
            # 'Peptiderecall' each fit inside one column, so 'Peptiderecall'
            # became the CasanovoV2 column's own header and that column could
            # not resolve. Two or more phrases that are ALL metric or level
            # names are spanners, however narrow.
            metric_row = len(inside) >= 2 and all(
                metric_of(q["text"]) or level_of(q["text"]) for q in inside)
            for q in sorted(inside, key=lambda q: q["x0"]) if not metric_row else []:
                k = assign(q, edges)
                fits = (k is not None and edges[k][0] - 1 <= q["x0"]
                        and q["x1"] <= edges[k][1] + 1)
                # The same stub test as for a row of column headers, applied
                # only to what would otherwise head a column: CausalNovo sets
                # 'Method' on its own line, 4 pt above 'Prec.'.
                if fits and data_left is not None and q["x1"] <= data_left:
                    stub.append(q["text"])
                    continue
                if fits:
                    row_own[k].append(q["text"])
                else:
                    own_wide.append(q)
            for k, ws in row_own.items():
                own[k][0:0] = ws
            inside = own_wide if not metric_row else inside
            if not inside:
                taken += 1
                if taken >= max_rows:
                    break
                continue
            # Fewer phrases than columns, so each governs a GROUP. Prefer the
            # rules: a partial rule just below this row states its extent.
            band = [(t, x0, x1) for t, x0, x1 in (rules or [])
                    if min(q["top"] for q in ph) < t < min(q["top"] for q in ph) + 26
                    and (x1 - x0) < (edges[-1][1] - edges[0][0]) * 0.95]
            by_rule: dict[int, str] = {}
            if len(band) >= 2:
                for k, c in enumerate(col_c):
                    seg = next((b for b in band if b[1] - 2 <= c <= b[2] + 2), None)
                    if seg is None:
                        continue
                    hit = [q for q in inside
                           if seg[1] - 2 <= (q["x0"] + q["x1"]) / 2 <= seg[2] + 2]
                    if len(hit) == 1:
                        by_rule[k] = hit[0]["text"]
            # A RULE-BASED MAPPING MUST COVER EVERY COLUMN OR BE DISCARDED.
            # Taking whatever it happened to match left GAPS, silently: on
            # LIPNovo's Table 1, whose header is a level over a dataset over a
            # metric, two of twelve columns got no dataset at all and the level
            # reached only four, so the dataset split could not fire and every
            # level collapsed onto the first one the caption named.
            if len(by_rule) == len(edges):
                for k, text in by_rule.items():
                    span[k].insert(0, text)
                    grouped.add(k)
            else:
                for k, c in enumerate(col_c):
                    nearest = min(inside, key=lambda q: abs((q["x0"] + q["x1"]) / 2 - c))
                    span[k].insert(0, nearest["text"])
                    ambiguous.add(k)
        taken += 1
        if taken >= max_rows:
            break
    return own, span, " ".join(stub).strip(), ambiguous - grouped


# Mixed-case NAMES that a camelCase split would break: 'HeLa' is a cell line,
# not 'He La'. Add to it when another one is met.
PROTECTED_CASE = ("HeLa",)


def unglue_label(text: str) -> str:
    """Re-space a label the text layer glued, for display and storage.

    Shared by the miner's subsets and the review page, so the two never
    disagree. InstaNovo's results table reads 'HeLasingle-shot', 'S.Brodae',
    'HeLadegradome' and 'Exc.Yeast' off the text layer.
    """
    keep = {}
    for i, name in enumerate(PROTECTED_CASE):
        # a protected name followed by a glued lowercase word gets its space,
        # and is shielded from the camelCase split by a placeholder
        text = re.sub(re.escape(name) + r"(?=[a-z])", name + " ", text)
        token = f"\x00{i}\x00"
        keep[token] = name
        text = text.replace(name, token)
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z][A-Za-z])", " ", text)
    out = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", out)
    # 'S.Brodae' -> 'S. Brodae' (an initial), 'Exc.Yeast' -> 'Exc. Yeast' (an
    # abbreviated word); 'Casa.V2' is untouched, since a digit follows the V.
    out = re.sub(r"(?<![A-Za-z])([A-Z])\.(?=[A-Za-z])", r"\1. ", out)
    out = re.sub(r"(?<=[a-z])\.(?=[A-Z][a-z])", ". ", out)
    # An OPENING quote glued to the word before it: InstaNovo's
    # 'Candidatus“Scalindua brodae”'. A closing quote stays attached.
    out = re.sub(r"(?<=[A-Za-z])(?=[\u201c\u2018])", " ", out)
    for token, name in keep.items():
        out = out.replace(token, name)
    return re.sub(r"\s+", " ", out).strip()


def unsquash_label(text: str) -> str:
    """Put the spaces back into a row or column LABEL for display and storage.

    A PDF producer that drops spaces turns 'Apis Mellifera' into
    'ApisMellifera', and a subset is read straight off the page, so the squash
    survives into the data. desquash() recovers it at the camelCase boundary.

    Applied ONLY to subset labels, never to a method name: 'pNovo' would become
    'p Novo'. And a label squashed with no case change, such as the printed
    'Clambacteria' for 'Clam bacteria', cannot be recovered this way and is
    left as the page has it.
    """
    # A LABEL WITH AN UNDERSCORE IS AN IDENTIFIER the paper chose, not a
    # squashed phrase: BiATNovo's preprint prints 'Precision_AAid(%)', and
    # splitting at its case change gave 'Precision_A Aid(%)'.
    if "_" in text:
        return text.strip()
    # NOT desquash(), which also serves matching and must split 'PeptideAUC'.
    # For a label it may split before a capital only when a letter follows:
    # 'ApisMellifera' and 'HelaQC' split, while a protease written 'GluC',
    # 'AspN' or 'LysC' keeps its trailing capital ('Glu C' was wrong).
    out = unglue_label(text)
    # An abbreviated genus loses the space after its initial too:
    # 'C.bacteria' -> 'C. bacteria', 'M.mazei' -> 'M. mazei'. Only after a
    # SINGLE capital, so 'Chymo.' and 'Prec.' are untouched.
    out = re.sub(r"(?<![A-Za-z])([A-Z])\.(?=[A-Za-z])", r"\1. ", out)
    return re.sub(r"\s+", " ", out).strip()


def desquash(text: str) -> str:
    """Re-insert the spaces a PDF producer dropped, at camelCase boundaries.

    Header text arrives collapsed often enough to matter, and a collapsed run
    defeats every `\b` anchor in the lexicons: 'PeptideAUC' does not match
    `\bAUC\b`, because the boundary it needs is between 'e' and 'A' and both
    are word characters. That is the same failure that hid PointNovo from
    CrossNovo's vocabulary, in a different place.

    'PeptideAUC' -> 'Peptide AUC', 'AminoAcidPrecision' -> 'Amino Acid
    Precision', 'PTMPrecision' -> 'PTM Precision'.
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)


def _forms(text: str) -> tuple[str, ...]:
    # An UNDERSCORE is a word character, so 'Precision_AAid(%)' defeats every
    # '\b' in the lexicons on both sides of it: BiATNovo's preprint labels its
    # metric rows that way. A fourth form reads it as a space.
    return (text, re.sub(r"\s+", "", text), desquash(text),
            text.replace("_", " "))


PROSE_TOKEN = re.compile(r"^[a-z][a-z.,;:]{9,}$")


# Words a sentence needs and a header row does not.
FUNCTION_WORDS = frozenset(
    "the a an and or of to in is are was were for with on by we our this that "
    "per as from at be".split())


def prosey(text: str) -> bool:
    """Is this phrase a run of prose rather than a header?

    A header phrase carries a capital or is short: 'Nine-species', 'Prec.',
    'AminoAcid-LevelPerformance'. A long all-lowercase token is a line of body
    text whose spaces the producer dropped. On LIPNovo's PTM table the
    neighbouring column's 'andevaluation.' was read as a spanner and became
    the subset of every cell, which stopped the dataset split firing.
    """
    t = text.strip()
    # A long unbroken token is prose too, whatever its case. The longest real
    # header phrase here is 'AminoAcid-LevelPerformance' at 26 characters.
    # A long phrase that opens in lower case is the tail of a sentence:
    # PLMNovo's caption ends 'in the 9-Species-v2 dataset.', glued to
    # 'inthe9-Species-v2dataset.', and was read as a spanner over every column.
    return (bool(PROSE_TOKEN.match(t)) or len(t) > 34
            or (t[:1].islower() and len(t) >= 16))


def metric_of(text: str) -> str | None:
    forms = _forms(text)
    for rx, m in METRIC_WORDS:
        if any(rx.search(f) for f in forms):
            return m
    return None


def level_of(text: str) -> str | None:
    forms = _forms(text)
    for rx, lv in LEVEL_WORDS:
        if any(rx.search(f) for f in forms):
            return lv
    return None


# A caption label is followed by a period, a colon, a pipe or a dash. A COMMA
# means a cross-reference inside a sentence -- 'Table2,LIPNovooutperforms
# AdaNovoby+8.7%' is prose, and read as a label it took the caption of the
# table below it.
# A THESIS NUMBERS ITS TABLES BY CHAPTER. Publication 196 prints 'Table 6.1:',
# which matched as 'Table 6' and left '1:' at the head of the caption, so the
# badge a reader quotes named a table the thesis does not have. The optional
# '.N' cannot swallow an ordinary 'Table 1. Comparison', because a digit has to
# follow the dot.
# ...WITH AN OPTIONAL 'Extended Data' / 'Supplementary' PREFIX. Nature-family
# papers keep their tables as 'Extended Data Table 2 |' and preprints bundle
# 'Supplementary Table 3.', and requiring the line to START with 'Table' meant
# InstaNovo's three Extended Data tables were never read as captions at all.
# The prefix may be glued on ('SupplementaryTable1'), as text layers drop spaces.
LABEL_ROW = re.compile(
    r"(?i)^\s*(?:(?:Extended\s*Data|Supplementary|Supplemental)\s*)?"
    r"Tab(?:le|\.)\s*(S?\d{1,2}(?:\.\d{1,2})?|[IVX]{1,4})\s*"
    r"(?=$|[.:|\u2013\u2014]|\s)")


def label_rows(rows: list[list[dict]]) -> list[int]:
    return [i for i, r in enumerate(rows)
            if LABEL_ROW.match(" ".join(w["text"] for w in r))]


def label_runs(rows: list[list[dict]]) -> list[tuple[int, float, float]]:
    """Every caption label on the page, as (row, x0, x1).

    A row can hold TWO labels, one per page column: LIPNovo's page 7 has
    'Table3. Leave-one-out cross validation compared to baseline on' beside
    'Table5. Component ablation'. Taking one label per row attributed the
    right-hand table's caption to the left-hand one.
    """
    out = []
    for i, r in enumerate(rows):
        for run in caption_runs(r):
            if LABEL_ROW.match(" ".join(w["text"] for w in run)):
                out.append((i, min(w["x0"] for w in run),
                            max(w["x1"] for w in run)))
    return out


# A FIGURE'S OWN CAPTION, which is a boundary a table caption cannot reach
# across. Publication 99's page 5 prints a nine-panel figure whose bar labels
# cluster into rows of numbers exactly as a table does, then Figure 4's
# caption, then Table 1 -- whose cells carry '(cid:78)' glyphs and so form no
# numeric block at all. The only block on the page was the CHART, and the only
# table label was Table 1's, so the chart was handed Table 1's caption and
# cropped together with the figure and two paragraphs of prose.
FIGURE_LABEL = re.compile(r"(?i)^\s*Fig(?:ure|\.)?\s*(S?\d{1,2}|[IVX]{1,4})\s*(?=$|[.:|]|\s)")


def figure_rows(rows: list[list[dict]]) -> list[int]:
    """Rows that begin a figure caption."""
    out = []
    for i, r in enumerate(rows):
        for run in caption_runs(r):
            if FIGURE_LABEL.match(" ".join(w["text"] for w in run)):
                out.append(i)
                break
    return out


def caption_runs(row: list[dict]) -> list[list[dict]]:
    """The row's words grouped into page columns, for reading captions.

    A GUTTER THRESHOLD ALONE IS NOT ENOUGH, and publication 17's page 7 is why:
    its two captions are single glued words, 'Table3.Leave-one-outcross...' and
    'Table5.Componentablation...', 18 pt apart -- a narrower gap than the 25 pt
    that separates a label from its own prose elsewhere, so they merged and the
    page reported one caption spanning both columns. Two different tables, Table
    3 and Table 5, then shared one label and one merged caption.

    So a run is also broken at any word that itself BEGINS a label. A second
    'Table N' on a row is a second caption by definition, which needs no
    measurement and cannot be defeated by a narrow gutter.
    """
    words = sorted(row, key=lambda w: w["x0"])
    runs: list[list[dict]] = []
    for w in words:
        starts = bool(LABEL_ROW.match(w["text"]))
        if runs and not starts and w["x0"] - runs[-1][-1]["x1"] <= 25.0:
            runs[-1].append(w)
        else:
            runs.append([w])
    return runs


def pair_parts(rows: list[list[dict]],
               parts: list[tuple[int, int, float, float]]) -> dict:
    """Pair each (block, column span) with its caption label, as (row, x0, x1).

    THE LABEL'S X-SPAN TRAVELS WITH IT, because a row can hold two of them.
    Returning the row alone was enough to pair correctly and still wrong
    downstream: publication 17's two page-7 captions share row 1, so
    caption_text re-derived which label that row meant and took the first,
    giving Table 5's table Table 3's label and caption.

    COLUMN FIRST, THEN ORDER. Two tables printed side by side have captions on
    the same rows, so distance alone cannot say which belongs to which; but
    within one page column, captions and tables both run down the page in
    order, whichever side of its table a house style puts the caption on.
    Using distance alone paired LIPNovo's first table with the caption of the
    SECOND, which sat two rows below it while its own was five rows above.
    """
    # A label is discarded as being INSIDE a table only if it is inside that
    # table's rows AND its width. A side-by-side neighbour's caption sits
    # within the other table's rows and in another column: PhysNovo's Table 6
    # caption (right column) falls inside Table 3's rows (left), was dropped
    # here, and Table 6 was paired with a line of prose further down instead.
    runs = [r for r in label_runs(rows)
            if not any(lo <= r[0] <= hi and min(r[2], b) - max(r[1], a) > 0
                       for lo, hi, a, b in parts)]
    # Cluster the parts into page columns by x overlap.
    cols: list[dict] = []
    for part in sorted(parts, key=lambda t: t[2]):
        _lo, _hi, x0, x1 = part
        for c in cols:
            if min(c["x1"], x1) - max(c["x0"], x0) > 0:
                c["parts"].append(part)
                c["x0"], c["x1"] = min(c["x0"], x0), max(c["x1"], x1)
                break
        else:
            cols.append({"x0": x0, "x1": x1, "parts": [part]})

    # A LABEL BELONGS TO THE COLUMN IT MOSTLY SITS IN, and to one only. Taking
    # every label that merely touches a column, or starts within 30 pt of it,
    # made publication 17's three labels all candidates for its left column:
    # Table 5's caption begins 57 pt inside the left column's span, so the
    # left column saw three captions for two tables, the count no longer
    # agreed, and the nearest-distance fallback handed the first table the
    # caption of Table 4 further down the page. Each label is assigned once,
    # by overlap fraction, which is a measurement of the page rather than a
    # tolerance.
    if not cols:
        return {}
    # A TABLE CAPTION CANNOT REACH ACROSS A FIGURE'S CAPTION. See FIGURE_LABEL.
    figs = figure_rows(rows)

    def blocked(part, run) -> bool:
        a, b = min(part[0], run[0]), max(part[1], run[0])
        return any(a < f < b for f in figs)

    assigned: dict[int, list] = {i: [] for i in range(len(cols))}
    for r in runs:
        width = max(r[2] - r[1], 1.0)
        best, best_frac = None, 0.0
        for i, c in enumerate(cols):
            frac = max(0.0, min(r[2], c["x1"]) - max(r[1], c["x0"])) / width
            if frac > best_frac:
                best, best_frac = i, frac
        if best is None:
            # No overlap with any column: the nearest one by x takes it.
            best = min(range(len(cols)),
                       key=lambda i: min(abs(cols[i]["x0"] - r[2]),
                                         abs(r[1] - cols[i]["x1"])))
        assigned[best].append(r)

    out: dict = {}
    # SIDE BY SIDE IS ORDERED LEFT TO RIGHT, not top to bottom. A full-width
    # table merges both page columns into one, and within it captions and
    # tables are paired in reading order; for two tables that share rows,
    # reading order is x, and ordering them by their first data row instead
    # swapped CausalNovo's Table 2 and Table 3, once Table 2 correctly started
    # four rows below Table 3. Parts that overlap vertically form one band,
    # ordered by x; labels on one row are ordered by x likewise.
    def _reading_order(parts_):
        parts_ = sorted(parts_, key=lambda t: t[0])
        bands: list[list] = []
        for pt in parts_:
            if bands and pt[0] <= max(q[1] for q in bands[-1]):
                bands[-1].append(pt)
            else:
                bands.append([pt])
        return [pt for b in bands for pt in sorted(b, key=lambda t: t[2])]

    for ci, c in enumerate(cols):
        cand = list(assigned[ci])
        cand.sort(key=lambda r: (r[0], r[1]))
        mine = _reading_order(c["parts"])
        cand = [r for r in cand if any(not blocked(part, r) for part in mine)]
        if len(cand) == len(mine) and cand:
            if all(not blocked(part, run) for part, run in zip(mine, cand)):
                for part, run in zip(mine, cand):
                    out[(part[0], part[1], part[2])] = run
                continue
        # A CAPTION BELONGS TO ONE TABLE, and labels and tables both run down
        # the column in order. Handing the same label to every part near it
        # gave a FIGURE the caption of the table above it; claiming labels
        # greedily, nearest first, was the next fault: PhysNovo's right column
        # has Table 5's caption, Table 6's, and much lower a line of prose that
        # starts 'Table6.' at a line break, and greed let the parts steal each
        # other's labels until Table 6 took the prose. Labels and parts are now
        # ALIGNED in order -- each label used once, a part may go without one
        # (it is then a figure), total distance minimised -- which is the
        # reading of a column a person does.
        INF, SKIP = float("inf"), 25.0
        nl, np_ = len(cand), len(mine)
        dist = [[(INF if blocked(mine[j], cand[i]) else
                  min(abs(cand[i][0] - mine[j][0]), abs(cand[i][0] - mine[j][1])))
                 for j in range(np_)] for i in range(nl)]
        dp = [[INF] * (np_ + 1) for _ in range(nl + 1)]
        back: dict = {}
        dp[0][0] = 0.0
        for i in range(nl + 1):
            for j in range(np_ + 1):
                if dp[i][j] == INF:
                    continue
                if i < nl and dp[i][j] < dp[i + 1][j]:            # label unused
                    dp[i + 1][j], back[(i + 1, j)] = dp[i][j], ("L", i, j)
                if j < np_ and dp[i][j] + SKIP < dp[i][j + 1]:     # part uncaptioned
                    dp[i][j + 1], back[(i, j + 1)] = dp[i][j] + SKIP, ("P", i, j)
                if i < nl and j < np_ and dist[i][j] < INF and \
                        dp[i][j] + dist[i][j] < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = dp[i][j] + dist[i][j]
                    back[(i + 1, j + 1)] = ("M", i, j)
        chosen: dict = {}
        i, j = nl, np_
        while (i, j) in back:
            kind, pi_, pj_ = back[(i, j)]
            if kind == "M":
                chosen[pj_] = pi_
            i, j = pi_, pj_
        for pi, part in enumerate(mine):
            ri = chosen.get(pi)
            out[(part[0], part[1], part[2])] = cand[ri] if ri is not None else None
    return out


def pair_captions(rows: list[list[dict]], blocks: list[tuple[int, int]]) -> dict:
    """Assign each table block its own caption, by ORDER rather than distance.

    Nearest-label-wins is wrong whenever two tables share a page, and this
    literature does that constantly. On CrossNovo's page 8 the caption of
    Table 1 sits 3 rows above its block while the caption of Table 2 sits 1
    row below it, so 'nearest' gave Table 1's numbers Table 2's caption, which
    would have filed a 9-species-v1 result as 9-species-v2. That is the
    nine-species trap committed as data, from a one-line heuristic.

    Captions and blocks both run down the page in order, whichever side of its
    table a house style puts the caption on, so when the counts agree the
    i-th label belongs to the i-th block. That holds for 'caption above'
    (ACL, ICLR) and 'caption below' (most journals) alike, and only falls back
    to nearest when the counts disagree.
    """
    labels = [i for i in label_rows(rows)
              if not any(lo <= i <= hi for lo, hi in blocks)]
    out: dict[tuple[int, int], int | None] = {}
    if len(labels) == len(blocks) and labels:
        for blk, li in zip(blocks, labels):
            out[blk] = li
        return out
    for blk in blocks:
        lo, hi = blk
        cand = sorted(labels, key=lambda i: min(abs(i - lo), abs(i - hi)))
        out[blk] = cand[0] if cand else None
    return out


def assemble_lines(band: list[dict]) -> str:
    """Words, bucketed into lines and joined, with line-break hyphens healed.

    LaTeX hyphenates freely, so 'identify' arrives as 'iden-' / 'tify' and a
    caption read '...models to iden- tify Post-Translational'. The join applies
    only where the hyphen is the last thing on ITS line, which the buckets
    know, so a mid-line compound such as 'Post-Translational' is untouched. A
    compound that breaks AT its own hyphen still loses it, which is inherent:
    the PDF does not record whether the hyphen was already there.
    """
    if not band:
        return ""
    # A SUPERSCRIPT IS NOT ITS OWN LINE. Bucketing on `top` put a raised
    # marker one bucket early, so a footnote came out '+ The best entry is in
    # bold. Indicates the filtered...' with its own marker moved to the front,
    # and publication 17's caption read '...in amino acid-level and † peptide-
    # level performance. denotes our retrained results'. The lines are built
    # from the full-height words, and anything shorter is attached to the line
    # whose centre is nearest, which is where it was printed.
    heights = sorted(w["bottom"] - w["top"] for w in band)
    h = heights[len(heights) // 2] or 1.0
    tall = [w for w in band if (w["bottom"] - w["top"]) >= 0.8 * h]
    small = [w for w in band if (w["bottom"] - w["top"]) < 0.8 * h]
    rows_: list[list[dict]] = []
    for w in sorted(tall or band, key=lambda w: w["top"]):
        if rows_ and abs(w["top"] - rows_[-1][0]["top"]) <= 0.6 * h:
            rows_[-1].append(w)
        else:
            rows_.append([w])
    for w in small:
        mid = (w["top"] + w["bottom"]) / 2.0
        near = min(rows_, key=lambda L: abs(
            mid - sum((x["top"] + x["bottom"]) / 2.0 for x in L) / len(L)),
            default=None)
        (near if near is not None else rows_.append([w]) or rows_[-1]).append(w)
    lines = [[w["text"] for w in sorted(L, key=lambda w: w["x0"])]
             for L in rows_]
    out = ""
    for line in lines:
        text = re.sub(r"\s+", " ", " ".join(line)).strip()
        if out.endswith("\x00"):
            out = out[:-1] + text
        elif out:
            out = out + " " + text
        else:
            out = text
        if out.endswith("-"):
            out = out[:-1] + "\x00"
    return out.replace("\x00", "-").strip()


CID = re.compile(r"\(cid:\d+\)")


def caption_text(rows: list[list[dict]], paired: tuple | None,
                 blocks: list[tuple[int, int]],
                 fine: list[dict] | None = None,
                 page_width: float = 612.0) -> tuple[str, str, int]:
    """The label, the caption prose at row `li`, and the caption's LAST row.

    Two things make a caption harder to read than it looks.

    ITS WORDS COME FROM A FINER EXTRACTION. At the default word tolerance a
    caption arrives with its spaces gone -- 'Thecomparisonofdenovopeptide' --
    because these producers leave an inter-word gap narrower than 3 pt. At
    x_tolerance=1.6 the same line reads 'The comparison of de novo peptide'.
    1.6 rather than 2 because a JUSTIFIED caption line is set tighter still:
    publication 4's third line stayed glued at 2 and separates at 1.6, which
    is also where it stops changing -- 1.6, 1.3 and 1.0 give the same words on
    that page, so the floor is the producer's kerning and not a tuned value.
    The PARSING geometry still uses the default, since a tighter tolerance
    would split 'M.mazei' into two words and change which column a cell lands
    in; only the caption's text is re-read.

    IT MUST BE CLIPPED TO ITS OWN COLUMN. On a two-column page the other
    column's lines sit at the same heights, so joining rows wholesale
    interleaved them: publication 4's caption came out as 'Performance after
    training the models on the thenine-speciesV2dataset; theresultsshownin
    Table3. MassIVE-KBset. Herewetested...' -- the right words in the wrong
    order, mixed with a neighbouring paragraph. The label row gives the
    column: a caption that spans most of the page is single-column, otherwise
    its own first line bounds it.

    The last row matters to the header walk, which must not read the caption.
    """
    if paired is None:
        return "", "", -1
    li, pair_x0, pair_x1 = paired
    text = " ".join(w["text"] for w in sorted(rows[li], key=lambda w: w["x0"])
                     if pair_x0 - 1 <= w["x0"] and w["x1"] <= pair_x1 + 1) \
        or " ".join(w["text"] for w in rows[li])
    m = LABEL_ROW.match(text)
    label = m.group(0).strip() if m else ""
    # RESERVE THE ROW DIRECTLY ABOVE EACH DATA BLOCK for the header. Without
    # that, a caption's continuation lines ran on into the header row and
    # swallowed it, which moved the header walk's floor past the only header
    # there was and rejected the table for having none.
    stop = len(rows)
    for lo, _ in blocks:
        if lo > li:
            stop = min(stop, lo - 1)
    # THE TABLE BELOW A CAPTION IS ITS LOWER BOUND, and where there is one
    # every line in between is caption. A fixed three-line window cut
    # publication 4's Table 3 short of its last sentence, which happened to be
    # the one naming the models ('The models tested are Base and PA, alongside
    # Casanovo's reported numbers'): the two columns interleave, so three ROWS
    # is barely two caption LINES. Where no table follows -- the caption sits
    # below its own table, and body prose runs on underneath with no gap to
    # stop at -- the conservative window stays.
    runs = caption_runs(rows[li])
    own_run = next((r for r in runs
                    if LABEL_ROW.match(" ".join(x["text"] for x in r))
                    and min(x["x0"] for x in r) <= pair_x1
                    and max(x["x1"] for x in r) >= pair_x0), runs[0])
    lx0 = min(w["x0"] for w in own_run)
    lx1 = max(w["x1"] for w in own_run)
    right = lx1 + 6
    limit = min(li + 4, stop) if stop >= len(rows) or stop - li > 14 else stop
    last = li
    # A PARAGRAPH GAP ENDS THE CAPTION. Its own lines sit one line pitch apart
    # and the body text under it starts a paragraph further down, which is the
    # one signal that works whichever side of the table the caption is on.
    # Publication 16's Table 1 caption is three lines at an 11.0 pt pitch and
    # the next paragraph begins 32.1 pt below the last of them, so without this
    # the caption ran on into 'clude DeepNovo (Tran et al., 2017), which
    # integrates bedding dimensions, attention heads count, and learning' --
    # two columns of body prose interleaved, which is what the band returns
    # when a caption spans the full page width and nothing stops the walk.
    prev_top = min(w["top"] for w in rows[li])
    pitch = None
    for j in range(li + 1, limit):
        nxt = rows[j]
        if sum(1 for w in nxt if numeric(w["text"])) >= 2:
            break
        if LABEL_ROW.match(" ".join(w["text"] for w in nxt)):
            break
        # A HEADER ROW IS NOT PROSE, and once the table below bounds the walk
        # rather than a line count, the walk reaches it: publication 4's
        # caption ran on into 'TEST SET NINE-SPECIES V2 MODEL BASE PA
        # CASANOVO'. Reserving one row for the header is not enough, because a
        # spanner gives a table two.
        #
        # TWO SHAPES OF HEADER ROW, and each needs its own test.
        #
        # ALL CAPS: publication 4's 'TEST SET NINE-SPECIES V2' and 'MODEL BASE
        # PA CASANOVO' carry no lowercase letter at all, which no sentence
        # does.
        #
        # COLUMN-ALIGNED: publication 18's 'ID Clam Human Honey Mouse Tomato
        # M.mazei Bacillus Yeast Rice Average' is capitalised English, so the
        # first test passes it. What gives it away is the geometry: its words
        # sit one per column with the column pitch between them, while a
        # caption's words are a line of prose a few points apart. Three or
        # more words whose median gap is over 15 pt is a header.
        #
        # Requiring a word to START lowercase was tried instead of both and is
        # wrong: publication 4's caption line is two glued words, 'MassIVE-KB
        # set.' and 'Here we tested on each of the species in the', and both
        # begin with a capital, so the caption lost its last two sentences.
        # An UNMAPPED GLYPH ('(cid:100)') is not a word of the line: InstaNovo's
        # caption sets the hats of two 'ŝe_B's on a line of their own, at x 183
        # and 225, and that line read as an indented header row and ended the
        # caption one sentence early.
        # A BARE CENTRED LABEL OVER A WIDER CENTRED CAPTION. IEEE sets
        # 'TABLE V' alone on a centred line and the caption, wider, centred
        # under it: SeqNovo's label sits at x 290 and its caption starts at x
        # 190, so a band the width of the label held none of the caption. The
        # first line under a bare label is taken whole when it is centred on
        # the label, and the band widens to it.
        bare = last == li and re.fullmatch(
            r"(?i)table\s*[IVXLC\d]+[.:]?", " ".join(w["text"] for w in own_run).strip())
        if bare:
            cx = (lx0 + lx1) / 2
            row_ = sorted(nxt, key=lambda w: w["x0"])
            runs_, cur_ = [], []
            for w in row_:
                if cur_ and w["x0"] - cur_[-1]["x1"] > 15.0:
                    runs_.append(cur_)
                    cur_ = []
                cur_.append(w)
            if cur_:
                runs_.append(cur_)
            for run_ in runs_:
                r0, r1 = run_[0]["x0"], max(w["x1"] for w in run_)
                if r0 <= cx <= r1 and abs((r0 + r1) / 2 - cx) < 25.0:
                    lx0, right = min(lx0, r0), max(right, r1 + 6)
        mine = sorted((w for w in nxt if lx0 - 6 <= w["x0"] <= right
                       and not CID.fullmatch(w["text"].strip())),
                      key=lambda w: w["x0"])
        # A SUB- OR SUPERSCRIPT ON A LINE OF ITS OWN is stepped over, not taken
        # for an indented header row: Pairwise's caption reads 'Casanovo_bm',
        # and the 'bm' sits alone at x 184, which ended the caption before its
        # last sentence. At most two words of at most three characters each.
        if mine and len(mine) <= 2 and all(len(w["text"].strip()) <= 3 for w in mine):
            continue
        # BODY PROSE UNDER A CENTRED ONE-LINE CAPTION. With a single caption
        # line there is no pitch yet for the paragraph-gap test, and ReNovo's
        # 'Table 8: Performance Comparison on Filtered Test Datasets.' ran on
        # into fragments of the paragraph beneath it, which also clipped the
        # crop on the prose's right edge. Once the caption read so far ends a
        # sentence, a line with a word STRADDLING the caption's left edge is
        # the text block, not the caption: a caption's own continuation lines
        # start at that edge, and the other column's text ends before the
        # gutter rather than running across it.
        so_far = " ".join(w["text"] for i in range(li, last + 1) for w in rows[i]
                          if lx0 - 6 <= w["x0"] <= right).rstrip()
        if so_far.endswith((".", "!", "?")) and any(
                w["x0"] < lx0 - 6 < w["x1"] for w in nxt):
            break
        if mine:
            # Measured over the caption's OWN lines only: a row carrying just
            # the other column's text has to be stepped over and says nothing
            # about this caption's spacing.
            delta = min(w["top"] for w in mine) - prev_top
            if pitch is not None and delta > max(1.8 * pitch, pitch + 6.0):
                break
            if pitch is None and 2.0 < delta < 40.0:
                pitch = delta
            prev_top = min(w["top"] for w in mine)
        # A CAPTION IS FLUSH LEFT; A HEADER ROW IS INDENTED TO ITS COLUMN.
        # Publication 17's Table 4 caption is one line and the three header
        # rows under it start 92, 4 and 90 pt right of it, so the caption came
        # out '...(Mao et al., 2023). Method # Params Peptide Level Amino Acid
        # Level Recall AUC Recall Prec.'. Every genuine continuation line
        # measured here starts within 0.5 pt of its caption's own left edge.
        # The threshold is 20 pt rather than a few, so a house style with a
        # hanging indent under the label still reads as a caption.
        if mine and min(w["x0"] for w in mine) > lx0 + 20.0:
            break
        # AN IEEE CAPTION IS IN SMALL CAPITALS on the line under a bare
        # 'TABLE V', and so carries no lower-case letter: 'T HE B EST R ESULTS
        # (ACCURACY ) OF F OUR M ODELS ON T ESTSET'. The all-caps test took it
        # for a header row and SeqNovo lost all three captions. Where the label
        # line holds the label and nothing else, the first line under it is the
        # caption whatever its case; the column-aligned test below still
        # catches a header row.
        if mine and not bare and not any(
                c.islower() for c in " ".join(w["text"] for w in mine)):
            break
        gaps = [b["x0"] - a["x1"] for a, b in zip(mine, mine[1:])]
        if len(mine) >= 3 and statistics.median(gaps) > 15.0:
            break
        # A CAPTION'S EXTENT IS SET BY ITS OWN LINES. A row holding only the
        # OTHER column's text has to be walked over, since the two columns
        # interleave, but it must not extend the caption's bottom: publication
        # 222's Table 3 caption ends at y 409, the walk crossed two right-column
        # rows to y 437, and the band that wide then swept in a figure's axis
        # tick, so the caption ended '...respectively. 12000'.
        if mine:
            last = j

    # THE COLUMN COMES FROM THE GUTTER BESIDE THE LABEL WORD, not from the
    # width of the label's row. On a two-column page that row already holds
    # BOTH columns, so its width said "single column" and nothing was clipped:
    # DiffuNovo's Table 3 caption came out as 'ablated models on the HC-PT
    # dataset. All other settings Table 3. Empirical comparison of PTM
    # identification. We were k...'. The label row's words are grouped into
    # runs separated by a gutter, and the run holding 'Table N' is the caption's
    # own column.
    top = min(w["top"] for w in rows[li]) - 1
    bottom = max(w["bottom"] for w in rows[last]) + 1

    words = fine if fine else [w for i in range(li, last + 1) for w in rows[i]]
    band = [w for w in words
            if top <= w["top"] <= bottom and lx0 - 6 <= w["x0"] <= right]
    # LINES ARE BUCKETED, not sorted on the exact top. A label word's baseline
    # sits a fraction off its own line's -- bold, or a different size -- and
    # sorting on the exact value interleaved it with the line below, so
    # publication 4's caption read 'Performance after training the models on
    # the Table 3. MassIVE-KB set.' A 2 pt bucket keeps a line together.
    band.sort(key=lambda w: (round(w["top"] / 2.0), w["x0"]))
    # A HYPHEN AT THE END OF A LINE IS A LINE BREAK, not punctuation. LaTeX
    # hyphenates a caption freely, so 'identify' arrives as 'iden-' / 'tify'
    # and the caption read '...models to iden- tify Post-Translational'. The
    # join is only applied where the hyphen is the last thing on ITS LINE,
    # which the buckets already know, so a mid-line compound such as
    # 'Post-Translational' is untouched. A compound that happens to break AT
    # its own hyphen loses it, which is inherent: the PDF does not record
    # whether the hyphen was already there.
    cap = assemble_lines(band)
    # The finer extraction spaces the label differently from the coarse one
    # ('Table 2:' against 'Table2'), so it is stripped by pattern rather than
    # by the exact string.
    cap = LABEL_ROW.sub("", cap, count=1)
    cap = re.sub(r"^\s*[.:|\u2013\u2014]\s*", "", cap).strip()
    # An UNMAPPED GLYPH becomes '\ufffd', the replacement character: what it
    # stood for cannot be recovered here, but deleting it lost meaning --
    # pi-HelixNovo's 'The (cid:78) denotes deterioration ratio' read 'The
    # denotes ...' -- so the caption says visibly that a symbol was there.
    # CAPTION_OVERRIDE holds captions corrected by hand.
    cap = re.sub(r"\s{2,}", " ", CID.sub("\ufffd", cap)).strip()
    # SMALL CAPITALS ARRIVE SPLIT: each word's larger first capital is its own
    # glyph run, so 'THE BEST' reads 'T HE B EST' and '(ACCURACY)' reads
    # '(ACCURACY )'. Only in a caption with no lower-case letter at all, so an
    # ordinary 'a' or 'A' starting a word in running text is never glued on.
    if cap and not any(c.islower() for c in cap):
        cap = re.sub(r"\b([A-Z]) (?=[A-Z]{2,})", r"\1", cap)
        cap = re.sub(r"\s+([)\],.;:])", r"\1", cap)
    return label, cap, last


# A TABLE'S FOOTNOTE DEFINES ITS MARKERS, and without it a cell reading
# '0.550/0.530*/0.664+' is three numbers and no explanation. DiffNovo's two
# tables carry 'The best entry is in bold. * Indicates the positional accuracy
# reported in [10].' and '+ Indicates the filtered peptide-level accuracy, and
# * indicates the peptide-level accuracy reported in [10].' -- which is what
# says one of those numbers was computed by somebody else, i.e. the basis.
FOOTNOTE_CUE = re.compile(
    r"(?i)(?:^|\s)[*\u2217+\u2020\u2021\u00a7\u00b6]\s*"
    r"(?:indicates?|denotes?|means?|marks?|stands|refers|is|are|the)\b"
    r"|^\s*(?:note|notes|the best|best (?:results?|entry|values?)|bold|"
    r"underlined?|scores? for|\u2013 ?indicates)\b")


def footnote_text(rows: list[list[dict]], hi: int, fine: list[dict] | None,
                  x0: float, x1: float) -> str:
    """The marker-defining note printed under the block, or ''.

    Found rather than assumed: the first row within four of the block's end
    whose text reads like a marker definition, then its continuation lines
    while they keep the same pitch. Four rather than one, because a wrapped
    cell can sit between the table and its note -- DiffNovo's page 7 puts a
    lone '0.725*' there, the overflow of the cell above it.
    """
    start = None
    for j in range(hi + 1, min(hi + 5, len(rows))):
        text = " ".join(w["text"] for w in rows[j])
        if LABEL_ROW.match(text):
            break
        if FOOTNOTE_CUE.search(text):
            start = j
            break
    if start is None:
        return ""
    last, prev_top = start, min(w["top"] for w in rows[start])
    pitch = None
    for j in range(start + 1, min(start + 5, len(rows))):
        text = " ".join(w["text"] for w in rows[j])
        if LABEL_ROW.match(text) or sum(1 for w in rows[j]
                                        if numeric(w["text"])) >= 2:
            break
        top = min(w["top"] for w in rows[j])
        delta = top - prev_top
        if pitch is not None and delta > max(1.8 * pitch, pitch + 6.0):
            break
        if pitch is None and 2.0 < delta < 40.0:
            pitch = delta
        prev_top, last = top, j
    top = min(w["top"] for w in rows[start]) - 1
    bottom = max(w["bottom"] for w in rows[last]) + 1
    words = fine if fine else [w for i in range(start, last + 1) for w in rows[i]]
    band = [w for w in words if top <= w["top"] <= bottom
            and x0 - 8 <= w["x0"] and w["x1"] <= x1 + 8]
    return assemble_lines(band)


METRIC_TOKEN = re.compile(r"[A-Z]{2,}(?=[A-Z][a-z]|$)|[A-Z][a-z]+\.?")


def split_metric_runs(words: list[dict]) -> list[dict]:
    """Split a header word that is several metric names glued together.

    LIPNovo+'s Table 2 sets its twelve metric headers so tightly that the text
    layer returns one word, 'PrecisionRecallPrecisionRecall...PrecisionAUC',
    spanning every column, so every column took the same metric and G6 fired.
    Each token is cut out at its own characters' x, which is exact, and only
    when the whole word is three or more metric names and nothing else.
    """
    out = []
    for w in words:
        chars = w.get("chars") or []
        toks = list(METRIC_TOKEN.finditer(w["text"]))
        if (len(toks) >= 3 and len(chars) == len(w["text"])
                and "".join(t.group() for t in toks) == w["text"]
                and all(metric_of(t.group()) for t in toks)):
            for t in toks:
                cs = chars[t.start():t.end()]
                out.append({**w, "text": t.group(), "chars": cs,
                            "x0": min(c["x0"] for c in cs),
                            "x1": max(c["x1"] for c in cs)})
            continue
        out.append(w)
    return out


GLUED_YEAR = re.compile(r"^(.*[^\d\s])((?:19|20)\d{2})$")


def split_glued_year(row: list[dict], edges: list[tuple[float, float]]) -> list[dict]:
    """Split a year that the PDF glued onto its row label.

    LIPNovo+'s Table 2 prints every method with its year in a column of its
    own, except the last row, whose text layer reads 'LIPNovo+(Ours)2026' as
    one word. That row came up one cell short and refused the whole table as
    ragged. The year is cut off only where its estimated position lands in a
    column that this row has no other word in, so a name that merely ends in
    digits is left alone.
    """
    out = []
    for w in row:
        m = GLUED_YEAR.match(w["text"])
        # A NUMBER IS NEVER A LABEL WITH A YEAR ON IT. '0.1983', PLMNovo's
        # classification loss, matched as '0.' plus the year 1983, and every
        # loss of the form 0.19xx split into two cells in one column, which
        # refused Table 1 as ragged.
        if m and len(m.group(1)) >= 2 and numeric(w["text"]) is None:
            frac = len(m.group(1)) / len(w["text"])
            cut = w["x0"] + (w["x1"] - w["x0"]) * frac
            year = {**w, "text": m.group(2), "x0": cut}
            k = assign(year, edges)
            if k is not None and not any(
                    x is not w and assign(x, edges) == k for x in row):
                out += [{**w, "text": m.group(1), "x1": cut}, year]
                continue
        out.append(w)
    return out


def extract(page, pub_id: int | None = None) -> tuple[list[dict], list[dict], int]:
    """Parse every table on a page.

    Returns (tables, vetoed, uncaptioned). Each is per BLOCK, because a page
    routinely holds several and collapsing them into one page-level verdict
    threw away the caption of everything that failed, which is the column of
    the audit CSV a human reviews from.

    `uncaptioned` is a COUNT, not a list. A numeric block with no 'Table N'
    anywhere near it is almost always a figure: the axis ticks and legend of a
    multi-panel Nature figure cluster into rows of numbers exactly as a table
    does. Measured over this library, 621 of 710 numeric blocks are of that
    kind, so listing them would bury the 89 real tables in the worksheet.
    """
    # A DIAGONAL WATERMARK IS NOT TEXT ON THE PAGE. SSRN stamps "This preprint
    # research paper has not been peer reviewed" across every page in 74 pt
    # grey Helvetica at about 52 degrees, and its letters arrive one by one at
    # whatever height they cross the table: LIPNovo+'s Table 2 read AdaNovo's
    # '0.379' as '0p.379', pushed it onto a row of its own and refused the
    # table as ragged. Body text is never set diagonally -- a landscape table
    # is rotated by a right angle, which leaves a zero on the matrix diagonal
    # -- so a character with both terms non-zero is dropped.
    page = page.filter(lambda o: o.get("object_type") != "char" or not (
        abs(o["matrix"][1]) > 0.05 and abs(o["matrix"][0]) > 0.05))
    words = split_metric_runs(strip_line_numbers(collapse_fake_bold(
        page.extract_words(use_text_flow=False, keep_blank_chars=False,
                           return_chars=True))))
    if not words:
        return [], [], 0
    rows = word_rows(words)
    # A second, finer pass used ONLY for caption text. See caption_text().
    try:
        fine = strip_line_numbers(collapse_fake_bold(page.extract_words(
            use_text_flow=False, keep_blank_chars=False, x_tolerance=1.6)))
    except Exception:
        fine = None
    rules = page_rules(page)
    bold_words, under_rules = page_emphasis(page)
    out, vetoed, uncaptioned = [], [], 0
    blocks = [b for b in data_blocks(rows) if len(column_edges(rows, *b)) >= 2]
    # EACH COLUMN GROUP IS ITS OWN TABLE. Rows span the page, so two tables
    # printed side by side arrive as one block; column_groups() finds the
    # gutter and each group is extracted separately, with its own caption.
    parts: list[tuple[int, int, list[tuple[float, float]]]] = []
    # THE PAGE'S OWN COLUMN SPLIT, where it states one: two captions on one
    # row are the two page columns, and the boundary between them is the
    # gutter. PhysNovo's page 8 sets Table 3 beside Tables 5 and 6, and the
    # numeric gutter was too narrow to see -- Table 3 ends at x 282 and Table
    # 6's length-error column ('0', '1', '>=2') starts at 313 -- so all three
    # came out as one twelve-column block, refused as ragged and labelled
    # with the Table 4 caption below it.
    # A gutter governs only the block its caption row CAPTIONS: the label row
    # nearest above the block, or one inside it. Publication 64's page 6 sets
    # Tables 2 and 3 side by side BELOW a full-width Table 1, and applying
    # their gutter page-wide cut Table 1 in half.
    # The test uses where a column's NUMBERS sit, never its tiled interval:
    # tiling stretches a column to the next one's midpoint, so LIPNovo's last
    # Table 3 column (numbers at x 271-284) was tiled to 364, its centre fell
    # past the 298 gutter and it was cut off as a one-column table.
    runs_all = label_runs(rows)
    label_rows = sorted({r[0] for r in runs_all})
    def gutters_for(lo, hi):
        above = [r for r in label_rows if r < lo]
        mine = ([above[-1]] if above else []) + [r for r in label_rows if lo <= r <= hi]
        out = []
        for r in mine:
            same = sorted((q for q in runs_all if q[0] == r), key=lambda q: q[1])
            out += [(a[2] + b[1]) / 2 for a, b in zip(same, same[1:])]
        return out
    for lo, hi in blocks:
        full = column_edges(rows, lo, hi)
        gutters = gutters_for(lo, hi)
        def centre(k):
            xs = [(w["x0"] + w["x1"]) / 2 for i in range(lo, hi + 1) for w in rows[i]
                  if numeric(w["text"]) and full[k][0] <= (w["x0"] + w["x1"]) / 2 < full[k][1]]
            return statistics.median(xs) if xs else (full[k][0] + full[k][1]) / 2
        for grp in column_groups(full):
            cur: list[int] = []
            for k in grp:
                c = centre(k)
                if cur and any(centre(cur[-1]) < g < c for g in gutters):
                    parts.append((lo, hi, [full[j] for j in cur]))
                    cur = []
                cur.append(k)
            if cur:
                parts.append((lo, hi, [full[j] for j in cur]))
    # EACH SIDE-BY-SIDE TABLE STARTS AT ITS OWN FIRST ROW OF NUMBERS. Blocks
    # are built from whole rows, so two tables printed side by side share one
    # block from the higher one's first data row to the lower one's last.
    # CausalNovo's Table 3 (right) starts four rows above its Table 2 (left),
    # which put Table 2's whole header -- 'Nine-species Seven-species HC-PT',
    # 'Prec. Recall ...' -- INSIDE the block, where the header reader never
    # looks; it read Table 3's header above the block instead and refused
    # Table 2 for having no metric. Each part is trimmed to the rows carrying
    # at least two numbers inside its own columns.
    def _trim(lo, hi, e):
        x0, x1 = e[0][0], e[-1][1]
        def has(i):
            return sum(1 for w in rows[i] if numeric(w["text"])
                       and x0 - 2 <= (w["x0"] + w["x1"]) / 2 <= x1 + 2) >= 2
        nlo = next((i for i in range(lo, hi + 1) if has(i)), lo)
        nhi = next((i for i in range(hi, lo - 1, -1) if has(i)), hi)
        return (nlo, nhi, e) if nlo <= nhi else (lo, hi, e)
    # Remember each part's BLOCK OF ORIGIN: side-by-side tables are neighbours
    # because they came out of one block, and trimming each to its own rows
    # can leave them with no rows in common.
    origin: dict[tuple, tuple] = {}
    trimmed = []
    for pt in parts:
        tp = _trim(*pt) if len(parts) > 1 else pt
        # A CAPTION INSIDE A PART'S ROWS, in its own column, separates two
        # tables stacked in that column: PhysNovo's right column holds Table 5
        # and then Table 6, and Table 6's caption sits between their rows.
        lo_, hi_, e_ = tp
        x0_, x1_ = e_[0][0], e_[-1][1]
        cuts = sorted(r[0] for r in runs_all if lo_ < r[0] < hi_
                      and min(r[2], x1_) - max(r[1], x0_) > 0.3 * (r[2] - r[1]))
        pieces, start = [], lo_
        for cut in cuts:
            if cut - 1 >= start:
                pieces.append((start, cut - 1, e_))
            start = cut + 1
        pieces.append((start, hi_, e_))
        for pc in pieces:
            pc = _trim(*pc) if len(pieces) > 1 else pc
            # EACH PIECE GETS ITS OWN COLUMNS. The part's columns were found
            # over the rows of every table stacked in it, so PhysNovo's Table 5
            # (speed, AA precision) inherited Table 6's six columns from below
            # the cut and was refused as ragged. Recomputed from the piece's
            # rows, kept to the part's width.
            if len(pieces) > 1:
                # From the words inside the part's span only: the table beside
                # it shares these rows, and its last column, tiled outwards,
                # had a centre inside this span.
                inside_rows = [[w for w in r
                                if x0_ - 2 <= (w["x0"] + w["x1"]) / 2 <= x1_ + 2]
                               for r in rows]
                own_e = column_edges(inside_rows, pc[0], pc[1])
                # ...and kept to it: tiling mirrors the first column's
                # half-width leftwards, which put Table 6's first column into
                # Table 3 at x 266 and paired the captions wrongly.
                if own_e:
                    own_e[0] = (max(own_e[0][0], x0_), own_e[0][1])
                    own_e[-1] = (own_e[-1][0], min(own_e[-1][1], x1_))
                if len(own_e) >= 2:
                    pc = (pc[0], pc[1], own_e)
            # a piece with no numbers of its own is caption text, not a table
            if not any(sum(1 for w in rows[i] if numeric(w["text"])
                           and x0_ - 2 <= (w["x0"] + w["x1"]) / 2 <= x1_ + 2) >= 2
                       for i in range(pc[0], pc[1] + 1)):
                continue
            origin[(pc[0], pc[1], pc[2][0][0])] = (pt[0], pt[1])
            trimmed.append(pc)
    parts = trimmed
    paired = pair_parts(rows, [(lo, hi, e[0][0], e[-1][1]) for lo, hi, e in parts])
    span_of = {(lo, hi, e[0][0]): (lo, hi) for lo, hi, e in parts}
    # A PART'S CROP STOPS AT ITS NEIGHBOUR. Two tables printed side by side
    # share their rows, and each one's picture was free to run into the other:
    # publication 64's page 6 cropped Table 2 together with the left half of
    # Table 3, because Table 3's method names sit in the band Table 2's crop
    # claimed. The left limit matters as much as the right -- Table 3's own
    # crop began at x 153, inside Table 2 -- so both are bounded by the
    # adjacent part in the same block.
    # Neighbours are the parts that OVERLAP VERTICALLY, not those with the
    # same rows: once each side-by-side part is trimmed to its own data, two
    # neighbours no longer share (lo, hi), and grouping on that dropped the
    # limit, so LIPNovo's Table 5 crop spread 200 pt left over Table 3.
    neighbours: dict[tuple, tuple[float, float]] = {}
    for pt in parts:
        lo, hi, edges = pt
        home = origin.get((lo, hi, edges[0][0]))
        side = sorted((q for q in parts if q is not pt
                       and origin.get((q[0], q[1], q[2][0][0])) == home),
                      key=lambda q: q[2][0][0])
        left_ = [q for q in side if q[2][-1][1] <= edges[0][0] + 1]
        right_ = [q for q in side if q[2][0][0] >= edges[-1][1] - 1]

        # THE LEFT NEIGHBOUR ENDS WHERE ITS NUMBERS END, not where its last
        # column is tiled to: that interval runs to the gutter's midpoint, past
        # the start of THIS table's stub, and CausalNovo's Table 3 lost its
        # species column (x 327) to Table 2's tiled edge.
        def data_right(q):
            xs = [w["x1"] for i in range(q[0], q[1] + 1) if 0 <= i < len(rows)
                  for w in rows[i] if numeric(w["text"])
                  and q[2][0][0] - 2 <= w["x0"] and w["x1"] <= q[2][-1][1] + 2]
            return max(xs) if xs else q[2][-1][1]
        lim_lo = (max(data_right(q) for q in left_) + 4) if left_ else 0.0
        lim_hi = (min(q[2][0][0] for q in right_) - 4) if right_ else float("inf")
        neighbours[(lo, hi, edges[0][0])] = (lim_lo, lim_hi)
    for lo, hi, edges in parts:
        table_label, caption, cap_end = caption_text(
            rows, paired.get((lo, hi, edges[0][0])), blocks, fine,
            float(page.width))
        caption = CAPTION_OVERRIDE.get((pub_id, (table_label or "").strip()), caption)
        if not table_label:
            uncaptioned += 1
            continue
        # Computed BEFORE the vetoes, because the review page shows a refused
        # table beside its picture too, and a veto is exactly the case a human
        # most needs to see.
        lim_lo, lim_hi = neighbours.get((lo, hi, edges[0][0]), (0.0, float("inf")))
        # A TABLE SET BESIDE THE TEXT (a LaTeX wrapfigure) shares its rows with
        # a column of prose, and the nearest-words rule below took the crop's
        # left edge from that prose: CrossNovo's Table 3 crop opened on half
        # a paragraph ('Table 3 shows', 'We an-'). The tell is the caption's
        # own line, where the prose sits to the LEFT of the 'Table N' label.
        # Then the label, not the prose, is the table's left edge.
        _cap = paired.get((lo, hi, edges[0][0]))
        if _cap and 0 <= _cap[0] < len(rows):
            _li, _lx0 = _cap[0], _cap[1]
            _lab = [w for w in rows[_li] if w["x0"] >= _lx0 - 1]
            if _lab and _lx0 <= edges[0][0]:
                _ltop = min(w["top"] for w in _lab)
                beside = [w for r in rows[max(0, _li - 1):_li + 2] for w in r
                          if abs(w["top"] - _ltop) <= 4 and w["x1"] < _lx0 - 5]
                # THE LABEL MAY BE INDENTED FROM THE TABLE: MemNovo's Table 9
                # sets 'Table9:' at x 325 over a stub starting at 318, and a
                # bound at the label cut 'B. subtilis' in half. So the bound is
                # the label or the table's own stub words just left of it,
                # whichever is further left; the prose beside a wrapfigure ends
                # well short of that window.
                stub_x = [w["x0"] for r in rows[lo:hi + 1] for w in r
                          if _lx0 - 20 <= w["x0"] < min(_lx0, edges[0][0])]
                if beside:
                    lim_lo = max(lim_lo, min([_lx0] + stub_x) - 4)
        crop_lb = min([w["x0"] for r in rows[lo:hi + 1] for w in r
                       if w["x1"] <= edges[0][0] and w["x0"] > edges[0][0] - 220
                       and w["x0"] >= lim_lo]
                      or [max(lim_lo, edges[0][0] - 150)])
        _pair = paired.get((lo, hi, edges[0][0]))
        bbox = block_bbox(rows, lo, hi, cap_end, edges, crop_lb,
                          cap_start=(_pair[0] if _pair else -1),
                          right_limit=lim_hi, left_limit=lim_lo)
        try:
            if (pub_id, (table_label or "").strip()) in CAPTION_VETO_ADD:
                raise Reject("C1 not a cross-method comparison (curated): "
                             + CAPTION_VETO_ADD[(pub_id, (table_label or "").strip())])
            # A SENTENCE THAT MENTIONS THE TABLE IS NOT ITS CAPTION. 'Table 3
            # reports the results of these benchmark experiments' opens a
            # paragraph, and the percentages in it were read as a ragged grid.
            # A caption never continues with a lower-case word; all five
            # captions in the library that did were prose, every one refused
            # for a reason that named the wrong fault.
            if caption and caption.strip()[:1].islower():
                raise Reject("C0 a sentence that mentions the table, not its caption: "
                             + caption.strip()[:60])
            if (pub_id, (table_label or "").strip()) not in CAPTION_VETO_OVERRIDE:
                caption_verdict(caption)
        except Reject as exc:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": str(exc), "bbox": bbox, "page": page.page_number})
            continue

        # THE BODY IS READ BEFORE THE HEADER, so the header walk can be clipped
        # to the width the body actually occupies. Reading the header first
        # meant guessing that width from the columns alone, which on a
        # two-column page let the neighbouring column's prose end the walk.
        body, dropped, ragged = [], 0, False
        # WHAT THE MINER DOES NOT RECORD IS STILL PART OF THE PRINTED TABLE.
        # A count row, a BLEU row, a year or speed column: none is a
        # measurement, but a faithful copy of the table needs them, so they are
        # kept here with their positions instead of being thrown away.
        not_recorded_rows: list[dict] = []
        multi = None
        pending: list[str] = []          # interstitial group-label words
        used_as_label: set[int] = set()  # label lines already given to a row
        pending_top: float | None = None   # where that label sits
        # WHERE THE DATA REALLY BEGINS, not where the first column's interval
        # is extrapolated to begin. `column_edges` tiles the intervals by
        # mirroring each column's right half-width, so the leftmost interval
        # reaches further left than any number in it -- 89.1 against a first
        # digit at 107.9 in publication 18's Table 1. A row label is taken
        # from the words ENTIRELY left of that bound, so a label 1 pt wider
        # than the extrapolation was discarded: 'TSARseqNovo' (x1 90.1) and
        # 'vs pi-HelexiNovo' (93.6) vanished while the narrower 'vs CasaNovo'
        # (83.8) survived, which left the table with no subject and no
        # comparator and rejected it for carrying no methods. The measured
        # left edge of the first column's data cannot clip a label.
        data_x0 = [w["x0"] for r in rows[lo:hi + 1] for w in r
                   if numeric(w["text"]) and assign(w, edges) == 0]
        label_right = max(edges[0][0], min(data_x0) - 1.0) if data_x0 else edges[0][0]
        # EACH ROW IS CLIPPED TO THIS PART'S OWN WIDTH, between its side-by-
        # side neighbours. A page row holds both tables' words, so a row's
        # 'top' came from the neighbour's text, and a species label set beside
        # the neighbour's data never arrived as an interstitial at all:
        # CausalNovo's Table 3 centres each species between a CasaNovo-dagger
        # and a +CausalNovo row, and with Table 2's words in the same page
        # rows the centred-label rule measured against the wrong heights,
        # paired two species' rows and refused the table as a duplicate.
        nb_lo, nb_hi = neighbours.get((lo, hi, edges[0][0]), (0.0, float("inf")))
        for ri_abs, r in enumerate(rows[lo:hi + 1], start=lo):
            r = [w for w in r if w["x1"] > nb_lo and w["x0"] < nb_hi]
            if not r:
                continue
            r = split_glued_year(heal_fragments(r), edges)
            cells: dict[int, dict] = {}
            for w in r:
                if URLISH.match(w["text"]):
                    continue
                if MULTI.search(w["text"]):                  # G4
                    parts = compound(w["text"])
                    if parts is None:
                        multi = w["text"]
                        break
                    k = assign(w, edges)
                    if k is None:
                        continue
                    if k in cells:
                        ragged = True
                    cells[k] = {"value": parts[0][0], "stddev": None,
                                "printed": w["text"].strip(), "parts": parts}
                    cells[k]["bold"], cells[k]["underlined"] = emphasis_of(
                        w, bold_words, under_rules)
                    continue
                parsed = numeric(w["text"])
                if parsed is None:
                    continue
                k = assign(w, edges)
                if k is None:
                    continue
                if k in cells:
                    ragged = True
                cells[k] = {"value": parsed[0], "stddev": parsed[1],
                            "printed": w["text"].strip(),
                            "parts": [(parsed[0], "")]}
                cells[k]["bold"], cells[k]["underlined"] = emphasis_of(
                    w, bold_words, under_rules)
            if multi:
                break
            # A LONE '/' MEANS THE CELL WRAPS. 'Plasma ... 0.491 / 0.782'
            # with '0.725*' on the line below is one cell holding 0.491 and
            # 0.725*, broken over two lines by the column width. The slash is
            # its own token, so the cell it belongs to is the numeric token
            # immediately to its left.
            for w in r:
                if w["text"].strip() != "/":
                    continue
                left = [x for x in r if numeric(x["text"]) and x["x1"] <= w["x0"]]
                if not left:
                    continue
                k = assign(max(left, key=lambda x: x["x1"]), edges)
                if k is not None and k in cells:
                    cells[k]["wrapped"] = True
            label = row_label(r, label_right)
            # A LABEL SPLIT ONTO ITS OWN LINE. A row's label and its values can
            # sit a few points apart and so arrive as two rows: LIPNovo's
            # Table 3 has 'Baseline-dagger' 3.2 pt above its values, the line
            # above the block, so the row came through bare and was dropped.
            # An empty label takes a label-only line immediately above it.
            if cells and not label.strip() and ri_abs > 0:
                above = rows[ri_abs - 1]
                if above and not any(numeric(w["text"]) for w in above) and \
                        min(w["top"] for w in r) - min(w["top"] for w in above) <= 4.5:
                    label = row_label(above, label_right)
            # ...OR JUST BELOW IT, where a cell holds two lines and its label is
            # centred between them: GA-Novo's Table 5 sets 'GA-Novo' 6 pt under
            # its values, above the line of significance markers. Only a line
            # whose words all sit in the stub, within 7 pt.
            if cells and not label.strip() and ri_abs + 1 < len(rows):
                below = rows[ri_abs + 1]
                if below and not any(numeric(w["text"]) for w in below) and \
                        all(w["x1"] <= label_right + 1 for w in below) and \
                        min(w["top"] for w in below) - min(w["top"] for w in r) <= 7.0:
                    label = row_label(below, label_right)
                    used_as_label.add(ri_abs + 1)
            if cells and (COUNT_ROW.search(label)
                          or UNRECORDED_METRIC_ROW.search(label)):
                dropped += 1
                not_recorded_rows.append({
                    "label": label, "top": min(w["top"] for w in r),
                    "why": ("count" if COUNT_ROW.search(label)
                            else "metric outside the vocabulary"),
                    "words": [((w["x0"] + w["x1"]) / 2, w["text"].strip())
                              for w in r if w["x0"] >= label_right - 1]})
                continue
            missing = [w["text"].strip().lower() for w in r
                       if w["text"].strip().lower() in NOT_RUN]
            # WHICH COLUMN THE MARKER IS IN, not just how many there are. A
            # dataset-split part keeps only the columns it owns, so a row whose
            # cell in THAT dataset is an en-dash had nothing left and vanished
            # from the part: RefineNovo's Table 6 lists InstaNovo and
            # PrimeNovo-CV, neither of which was run on seven-species, and the
            # seven-species part showed eight rows where the paper prints ten.
            # The row has no measurement there, which is the point, so it is
            # carried with its marker and no value.
            absent = {}
            for w in r:
                if w["text"].strip().lower() in NOT_RUN:
                    kk = assign(w, edges)
                    if kk is not None:
                        absent[kk] = w["text"].strip()
            if len(cells) == 0 and ri_abs in used_as_label:
                # Already the label of the row above it (GA-Novo's Table 5):
                # carried on as a group label too, it put 'GA-Novo' over the
                # PEAKS row as well.
                continue
            if len(cells) == 0:
                # A row with no cells inside the table is either an
                # interstitial group label or a rule. Its words are carried to
                # the next data row rather than discarded, because that is
                # where 'AminoAcid' / 'Precision' live when a stacked table
                # sets its metric on its own line.
                # CLIPPED TO THE TABLE'S OWN SPAN FIRST. On a page holding two
                # tables side by side, an interstitial row carries the other
                # column's prose as well, which made it read as prose rather
                # than a label and lost the label entirely: LIPNovo's Table 3
                # dropped 'ClamBa.' and then gave that species' two rows the
                # previous species' name, so two different measurements became
                # one and G6 refused the table. Where the label survived it
                # arrived with a sentence glued to it
                # ('Mouse by+5.4%. Theseresults...').
                # A GROUP LABEL LIVES IN THE STUB, left of the first data
                # column. Clipping by the table's full span was not enough on
                # a two-column page whose body text starts INSIDE that span:
                # 'Mouse' came through as 'Mouse by+5.4%. Theseresultshigh...'.
                mine_ = [w for w in r if w["x1"] <= label_right + 4
                         and w["x1"] >= edges[0][0] - 220]
                if mine_ and row_kind(mine_) == "interstice":
                    pending.extend(w["text"] for w in mine_)
                    pending_top = min(w["top"] for w in mine_)
                continue
            if len(cells) != len(edges):
                if missing and len(cells) + len(missing) >= len(edges):
                    # A NOT-RUN MARKER DOES NOT DISCARD THE ROW. Dropping the
                    # whole row lost every value beside the gap: RefineNovo's
                    # Table 6 marks three of its nine rows with an en-dash for
                    # a dataset a model was never run on, and InstaNovo,
                    # PrimeNovo-CV and Casanovo-pretrained vanished with them.
                    # The cells that ARE there are recorded and the absent ones
                    # are simply absent, which is what a NULL is for.
                    dropped += len(edges) - len(cells)
                else:
                    ragged = True
            body.append({"label": label, "cells": cells, "extra": pending,
                         "absent": absent, "top": min(w["top"] for w in r),
                         "extra_top": pending_top})
            pending, pending_top = [], None
        if multi:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": f"G4 multi-valued cell {multi!r}",
                           "bbox": bbox, "page": page.page_number})
            continue
        if ragged:                                           # G1
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": f"G1 ragged grid ({len(edges)} columns)",
                           "bbox": bbox, "page": page.page_number})
            continue
        if not body:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": "G3 no data rows survived",
                           "bbox": bbox, "page": page.page_number})
            continue
        # Trailing interstitial words belong to the last group, not to nothing:
        # a stacked table whose final label line sits BELOW its last data row
        # would otherwise lose it.
        if pending and body:
            body[-1]["extra"] = body[-1]["extra"] + pending

        # The continuation of a wrapped cell sits on the row below the block.
        # Only numbers that land in a column ALREADY MARKED as wrapped are
        # taken, so a page number or a footnote marker cannot be mistaken for
        # a measurement.
        wrapped = {k for r in body for k, c in r["cells"].items()
                   if c.get("wrapped")}
        if wrapped:
            for after in range(hi + 1, min(hi + 3, len(rows))):
                nums = [w for w in rows[after] if numeric(w["text"])]
                if not nums or len(nums) > len(wrapped) + 1:
                    break
                took = False
                for w in nums:
                    k = assign(w, edges)
                    if k is None or k not in wrapped:
                        continue
                    host = next((r["cells"][k] for r in reversed(body)
                                 if k in r["cells"] and r["cells"][k].get("wrapped")),
                                None)
                    if host is None:
                        continue
                    v = numeric(w["text"])
                    if not v:
                        continue
                    mk = re.search(r"([*+\u2020\u2021])\s*$", w["text"].strip())
                    host.setdefault("parts", [(host["value"], "")]).append(
                        (v[0], mk.group(1) if mk else ""))
                    host["printed"] = host["printed"] + " / " + w["text"].strip()
                    took = True
                if not took:
                    break

        # Not past the left neighbour: PhysNovo's Table 5 sits beside Table 3,
        # whose numbers then counted as this table's stub, and the header walk
        # stopped on them as "another data block".
        left_bound = min(
            [w["x0"] for r in rows[lo:hi + 1] for w in r
             if w["x1"] <= edges[0][0] and w["x0"] > edges[0][0] - 220
             and w["x0"] >= nb_lo]
            or [max(nb_lo, edges[0][0] - 150)]) - 4
        li = (paired.get((lo, hi, edges[0][0])) or (None,))[0]
        own, span, stub, span_ambiguous = header_model(
            rows, lo, edges, left_bound=left_bound,
            floor=(cap_end + 1 if 0 <= cap_end < lo else 0), rules=rules,
            hard_floor=(li + 1 if li is not None and li < lo else 0),
            data_left=label_right,
            # bounded by this table's OWN last number too: the neighbour's
            # limit is its first data column, and its stub (CausalNovo's
            # Table 3 species, at x 327) sits left of that.
            right_bound=min(nb_hi, max(
                [w["x1"] for r in rows[lo:hi + 1] for w in r
                 if numeric(w["text"]) and assign(w, edges) == len(edges) - 1]
                or [edges[-1][1]]) + 14))
        if not own and not span:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": "G3 no header above the block",
                           "bbox": bbox, "page": page.page_number})
            continue
        # A YEAR COLUMN IS METADATA, NOT A MEASUREMENT. CausalNovo's Table 1
        # prints each method's publication year beside it (2003, 2017, 2021),
        # which parses as a number column. Its header names no metric, so the
        # per-column metric reading failed, the fallback gave every column one
        # metric, and two columns of the same method then collided (G6). A
        # column headed 'Year', or holding only years, is dropped the way a
        # count row is.
        year_cols = []
        for k in range(len(edges)):
            vals = [r["cells"][k]["printed"] for r in body if k in r["cells"]]
            # The column's UPPER header lines count too: GA-Novo heads a column
            # 'avg. len. of' / 'partial matches' over two lines, and the own
            # line alone ('partial matches') said nothing about a length.
            head = " ".join(span.get(k, []) + own.get(k, [])).strip()
            # ...and so is a SPEED or TIME column beside the metrics: PhysNovo's
            # Table 5 sets 'Speed (spectra/s)' beside 'AA Prec.', and its 11 and
            # 105 refused the table on mixed units. Only ever a column among
            # others; an all-runtime table is C3's to refuse.
            if (re.fullmatch(r"(?i)years?", head) or
                    re.search(r"(?i)\bspeed\b|spectra/s|throughput|latency"
                              r"|\btime\b|\(ms\)|\(s\)"
                              # an ERROR RATE is outside the metric vocabulary:
                              # InstaNovo's results table leads with one; so is
                              # a LOSS (PLMNovo's 'Classification Loss (↓)'),
                              # where lower is better and nothing compares.
                              # The text layer glues it ('ClassificationLoss'),
                              # so a capital L after a lower-case letter counts.
                              r"|error\s*rate|\bloss\b|(?-i:(?<=[a-z])Loss)"
                              # an AVERAGE LENGTH (GA-Novo's 'avg. len. of
                              # partial matches') is a count, not a metric
                              r"|avg\.?\s*len", head) or
                    (vals and all(re.fullmatch(r"(19|20)\d\d", v) for v in vals))
                    or k in NOT_RECORDED_COLUMNS.get((pub_id, (table_label or "").strip()), {})):
                year_cols.append(k)
        not_recorded_cols: list[dict] = []
        if year_cols and len(year_cols) < len(edges):
            for k in year_cols:
                head_k = " ".join(own.get(k, [])).strip()
                not_recorded_cols.append({
                    "x": (edges[k][0] + edges[k][1]) / 2, "header": head_k,
                    "why": "year" if (re.fullmatch(r"(?i)years?", head_k) or not head_k)
                           else "error rate" if re.search(r"(?i)error\s*rate", head_k)
                           else (NOT_RECORDED_COLUMNS.get(
                                     (pub_id, (table_label or "").strip()), {}).get(k)
                                 or ("loss" if re.search(r"(?i)\bloss\b|(?-i:(?<=[a-z])Loss)",
                                                         head_k) else None)
                                 or ("length" if re.search(r"(?i)avg\.?\s*len", head_k)
                                     else None)
                                 or "speed or time"),
                    "cells": {r["top"]: r["cells"][k]["printed"]
                              for r in body if k in r["cells"]}})
            keep = [k for k in range(len(edges)) if k not in year_cols]
            remap = {old_k: new_k for new_k, old_k in enumerate(keep)}
            edges = [edges[k] for k in keep]
            own = {remap[k]: v for k, v in own.items() if k in remap}
            span = {remap[k]: v for k, v in span.items() if k in remap}
            span_ambiguous = {remap[k] for k in (span_ambiguous or set()) if k in remap}
            for r in body:
                r["cells"] = {remap[k]: c for k, c in r["cells"].items() if k in remap}
                r["absent"] = {remap[k]: c for k, c in (r.get("absent") or {}).items()
                               if k in remap}
        out.append({"edges": edges, "own": own, "span": span, "stub": stub,
                    "body": body, "dropped_rows": dropped,
                    "not_recorded_rows": not_recorded_rows,
                    "not_recorded_cols": not_recorded_cols,
                    "caption": caption, "table_label": table_label,
                    "footnote": footnote_text(rows, hi, fine, crop_lb,
                                              edges[-1][1] + 14),
                    "header_raw": " ".join(
                        w["text"] for i in range(max(cap_end + 1, lo - 6), lo)
                        if 0 <= i < len(rows)
                        for w in rows[i]
                        if crop_lb - 4 <= w["x0"] and w["x1"] <= edges[-1][1] + 14),
                    "span_ambiguous": span_ambiguous,
                    "registry_label": table_label,
                    "bbox": bbox, "page": page.page_number})
    return out, vetoed, uncaptioned


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def algorithm_index(con: sqlite3.Connection) -> dict[str, list[tuple[int, str]]]:
    """Every name and alias, normalised, to the rows it could mean.

    A LIST, not a single row, because norm() strips '+' and three pairs of
    distinct methods differ only by it: InstaNovo / InstaNovo+,
    LIPNovo / LIPNovo+, pNovo / pNovo+. Keyed one-to-one with setdefault, the
    first row loaded silently won, and CrossNovo's 'Insta.' column was
    attributed to InstaNovo+ -- a different method, with a different paper and
    a different author list. norm() is not changed, because it has to stay
    identical to build_benchmarks.py's; the collision is resolved at the point
    of use instead, by plus_match().
    """
    index: dict[str, list[tuple[int, str]]] = {}
    for aid, name, aliases in con.execute(
            "SELECT id, name, aliases FROM algorithm"):
        for nm in [name] + [a.strip() for a in (aliases or "").split(",") if a.strip()]:
            if nm:
                bucket = index.setdefault(norm(nm), [])
                if (aid, name) not in bucket:
                    bucket.append((aid, name))
    return index


CITE_TAIL = re.compile(
    r"\s*(?:\[[\d,\s\u2013-]+\]|\((?=[^)]*(?:\b(?:19|20)\d{2}[a-z]?\b|et\s*al))[^)]*\))\s*$")
MARKER_TAIL = re.compile(r"([*+\u2020\u2021\u00a7\u00b6])\s*$")


def label_marker(printed: str) -> str:
    """The footnote marker on a label, or ''.

    The marker sits BEFORE a citation, not at the end of the string:
    'AdaNovo-dagger(Xia et al., 2024)'. Looking only at the end found nothing,
    so the two AdaNovo rows of LIPNovo's Table 1 came back with the same basis
    and G6 refused the table. The citation is removed first.
    """
    raw = CITE_TAIL.sub("", (printed or "").strip()).strip()
    m = MARKER_TAIL.search(raw)
    if m:
        return m.group(1)
    # ...or at the START. CausalNovo prints '†CasaNovo (Yilmaz et al., 2024)'
    # where LIPNovo prints 'AdaNovo†', so the dagger went unseen and its
    # retrained Casanovo row came back 'quoted', colliding with the plain one.
    # A '+' is not a marker here: it is CausalNovo's plug-in notation.
    m = re.match(r"\s*([*\u2217\u2020\u2021\u00a7\u00b6]+)", raw)
    return m.group(1) if m else ""


def plus_match(printed: str, candidates: list[tuple[int, str]]):
    """Pick between names that differ only by a trailing '+'.

    The rule is the only one the printed text supports: a name printed with a
    '+' means the '+' variant, and a name printed without one means the base
    method. So 'Insta.' is InstaNovo and 'InstaNovo+' is InstaNovo+. Anything
    still ambiguous after that is refused rather than guessed.
    """
    if len(candidates) == 1:
        return candidates[0]
    wants_plus = "+" in printed
    hits = [c for c in candidates if ("+" in c[1]) == wants_plus]
    return hits[0] if len(hits) == 1 else None


def paper_vocabulary(text: str, con: sqlite3.Connection) -> dict[str, list[tuple[int, str]]]:
    """The methods THIS paper names in full, which is the matching universe.

    Prefix matching against all 382 algorithm rows is ambiguous and would be
    wrong: 'Deep.' prefixes DeepNovo, DeepNovo V2, DeepNovo-DIA, DeepNovoAA,
    Deep Novo A+ and more. Restricting to what the paper spells out somewhere
    (ContraNovo's Baselines section names all five of its baselines in full)
    turns an 8-way ambiguity into a unique answer.
    """
    # EVERY SPELLING THE PAPER USES BECOMES A KEY, not just the first that
    # matches. Stopping at the first one recorded 'DiaTrans' for publication 30
    # and then never looked at its aliases, so the name its own TABLE prints,
    # 'Transformer-DIA', was absent from the vocabulary and the table was
    # refused for an unresolved method. An algorithm is reachable by any name
    # the paper actually spells out.
    vocab: dict[str, list[tuple[int, str]]] = {}
    # A MARGIN LINE NUMBER SITS INSIDE A HYPHENATED NAME. An ICLR submission
    # numbers every line in the margin, and extract_text() puts that number at
    # the head of the line it labels -- so CrossNovo's prose, which breaks its
    # only full spelling of PointNovo across a line as 'Point-' / 'Novo', came
    # out squashed as 'point360novo' and the de-spaced check below still
    # missed it. A leading digit run per line is dropped first. Digits are not
    # removed wholesale, because 'pNovo 3' needs its 3 to stay distinct from
    # 'pNovo'.
    squashed = re.sub(r"[^A-Za-z0-9]", "",
                      re.sub(r"(?m)^\s*\d{1,4}\s", " ", text)).lower()
    for aid, name, aliases in con.execute(
            "SELECT id, name, aliases FROM algorithm WHERE kind IN "
            "('algorithm','post-processor','adjacent')"):
        for nm in [name] + [a.strip() for a in (aliases or "").split(",") if a.strip()]:
            if not nm or len(nm) < 4:
                continue
            if re.search(r"(?i)(?<![A-Za-z0-9])" + re.escape(nm) + r"(?![A-Za-z0-9])", text):
                vocab.setdefault(norm(nm), [])
                if (aid, name) not in vocab[norm(nm)]:
                    vocab[norm(nm)].append((aid, name))
                continue
            # Some producers emit runs with the spaces dropped, so a baseline
            # cited as 'PointNovo (Qiao et al. 2021)' arrives as
            # 'PointNovo(Qiaoetal.2021)' and the word-boundary lookbehind
            # fails on the preceding letter. PointNovo was missing from
            # CrossNovo's vocabulary for exactly this reason, which then made
            # its 'Point.' column unresolvable. Checked against a de-spaced
            # copy, and only for names long enough that a substring hit is not
            # a coincidence.
            if len(nm) >= 6 and norm(nm) in squashed:
                vocab.setdefault(norm(nm), [])
                if (aid, name) not in vocab[norm(nm)]:
                    vocab[norm(nm)].append((aid, name))
    return vocab


# A ROW THAT IS A DIFFERENCE, NOT A MEASUREMENT. TSARseqNovo's Table 1
# (publication 18) prints its own score and then, under it, 'vs CasaNovo' and
# 'vs pi-HelexiNovo' rows holding the IMPROVEMENT in percentage points: 4.8,
# 8.4, 10.4. Those labels resolve to methods perfectly well, so read naively
# the table records 4.8 as CasaNovo's peptide precision, which is both wrong
# and worse than a rejection because it looks like data. The catalog has no
# grain for a difference -- `value` is a measurement normalised to 0..1 -- so
# these rows are identified and excluded, and a table left with no comparator
# of its own is refused rather than published as a one-method 'comparison'.
# TWO CLASSES OF MARKER, and they cannot share a lookahead.
#
# 'vs' and 'versus' may run straight into the method name, because these
# labels arrive with their spaces gone: 'vsCasaNovo', 'vspi-HelexiNovo'. No
# method in this catalog begins with either, so allowing a camel-case boundary
# there is safe.
#
# The word-like markers must be followed by a real separator, and that is not
# a nicety: 'diff' with a letter allowed after it matches **DiffNovo**, which
# is publication 13's method, and matched 'DiffuNovo' too -- which is exactly
# what happened, silently dropping both of DiffuNovo's own rows from its six
# tables and rejecting all six for having no subject. Note also that an inline
# (?i) applies to the whole pattern, so a case-insensitive [A-Z] matches every
# letter; the case-sensitive group below is deliberate.
DELTA_LABEL = re.compile(
    r"^\s*(?:(?i:vs\.?|versus)(?=$|[\s._-]|(?-i:[A-Z\u03c0]))"
    r"|\u0394(?=$|[\s._-]|(?-i:[A-Za-z]))"
    r"|(?i:delta|diff(?:erence)?|improvements?(?:\s+over)?|"
    r"gains?(?:\s+over)?)(?=$|[\s._-])"
    # 'Rel. Imp. (%)': MemNovo's relative-improvement rows, glued as
    # 'Rel.Imp.(%)', so the lookahead takes an opening bracket too.
    r"|(?i:rel\.?\s*imp(?:rovement|\.)?)(?=$|[\s(._-]))")


# A trailing version token, INCLUDING a bare integer: a paper comparing two of
# its own configurations prints 'DiffuNovo 1' and 'DiffuNovo 2', which resolved
# to nothing and took DiffuNovo's whole Table 2 down with it.
VERSION_TAIL = re.compile(
    r"(?i)[\s._-]*(v\s?\d(?:\.\d+)?|\d\.\d+|\d|[+]|-?DIA|-?DDA)$")


def resolve_method(printed: str, vocab: dict[str, list[tuple[int, str]]],
                   index: dict[str, list[tuple[int, str]]],
                   pub_id: int | None = None) -> tuple[int, str, str | None]:
    """Resolve a printed column header to (algorithm_id, name, printed version).

    Raises Reject on a miss. A wrong guess here would be invisible in the data;
    a rejection is in the tally, which is the trade this whole script makes.
    """
    raw = printed.strip().strip("|,")
    # A CITATION MARKER IS NOT PART OF THE NAME. Tables label a baseline with
    # its reference: 'DeepNovo[55]', 'Casanovov4.2[33]'. Stripped before
    # anything else looks at the string, so the version logic sees
    # 'Casanovov4.2' and not a bracket.
    raw = re.sub(r"\s*\[[\d,\s\u2013-]+\]\s*$", "", raw).strip()
    # AN AUTHOR-YEAR PARENTHETICAL IS A CITATION, not a variant, and it is
    # removed HERE rather than with the version logic further down: every later
    # rule, including the curated per-paper aliases, matches on the normalised
    # string, and 'peaksnovomaetal2003' matches nothing. A parenthetical with
    # no year and no 'et al' is left alone, because '(Logits)' and '(ours)'
    # really are variants.
    raw = re.sub(r"\s*\((?=[^)]*(?:\b(?:19|20)\d{2}[a-z]?\b|et\s*al))[^)]*\)\s*$",
                 "", raw).strip()
    # A dagger or asterisk on the label is a footnote marker, not a name; what
    # it MEANS is recorded per table in METHOD_MARKERS.
    # A TRAILING '+' IS PART OF THE NAME when the catalog has a '+' variant:
    # GyroNovo prints 'LIPNovo+(Du et al. 2026)' beside 'LIPNovo(Du et al.
    # 2025)', and stripping the '+' as a marker merged the two methods.
    plus_named = raw.endswith("+") and any(
        "+" in n for _, n in index.get(norm(raw), []))
    raw = re.sub(r"[*+\u2020\u2021\u00a7\u00b6]+\s*$", "", raw).strip()
    if plus_named:
        raw += "+"
    if not raw:
        raise Reject("N1 empty header")
    # A curated per-paper label wins over every general rule.
    # A '+'-KEEPING KEY FIRST: norm() drops the '+', so Prime-DiffNovo's 'IN
    # v1.1' and 'IN+ v1.1' (InstaNovo and InstaNovo+) shared one key.
    pa = (PAPER_LABEL_ALIASES.get((pub_id, re.sub(r"[^a-z0-9+]", "", raw.lower())))
          or PAPER_LABEL_ALIASES.get((pub_id, norm(raw)))) if pub_id else None
    if pa:
        got = plus_match(pa[0], index.get(norm(pa[0]), []))
        if got:
            return got[0], got[1], pa[1]
    # 5. the hand-written residue, consulted first because its entries exist
    #    precisely because the general rules get them wrong.
    hit = COMPARISON_ALIASES.get(norm(raw))
    if hit:
        name, ver = hit
        got = plus_match(name, index.get(norm(name), []))
        if got:
            return got[0], got[1], ver
    # 4a. A TRAILING PARENTHETICAL IS A VARIANT QUALIFIER, not part of the
    #     name. Papers label their own configurations this way --
    #     'DiffuNovo(Logits)', 'DiffuNovo(MBR)', 'RefineNovo(ours)' -- and
    #     norm() cannot help, because it just glues the words together into
    #     'diffunovologits'. Three such labels took DiffuNovo's Table 2 down
    #     entirely, and with it 84 reported values across three benchmarks.
    #     '(ours)' is not a version, it is the paper pointing at itself, so it
    #     is dropped rather than recorded.
    paren_stem = paren_ver = None
    m = re.match(r"^(.*?)\s*\(([^)]{1,24})\)$", raw)
    if m and len(m.group(1)) >= 3:
        paren_stem = m.group(1).strip()
        inner = m.group(2).strip()
        paren_ver = None if norm(inner) in SELF_WORDS else inner

    # 4b. split a trailing version token off, and keep it.
    version = None
    stem = raw
    m = VERSION_TAIL.search(raw)
    if m and len(raw) - len(m.group(0)) >= 3:
        stem, version = raw[:m.start()], m.group(1)
    # THE EXACT NAME IS TRIED BEFORE THE STRIPPED STEM. The other order breaks
    # every catalog name that legitimately ends in a number: 'PAAS 3' would
    # have its '3' stripped and match PAAS, a different program by the same
    # group, which is exactly the kind of wrong-but-plausible attribution a
    # rejection is preferable to.
    for cand, ver in ((raw, None), (paren_stem, paren_ver), (stem, version)):
        if cand is None:
            continue
        n = norm(cand)
        if not n:
            continue
        # 2. exact normalised match, restricted to the paper's vocabulary.
        if n in vocab:
            got = plus_match(cand, vocab[n])
            if not got:
                raise Reject(f"N1 {raw!r} is ambiguous between "
                             f"{sorted(nm for _, nm in vocab[n])}")
            return got[0], got[1], ver
        # 3. a trailing period means a truncation. Require the prefix to be
        #    unique WITHIN THE PAPER'S VOCABULARY; globally it never is.
        if cand.rstrip().endswith("."):
            pref = norm(cand)
            cands = [v for k, vs in vocab.items() if k.startswith(pref) for v in vs]
            got = plus_match(cand, cands)
            if got:
                return got[0], got[1], ver
            if cands:
                raise Reject(f"N1 ambiguous prefix {raw!r} -> "
                             f"{sorted(n for _, n in cands)}")
        # a non-truncated name may still be a prefix of exactly one entry
        cands = [v for k, vs in vocab.items() if k.startswith(n) for v in vs]
        got = plus_match(cand, cands)
        if got:
            return got[0], got[1], ver
    raise Reject(f"N1 unresolved header {raw!r}")


# Which dataset a table is scored on is NOT in the table body: it is in the
# caption, the column header or the spanner. A cue maps printed phrasing to a
# LABEL, and DATASET_TARGETS maps that label to a (dataset, version) pair,
# because what a paper names is not always a whole dataset: "HC-PT" names a
# VERSION of ProteomeTools. A phrase absent from here leaves the dataset
# unresolved rather than guessed.
DATASET_CUES: list[tuple[re.Pattern, str]] = [
    # HC-PT before ProteomeTools: it is the more specific name for the same
    # corpus, and a table naming it does not say "ProteomeTools".
    (re.compile(r"(?i)\bHC-?PT\b"), "HC-PT"),
    # The separator is optional: a column header reads '9Species (yeast)'
    # with nothing between the digit and the word, which a required hyphen or
    # space missed, so the table looked like it named three datasets when one
    # of them was simply unrecognised.
    (re.compile(r"(?i)nine[- ]?species|9[- ]?species"), "Nine-species"),
    (re.compile(r"(?i)\b7[- ]?species\b|\bseven[- ]?species\b"), "Seven-species"),
    (re.compile(r"(?i)ProteomeTools"), "ProteomeTools"),
    (re.compile(r"(?i)MassIVE-?KB"), "MassIVE-KB"),
    # pi-PrimeNovo's name for the Tran et al. 2016 antibody deposit, whose
    # accession its Data Availability gives (MSV000079801). Only the -HC form:
    # CrossNovo's bare 'IgG1-Human' gives no accession and stays unresolved.
    (re.compile(r"(?i)IgG1-?Human-?HC"), "IgG1-Human-HC"),
    # pNovo 3's two HeLa runs, which share one PRIDE submission and differ only
    # in how much of it was used, so the label pins the version.
    (re.compile(r"(?i)\bQE_?HF_?X1\b"), "QE_HF_X1"),
    (re.compile(r"(?i)\bQE_?HF_?X2\b"), "QE_HF_X2"),
    # A PER-SPECIES PROVENANCE SUBMISSION IS THE BENCHMARK, AT NO KNOWN
    # VERSION. pNovo 3 scores five columns on V. mungo, M. musculus, M. mazei,
    # S. cerevisiae and A. mellifera, which are five of the nine-species
    # benchmark's own provenance accessions (PXD005025, PXD004948, PXD004325,
    # PXD003868, PXD004467, all already in dataset_address). Going to those
    # submissions directly does not say which curated version was used, so
    # these resolve the dataset and leave the version NULL, which is the same
    # finding CLAUDE.md records for the other papers that cite provenance
    # accessions. They map to the SAME label as the nine-species cue, so they
    # add no ambiguity to the caption path.
    (re.compile(r"(?i)\bV\.?\s?mungo\b|Vigna mungo"), "Nine-species"),
    (re.compile(r"(?i)\bM\.?\s?musculus\b|Mus musculus"), "Nine-species"),
    (re.compile(r"(?i)\bM\.?\s?mazei\b|Methanosarcina mazei"), "Nine-species"),
    (re.compile(r"(?i)\bS\.?\s?cerevisiae\b|Saccharomyces cerevisiae"), "Nine-species"),
    (re.compile(r"(?i)\bA\.?\s?mellifera\b|Apis mellifera"), "Nine-species"),
]

# HC-PT is NovoBench's 10% subsample of the high-confidence InstaNovo split of
# ProteomeTools, so naming it pins both grains at once. See 'Datasets, at three
# grains' in CLAUDE.md for why it is a version and not a dataset.
DATASET_TARGETS: dict[str, tuple[str, str | None]] = {
    "HC-PT": ("ProteomeTools", "HC-PT (NovoBench)"),
    "Nine-species": ("Nine-species benchmark", None),
    "Seven-species": ("Seven-species benchmark", None),
    "ProteomeTools": ("ProteomeTools", None),
    "MassIVE-KB": ("MassIVE-KB", None),
    "IgG1-Human-HC": ("Monoclonal antibody de novo assembly", None),
    "QE_HF_X1": ("HeLa Q Exactive HF runs (pNovo 3)", "QE_HF_X1"),
    "QE_HF_X2": ("HeLa Q Exactive HF runs (pNovo 3)", "QE_HF_X2"),
}


# "v2" is not globally meaningful, so the lexicon is PER DATASET and
# hand-written, with the spelling each paper uses mapped onto the catalog's
# own `dataset_version.version`. An unlisted phrase leaves the version NULL
# with the printed text kept, exactly as publication_dataset does.
VERSION_LEXICON: dict[str, list[tuple[re.Pattern, str]]] = {
    "Nine-species benchmark": [
        (re.compile(r"(?i)9-?species-?v2|nine-?species-?v2|revised (?:nine-species )?(?:benchmark|dataset)|"
                    r"multi-?species benchmark|re-?curated"), "revised (main)"),
        (re.compile(r"(?i)balanced"), "revised (balanced)"),
        (re.compile(r"(?i)InstaNovo split|parquet split"), "InstaNovo split"),
        (re.compile(r"(?i)ProteoBench"), "ProteoBench selection"),
        (re.compile(r"(?i)9-?species-?v1|nine-?species-?v1|original (?:nine-species )?(?:benchmark|dataset)|"
                    r"DeepNovo(?:'s)? (?:original )?(?:benchmark|dataset)"), "original (DeepNovo, 2017)"),
        # The ACCESSION is the most reliable cue of all: Prime-DiffNovo heads
        # its columns 'Nine-species MSV000081382' and 'Revised Nine-species
        # MSV000090982'. Listed after the phrases, so a stated phrase wins.
        (re.compile(r"MSV000090982"), "revised (main)"),
        (re.compile(r"MSV000081382"), "original (DeepNovo, 2017)"),
    ],
}

# A version-split part ('Nine-species · revised (main)', see split_by_dataset)
# is pinned to that version of its dataset.
for _cue, (_ds, _v) in list(DATASET_TARGETS.items()):
    for _rx, _ver in VERSION_LEXICON.get(_ds, []):
        DATASET_TARGETS.setdefault(f"{_cue} \u00b7 {_ver}", (_ds, _ver))


def resolve_dataset(con: sqlite3.Connection, caption: str, near: str,
                    pub_id: int, hint: str | None = None,
                    header: str = "", label: str = ""
                    ) -> tuple[int | None, str | None, int | None, str | None]:
    """(dataset_id, dataset_name, dataset_version_id, printed) or raise Reject.

    Three sources in order: the caption, then the surrounding prose, then the
    paper's own publication_dataset links as a last resort.
    """
    pinned_ds = TABLE_DATASET.get((pub_id, (label or "").strip()))
    if pinned_ds:
        # A CURATED DATASET WINS over anything read off the page: it was put
        # there because the page cannot be read correctly without the prose.
        name, printed = pinned_ds
        if name is None:
            return None, None, None, printed
        row = con.execute("SELECT id FROM dataset WHERE name = ?", (name,)).fetchone()
        if not row:
            raise Reject(f"D1 TABLE_DATASET names {name!r}, absent from the "
                         f"dataset table")
        return row[0], name, None, printed
    if hint:
        # A per-column spanner or header naming the dataset is MORE specific
        # than the caption, which on a split table names all of them at once.
        name, pinned = DATASET_TARGETS.get(hint, (hint, None))
        row = con.execute("SELECT id FROM dataset WHERE name = ?", (name,)).fetchone()
        if not row:
            raise Reject(f"D1 {hint!r} maps to dataset {name!r}, which is absent")
        did = row[0]
        if pinned:
            # THE LABEL ITSELF PINS THE VERSION, so no lexicon lookup is
            # wanted: a column headed 'HC-PT' is NovoBench's subsample and
            # nothing else, whatever the surrounding prose discusses.
            got = con.execute(
                "SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
                (did, pinned)).fetchone()
            if not got:
                raise Reject(f"D1 {hint!r} maps to version {pinned!r} of "
                             f"{name!r}, which is absent")
            return did, name, got[0], hint
        # THE CAPTION OUTRANKS THE PROSE HERE TOO, and getting the loop order
        # wrong was a silent misattribution: iterating the lexicon outer and
        # the scopes inner let the NEXT table's caption, which is in `near` on
        # the same page, match the revised-benchmark pattern before this
        # table's own caption was tried against the original one. CrossNovo's
        # Table 1 says '9-species-v1' and came back as v2. Each scope is
        # resolved to exhaustion before the next is consulted, exactly as in
        # the caption path below.
        # THE TABLE'S OWN HEADER IS CONSULTED FIRST, then the caption, then the
        # prose. Pairwise Attention spans 'NINE-SPECIES V2' over its columns
        # and its caption names no version at all, so once the caption stopped
        # being contaminated with the header the version fell through to three
        # pages of surrounding text, which name two of them.
        for scope in (header, caption, near):
            if not scope:
                continue
            hits = [(m.group(0), v) for rx, v in VERSION_LEXICON.get(name, [])
                    if (m := rx.search(scope))]
            versions = {v for _, v in hits}
            if len(versions) > 1:
                where = ("the table header" if scope is header
                         else "caption" if scope is caption else "prose")
                raise Reject(f"D3 {where} names several versions of "
                             f"{name}: {sorted(versions)}")
            if versions:
                printed, version = hits[0]
                got = con.execute(
                    "SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
                    (did, version)).fetchone()
                return did, name, (got[0] if got else None), printed
        return did, name, None, None
    found: list[tuple[re.Pattern, str]] = []
    from_prose = False
    for scope in (caption, near):
        found = [(rx, nm) for rx, nm in DATASET_CUES if rx.search(scope)]
        if found:
            from_prose = scope is near
            break
    names = {nm for _, nm in found}
    if len({DATASET_TARGETS.get(n, (n, None)) for n in names}) > 1:   # D2
        if from_prose:
            # THE TABLE ITSELF NAMED NO DATASET, and the page around it
            # discusses several. That is not a multi-dataset table, it is a
            # table whose dataset the paper does not state beside it:
            # publication 17's Table 4 is a comparison against GraphNovo whose
            # caption names no benchmark, on a page that mentions three.
            # Rejecting it threw away a real comparison; picking one of the
            # three would be the nine-species trap committed as data. So the
            # dataset is recorded as NOT STATED, with the candidates kept in
            # `dataset_printed`, exactly as a NULL dataset_version_id already
            # records "the paper did not say which". The schema has to allow a
            # NULL dataset_id for this; see the plan.
            return None, None, None, "not stated; page mentions " + ", ".join(
                sorted(DATASET_TARGETS.get(n, (n, None))[0] for n in names))
        # A CAPTION naming two datasets for one grid is a different thing: the
        # table really does hold both and wants splitting, so this stays fatal.
        raise Reject(f"D2 multi-dataset table {sorted(names)}")
    if not names:
        rows = con.execute(
            "SELECT DISTINCT d.id, d.name FROM publication_dataset pd "
            "JOIN dataset d ON d.id = pd.dataset_id "
            "WHERE pd.publication_id = ? AND d.kind = 'benchmark'", (pub_id,)).fetchall()
        if len(rows) == 1:
            return rows[0][0], rows[0][1], None, None
        raise Reject("D1 dataset unresolved")
    label = names.pop()
    name, pinned = DATASET_TARGETS.get(label, (label, None))
    row = con.execute("SELECT id FROM dataset WHERE name = ?", (name,)).fetchone()
    if not row:                                                   # D1
        raise Reject(f"D1 cue names {name!r}, absent from the dataset table")
    did = row[0]
    if pinned:
        got = con.execute(
            "SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
            (did, pinned)).fetchone()
        return did, name, (got[0] if got else None), label
    # THE CAPTION OUTRANKS THE PROSE, ABSOLUTELY. Scanning both together, with
    # the lexicon in its own order, let the surrounding pages decide: on
    # ContraNovo's Table 1 the caption says '9-species-V1' and the neighbouring
    # prose discusses the re-curated benchmark, and the prose won. That silently
    # filed a claim about the original 2017 benchmark against the revised one,
    # which is the nine-species trap in BENCHMARKS.md committed as data. So each
    # scope is resolved to exhaustion before the next is consulted, and two
    # versions named in ONE scope is an ambiguity, not a race to match first.
    printed = None
    vid = None
    for scope in (caption, near):
        got = [(m.group(0), v) for rx, v in VERSION_LEXICON.get(name, [])
               if (m := rx.search(scope))]
        versions = {v for _, v in got}
        if len(versions) > 1:
            raise Reject(f"D3 caption names several versions of {name}: {sorted(versions)}"
                         if scope is caption else
                         f"D3 prose names several versions of {name}: {sorted(versions)}")
        if versions:
            printed, version = got[0]
            row = con.execute("SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
                              (did, version)).fetchone()
            vid = row[0] if row else None                         # D3: may stay NULL
            break
    return did, name, vid, printed


def reading_text(path) -> str:
    """The PDF's text in READING ORDER, for the basis search.

    pdfplumber's page text drops narrow word spaces and reads straight across a
    two-column page, so the sentence the basis search quoted arrived as
    'TheNine-speciesdataset,themost 6 LIPNovo:ANewComputational...' -- glued,
    interleaved with the other column and the running head -- and the page
    showed it as a tooltip. pdftotext's default mode follows the columns and
    keeps the spaces. Only the basis search reads this; method and dataset
    resolution keep their own text, so their results cannot move. Empty when
    pdftotext fails, and the caller falls back.
    """
    import subprocess
    try:
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"],
                             capture_output=True, text=True, timeout=120)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def find_basis(text: str, method: str) -> tuple[str, str | None, bool]:
    """(basis, the sentence that licensed it, whether cues conflicted).

    B1: 'unclear' unless an explicit sentence says otherwise, and the sentence
    is stored. A method matched by cues of two different kinds stays 'unclear'
    with both recorded, because choosing between them is precisely the error
    BENCHMARKS.md is about.
    """
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
    near = [s for s in sentences
            if re.search(r"(?i)(?<![A-Za-z0-9])" + re.escape(method) + r"(?![A-Za-z0-9])", s)]
    hits: dict[str, str] = {}
    for scope in (near, sentences if not near else []):
        for s in scope:
            for basis, rx in BASIS_CUES:
                if basis not in hits and rx.search(s):
                    hits[basis] = s.strip()[:300]
        if hits:
            break
    if len(hits) > 1:
        return "unclear", " || ".join(f"{k}: {v}" for k, v in hits.items()), True
    if hits:
        basis, cue = next(iter(hits.items()))
        return basis, cue, False
    return "unclear", None, False


# A CAPTION VETO OVERRULED FOR ONE TABLE, where the reviewer has read it and
# the caption's wording misleads. Keyed on (publication, printed label); every
# entry says why. The structural ablation test still applies downstream.
CAPTION_VETO_OVERRIDE: dict[tuple[int, str], str] = {
    # AdaNovo, Table 3, captioned "Ablations on amino acid-level and
    # peptide-level adaptive training strategies". Its rows are Casanovo and
    # AdaNovo variants, so it compares AdaNovo against Casanovo on the Human
    # test set as well as ablating; the reviewer asked for it to be included.
    (32, "Table3"): "compares against Casanovo as well as ablating",
}


# THE PRINTED LAYOUT, transcribed from the page, for a table whose header or
# row groups the text layer cannot give back. These are the same tables whose
# METHODS needed a curated column list (SPANNER_OVERRIDE and friends): what
# defeated the parser defeats the header reader too. Used ONLY for the printed
# layer; nothing standardised is read from it.
#   header      (row, row_end, c0, c1, text). Columns count the printed
#               non-stub columns from 0; -1 is the label column and -2 the
#               row-group column. row_end > row is a cell set across rows.
#   row_groups  (first body row, last body row, text) for a group column.
#   row_labels  the body rows' labels, where the text layer glued the group
#               label into them.
LAYOUT_OVERRIDE: dict[tuple[int, str], dict] = {
    # DiffNovo, Table 1. Mis-typeset IN THE PAPER: the method names wrap
    # inside the two metric spanners, and 'Cascadia' sits alone over the last
    # column. Reproduced as printed rather than tidied.
    (13, "Table 1"): {"header": [
        (0, 0, -1, -1, "DIA Datasets"),
        (0, 0, 0, 3, "Amino Acid Recall DiffNovo DeepNovo-DIA PepNet Cascadia"),
        (0, 0, 4, 6, "Amino Acid Precision DiffNovo DeepNovo-DIAPepNet"),
        (0, 0, 7, 7, "Cascadia")]},
    # BiATNovo, Table 2. The method row arrives as one glued token.
    (45, "Table 2"): {"header": [
        (0, 0, 0, 1, "OC Dataset"), (0, 0, 2, 3, "UTI Dataset"),
        (0, 0, 4, 6, "Plasma Dataset"),
        (1, 1, -1, -1, "Evaluation"),
        (1, 1, 0, 0, "DeepNovo-DIA"), (1, 1, 1, 1, "BiATNovo"),
        (1, 1, 2, 2, "DeepNovo-DIA"), (1, 1, 3, 3, "BiATNovo"),
        (1, 1, 4, 4, "DeepNovo-DIA"), (1, 1, 5, 5, "PepNet"),
        (1, 1, 6, 6, "BiATNovo")]},
    # Casanovo, Table 2: level spanners, then method names (Casanovo over
    # three of its own columns), then the measure.
    (49, "Table2"): {"header": [
        (0, 0, 0, 4, "Peptide-level performance"),
        (0, 0, 5, 8, "Amino acid-level performance"),
        (1, 1, 0, 0, "DeepNovo"), (1, 1, 1, 1, "PointNovo"),
        (1, 1, 2, 4, "Casanovo"),
        (1, 1, 5, 5, "DeepNovo"), (1, 1, 6, 6, "PointNovo"),
        (1, 1, 7, 8, "Casanovo"),
        (2, 2, -1, -1, "Species"),
        (2, 2, 0, 0, "Prec."), (2, 2, 1, 1, "Prec."), (2, 2, 2, 2, "Prec."),
        (2, 2, 3, 3, "Cov."), (2, 2, 4, 4, "Prec. at Cov.=1"),
        (2, 2, 5, 5, "Prec."), (2, 2, 6, 6, "Prec."), (2, 2, 7, 7, "Prec."),
        (2, 2, 8, 8, "Prec at Cov.=1")]},
    # MemNovo, Table 6. Its metric row arrives glued
    # ('AAPr.AARe.Pep.Pr.Pep.Re.').
    (202, "Table 6"): {"header": [
        (0, 0, 0, 3, "InstaNovo"), (0, 0, 4, 7, "InstaNovo + MemNovo"),
        (0, 0, 8, 9, "\u0394"),
        (1, 1, -1, -1, "Species"),
        *[(1, 1, i, i, t) for i, t in enumerate(
            ["AA Pr.", "AA Re.", "Pep. Pr.", "Pep. Re."] * 2
            + ["AA Pr.", "Pep. Re."])]]},
    # CrossNovo, Tables 6 and 7 (antibodies). Metric groups down the side,
    # set across three rows each, and HC/LC chain spanners over the enzymes.
    (9, "Table6"): {"header": [
        (0, 1, -2, -2, "Metrics"), (0, 1, -1, -1, "Methods"),
        (0, 0, 0, 2, "HC"), (0, 0, 3, 3, "LC"), (0, 1, 4, 4, "Average"),
        (1, 1, 0, 0, "AspN"), (1, 1, 1, 1, "Chymotrypsin"),
        (1, 1, 2, 2, "Trypsin"), (1, 1, 3, 3, "AspN")],
        "row_groups": [(0, 2, "Amino Acid Precision"), (3, 5, "Peptide Recall")]},
    (9, "Table7"): {"header": [
        (0, 1, -2, -2, "Metrics"), (0, 1, -1, -1, "Methods"),
        (0, 0, 0, 5, "HC"), (0, 0, 6, 7, "LC"), (0, 1, 8, 8, "Average"),
        *[(1, 1, i, i, t) for i, t in enumerate(
            ["AspN", "Chymo.", "GluC", "LysC", "Proteinase", "Trypsin",
             "AspN", "LysC"])]],
        "row_groups": [(0, 2, "Amino Acid Precision"), (3, 5, "Peptide Recall")],
        "row_labels": ["Casa.V2", "Contra.", "Ours"] * 2},
}


# A CAPTION CORRECTED BY HAND, where the text layer cannot give it back.
# InstaNovo's preprint sets a bootstrap standard error as 'se' with a hat and a
# subscript B; the hat is an unmapped glyph on a line of its own, so the text
# layer reads 's (cid:100) eB'. The wording is the reviewer's, against the page.
_INSTANOVO_CI = ("Confidence intervals are calculated as ±1.96 × se_B where se_B is a "
                 "bootstrap standard error estimated from 10,000 replicates.*We do not "
                 "calculate bootstrap standard errors for the ProteomeTools datasets "
                 "because their size makes it prohibitively costly but also implies "
                 "the standard errors would be very small.")
CAPTION_OVERRIDE: dict[tuple[int, str], str] = {
    (1, "Supplementary Table 2"): "InstaNovo evaluation results on all datasets. " + _INSTANOVO_CI,
    (1, "Supplementary Table 3"): "InstaNovo+ evaluation results on all datasets. " + _INSTANOVO_CI,
    # ReNovo, Table 8: the printed caption is one sentence; the reviewer asked
    # for the paragraph set under the table to be carried with it, because it
    # is what says how the filtered test sets were built and read. The walk had
    # stitched fragments of that paragraph onto the caption.
    (22, "Table8"): ("Performance Comparison on Filtered Test Datasets. From Table 8, we "
                     "observe that the performance of both ReNovo and AdaNovo declines on "
                     "the filtered test datasets, which is expected as the test set contains "
                     "fewer similar data to the training set. However, it is also evident "
                     "that ReNovo still outperforms the state-of-the-art baseline models "
                     "significantly on the same filtered test sets. This clearly indicates "
                     "that the improved performance of ReNovo is due to generalization "
                     "rather than overfitting to the training dataset."),
}


# THE CONVERSE: a table no caption rule refuses that the reviewer judged is
# not a cross-method comparison. Each entry carries the reason, which is
# printed as the refusal.
CAPTION_VETO_ADD: dict[tuple[int, str], str] = {
    # ReNovo, Table 8: its unfiltered rows repeat Table 1's nine-species
    # numbers exactly, and its two filtered test sets exist in no other paper,
    # so it adds nothing a comparison can use (reviewer's call).
    (22, "Table8"): "repeats Table 1 on the unfiltered set; filtered sets unique to this paper",
    # pi-PrimeNovo's Supplementary Table 10 (both versions): PrimeNovo against
    # PepNet split by precursor charge, which the reviewer reads as an
    # ablation over charge states rather than a comparison on a dataset.
    (21, "SupplementaryTable10"): "per-charge breakdown, an ablation over charge states",
    (107, "SupplementaryTable10"): "per-charge breakdown, an ablation over charge states",
    # LIPNovo+, Table 6, "Performance comparison of amino acids with similar
    # masses". Its columns are residues (M(o), F, Q, K), so what it compares
    # is amino acids, not methods; the reviewer rejected it on the caption.
    (432, "Table6"): "compares amino acids with similar masses, not methods",
}


def caption_verdict(caption: str) -> None:
    """Apply the two caption VETOES. There is deliberately no positive test.

    Requiring the caption to announce itself as a comparison was the first
    version of this guard, and it was wrong for a reason already measured
    before the script was written: a caption regex demanding comparison
    vocabulary plus a metric word identifies 15 of 116 papers, where locating
    pages by content identifies 77. Used as a gate it rejected 163 candidate
    tables, including real comparisons whose captions simply read
    'Results on the nine-species benchmark'.

    What makes a table a cross-method comparison is STRUCTURAL and is already
    enforced downstream: at least two columns resolving to different methods
    (N1, G6) with the claiming paper's own method among them (N2). A caption
    is needed for provenance (I7), not for classification.
    """
    if not caption.strip():
        raise Reject("I7 no caption, so no checkable provenance")
    if C3_VETO.search(caption):
        raise Reject("C3 runtime or model-size table")
    if C2_VETO.search(caption):
        raise Reject("C2 ablation, not a cross-method comparison")


# --------------------------------------------------------------------------- #
# Locating the candidate pages
# --------------------------------------------------------------------------- #

DEC = re.compile(r"\b0?\.\d{2,4}\b|\b\d{1,2}\.\d{1,2}\s?%")


def locator(con: sqlite3.Connection) -> re.Pattern:
    """A regex over every method name long enough to be unambiguous.

    This is the LOCATOR, not the resolver: it only has to tell a results page
    from a methods page. Resolution happens later, against what the paper
    itself names in full.
    """
    names = set()
    for name, aliases in con.execute(
            "SELECT name, aliases FROM algorithm WHERE kind IN "
            "('algorithm','post-processor')"):
        for nm in [name] + [a.strip() for a in (aliases or "").split(",") if a.strip()]:
            if nm and len(nm) >= 4:
                names.add(nm)
    return re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)),
                      re.I)


def candidate_pages(pages_text: list[str], rx: re.Pattern,
                    min_methods: int = 3, min_decimals: int = 6) -> list[int]:
    out = []
    for i, t in enumerate(pages_text):
        n_methods = len({m.group(0).lower() for m in rx.finditer(t)})
        n_dec = len(DEC.findall(t))
        # A page that PRINTS A TABLE CAPTION needs only one method: a paper's
        # table of its own results names nothing else. InstaNovo's Extended
        # Data Tables 2 and 3 report InstaNovo alone, per dataset, and the
        # three-method bar never let the miner look at those pages.
        has_caption = any(LABEL_ROW.match(line) for line in t.splitlines())
        if n_dec >= min_decimals and (n_methods >= min_methods
                                      or (has_caption and n_methods >= 1)):
            out.append(i)
    return out


AUDIT_COLUMNS = [
    "publication_id", "title", "pdf_file", "source", "pdf_page", "table_label", "verdict",
    "reason", "caption", "subject_method", "subject_resolved",
    "n_columns", "n_data_rows", "n_cells", "dropped_rows",
    "methods_printed", "methods_resolved", "methods_unresolved",
    "paper_vocabulary", "metrics_resolved", "levels",
    "dataset_printed", "dataset_resolved", "dataset_version_resolved",
    "subsets_canonical",
    "value_min", "value_max", "unit",
    "basis_assigned", "basis_cue", "basis_conflict", "proposed_results",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated publication ids")
    ap.add_argument("--show", action="store_true",
                    help="print each accepted table as a grid")
    ap.add_argument("--limit", type=int, help="stop after N papers (for a quick look)")
    args = ap.parse_args()

    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pdfplumber").setLevel(logging.ERROR)
    try:
        import pdfplumber
    except ModuleNotFoundError:
        print("pdfplumber is needed and is deliberately not a project dependency.\n"
              "Run: uv run --with pdfplumber python3 build_paper_comparisons.py",
              file=sys.stderr)
        return 2

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    pubs = bpl.load_publications(con)
    cov = bpl.coverage(pubs, LIBRARY)
    rx = locator(con)
    index = algorithm_index(con)

    wanted = {int(i) for i in args.ids.split(",")} if args.ids else None
    rows = con.execute("""
        SELECT DISTINCT p.id, p.title FROM publication p
          JOIN publication_algorithm pa ON pa.publication_id = p.id AND pa.role = 'describes'
          JOIN algorithm a ON a.id = pa.algorithm_id
         WHERE a.kind = 'algorithm' ORDER BY p.id""").fetchall()

    audit: list[dict] = []
    tally: collections.Counter = collections.Counter()
    seen_papers = accepted_tables = 0

    for pub in rows:
        if wanted and pub["id"] not in wanted:
            continue
        paths = cov.get(pub["id"]) or []
        if not paths:
            tally["paper: no local PDF"] += 1
            continue
        if args.limit and seen_papers >= args.limit:
            break
        # The main PDF, then each supplement: see review_comparisons.py, which
        # reads the same sources in the same order.
        pub_rec = next((q for q in pubs if q["id"] == pub["id"]), None)
        sources = [("", paths[0])] + (
            [(f"si{n}", sp) for n, sp in
             enumerate(bpl.supplement_files(pub_rec, LIBRARY), start=1)]
            if pub_rec else [])
        whole_parts = []
        for _src, _p in sources:
            try:
                with pdfplumber.open(_p) as _pdf:
                    whole_parts.append("\n".join((pg.extract_text() or "") for pg in _pdf.pages))
            except Exception:
                pass
        whole_all = "\n".join(whole_parts)
        # The basis search reads text in reading order (reading_text).
        basis_all = "\n".join(reading_text(p) for _s, p in sources) or whole_all
        for src, path in sources:
            try:
                pdf = pdfplumber.open(path)
            except Exception as exc:
                tally[f"paper: PDF would not open ({type(exc).__name__})"] += 1
                continue
            with pdf:
                pages_text = [(pg.extract_text() or "") for pg in pdf.pages]
                cands = candidate_pages(pages_text, rx)
                if not cands:
                    if not src:
                        tally["paper: no candidate results page"] += 1
                    continue
                seen_papers += 0 if src else 1
                vocab = paper_vocabulary(whole_all, con)
                whole = basis_all
                subject = con.execute("""
                    SELECT a.id, a.name FROM algorithm a
                      JOIN publication_algorithm pa ON pa.algorithm_id = a.id
                     WHERE pa.publication_id = ? AND pa.role = 'describes'
                     ORDER BY a.id LIMIT 1""", (pub["id"],)).fetchone()

                for pno in cands:
                    page = pdf.pages[pno]
                    near = "\n".join(pages_text[max(0, pno - 1):pno + 2])
                    base = {c: "" for c in AUDIT_COLUMNS}
                    base.update({
                        "publication_id": pub["id"], "title": pub["title"][:110],
                        "pdf_file": path.name, "pdf_page": pno + 1,
                        "source": src or "main",
                        "subject_method": subject["name"] if subject else "",
                        "paper_vocabulary": "|".join(
                            sorted(n for vs in vocab.values() for _, n in vs)),
                    })
                    tables, vetoed, uncap = extract(page, pub["id"])
                    tally["block: uncaptioned, treated as a figure"] += uncap
                    for v in vetoed:
                        tally[f"rejected: {v['reason'].split('(')[0].strip()}"] += 1
                        audit.append({**base, "verdict": "rejected",
                                      "reason": v["reason"],
                                      "table_label": v["table_label"],
                                      "caption": v["caption"][:260]})
                    for tb in tables:
                        row = {**base, "table_label": tb["table_label"],
                               "caption": tb["caption"][:260]}
                        try:
                            accepted_tables += emit(
                                con, row, tb, vocab, index, subject, near, whole,
                                audit, tally, args.show)
                        except Reject as exc:
                            reason = str(exc)
                            tally[f"rejected: {reason.split('(')[0].strip()}"] += 1
                            audit.append({**row, "verdict": "rejected", "reason": reason})

    with AUDIT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=AUDIT_COLUMNS)
        w.writeheader()
        for r in audit:
            w.writerow({k: r.get(k, "") for k in AUDIT_COLUMNS})

    print()
    print(f"  papers with a candidate results page : {seen_papers}")
    print(f"  tables accepted                      : {accepted_tables}")
    print(f"  audit rows                           : {len(audit)}  -> {AUDIT.name}")
    print()
    print("  Every outcome, counted. A filter nobody can see is a filter nobody")
    print("  can correct, so the rejections are the output too:")
    for reason, n in tally.most_common():
        print(f"      {n:>4}  {reason}")
    print()
    print("Phase 0: report only. Nothing was written to denovo.db, by design;")
    print("read the tally before landing the schema.")
    return 0


def resolve_in_label(label: str, vocab, index, subject, pub_id=None):
    """Find the method inside a row label, and return the words left over.

    A row label is not just a method name. ACL-style tables put the metric and
    the architecture class in the same leftmost column, spread down the rows,
    so the label reads 'Amino Deep.', 'Acid Point.', 'Precision Casa.', 'DB
    Peaks', 'NAT Prime.'. Taking the whole string resolved none of them.

    Longest match wins, then the rightmost, because the method sits next to its
    numbers and the group label leads. The leftover words are returned so the
    caller can rebuild the row-group context from them.
    """
    toks = [t for t in re.split(r"\s+", label.strip()) if t]
    if not toks:
        return None, [], ""
    for width in range(len(toks), 0, -1):
        for start in range(len(toks) - width, -1, -1):
            cand = " ".join(toks[start:start + width])
            if norm(cand) in SELF_WORDS:
                if subject:
                    return (subject["id"], subject["name"], None), \
                           toks[:start] + toks[start + width:], cand
                continue
            hit = try_resolve(cand, vocab, index, pub_id)
            if hit:
                # The MATCHED SUBSTRING is returned too, so a display can show
                # what the paper calls the method rather than the whole row
                # label. CrossNovo's tables carry an 'Architect' column of
                # DB / NAT / AT values, and the label reads 'DB Peaks' while
                # the method is 'Peaks'.
                return hit, toks[:start] + toks[start + width:], cand
    return None, toks, ""


# WHERE THE PAGE CANNOT SAY WHICH COLUMN BELONGS TO WHICH METHOD, A PERSON
# SAYS. One printed method name per column, in column order, read off the
# paper by eye. Same idiom as TOOL_ALIASES in build_benchmarks.py: curated
# data with the reasoning beside it, not a heuristic.
#
# An entry is only legitimate where the geometry is genuinely undecidable.
# Both candidate rules were measured on publication 49 and both are wrong:
# 'Casanovo' spans three columns in the peptide block and two in the
# amino-acid block, because the paper reports three metrics for itself and one
# per baseline, and its page carries no \cmidrule to say so. Nearest-centre
# puts column 2 under PointNovo; midpoints between the labels' edges put
# column 2 under PointNovo and column 4 under the next DeepNovo, because
# column 2's centre falls 3 points from one boundary and column 4's lands
# exactly on another.
#
# The names are resolved through the normal resolver, so a typo here is a
# rejection and not a wrong attribution.
SPANNER_OVERRIDE: dict[tuple[int, str], list[str]] = {
    # Casanovo, Table 2. Columns, left to right:
    #   peptide-level:    DeepNovo Prec. | PointNovo Prec. |
    #                     Casanovo Prec. | Casanovo Cov. | Casanovo Prec.@Cov=1
    #   amino-acid-level: DeepNovo Prec. | PointNovo Prec. |
    #                     Casanovo Prec. | Casanovo Prec.@Cov=1
    (49, "Table2"): ["DeepNovo", "PointNovo", "Casanovo", "Casanovo", "Casanovo",
                     "DeepNovo", "PointNovo", "Casanovo", "Casanovo"],
    # Pairwise Attention, Table 2: 'BASE | PA | CASANOVO' under each of
    # 'NINE-SPECIES V1' and 'NINE-SPECIES V2'. The table splits by version
    # (split_by_dataset), and each part's three columns are these methods; the
    # stub's 'TESTSET MODEL' kept the reader from committing to the row.
    (4, "Table2"): ["BASE", "PA", "CASANOVO"],
    # DiffNovo, Tables 1 and 2. The header rows are shredded: 'Cascadia' sits a
    # row above its group and 'DeepNovo-DIAPepNet' arrives as one token, so no
    # reading of the page recovers which column is which.
    # BiATNovo, Table 2. Its whole method header is ONE glued token,
    # 'DeepNovo-DIABiATNovoDeepNovo-DIABiATNovoDeepNovo-DIAPepNetBiATNovo',
    # under 'OC Dataset | UTI Dataset | Plasma Dataset'; PepNet is run on
    # plasma only.
    (45, "Table 2"): ["DeepNovo-DIA", "BiATNovo", "DeepNovo-DIA", "BiATNovo",
                      "DeepNovo-DIA", "PepNet", "BiATNovo"],
    # BiATNovo's earlier preprint (bioRxiv v1), Table 2: 'baseline | BiATNovo'
    # under each of OC, UTI and Plasma. The baseline is DeepNovo-DIA, per the
    # text: "we compared the performance of DeepNovo-DIA (Tran, et al.,
    # 2019) and BiATNovo. Table 2 shows the comparison of the results".
    (283, "Table 2"): ["DeepNovo-DIA", "BiATNovo"] * 3,
    # MemNovo, Table 6: 'InstaNovo | InstaNovo+MemNovo | Delta' over a glued
    # header ('AAPr.AARe.Pep.Pr.Pep.Re.' twice, then 'AAPr.Pep.Re.'). The two
    # Delta columns are DIFFERENCES between the other two and are dropped:
    # None in this list means "not a measurement".
    (202, "Table 6"): ["InstaNovo"] * 4 + ["InstaNovo+MemNovo"] * 4 + [None, None],
    (13, "Table 1"): ["DiffNovo", "DeepNovo-DIA", "PepNet", "Cascadia",
                      "DiffNovo", "DeepNovo-DIA", "PepNet", "Cascadia"],
    (13, "Table 2"): ["DiffNovo", "DeepNovo-DIA", "PepNet", "Cascadia"],
}


# WHAT A PAPER CALLS A METHOD IN ITS OWN TABLE, where no general rule can get
# there. Keyed by (publication, normalised printed label) so an entry cannot
# leak into another paper: 'BASE' means something different in every paper that
# prints it. Read off the paper, like SPANNER_OVERRIDE.
PAPER_LABEL_ALIASES: dict[tuple[int, str], tuple[str, str | None]] = {
    # Pairwise Attention, Table 3. Confirmed by the author of this catalog
    # against the paper: BASE is the base model and PA is the full pairwise
    # attention model, so both are the paper's own method and BASE is a variant
    # of it rather than a third-party baseline.
    (4, "base"): ("Pairwise", "base"),
    (4, "pa"): ("Pairwise", None),
    # RefineNovo. Its tables write PEAKS as 'PeaksNovo', which no general rule
    # reaches, and name two checkpoints of pi-PrimeNovo and Casanovo by a
    # suffix rather than a version token. Confirmed against the paper.
    (16, "peaksnovo"): ("PEAKS", None),
    (16, "primenovocv"): ("\u03c0-PrimeNovo", "CV"),
    (16, "casanovopretrained"): ("Casanovo", "pretrained"),
    # LIPNovo. Its table writes PEAKS with a citation and calls Casanovo
    # 'Baseline', identifiable only from the reference it carries
    # (Yilmaz et al., 2024), which the reviewer confirmed against the paper.
    (17, "peaks"): ("PEAKS", None),
    (17, "baseline"): ("Casanovo", None),
    # AdaNovo, Table 5. '+Re-weight' and '+Focal loss' are alternative
    # training methods APPLIED TO CASANOVO, which the prose states: "both
    # AdaNovo and the first alternative can help improve Casanovo's ability".
    (32, "reweight"): ("Casanovo", "re-weight"),
    (32, "focalloss"): ("Casanovo", "focal loss"),
    # The NeurIPS version (its Table 4) names the re-weighting scheme.
    (440, "reweightupsampling"): ("Casanovo", "re-weight"),
    # MemNovo. A plug-in like CausalNovo, written glued to its base:
    # 'Casanovo+MemNovo' is MemNovo applied to Casanovo, which its captions
    # state ("results with MemNovo applied").
    (202, "casanovomemnovo"): ("MemNovo", "on Casanovo"),
    (202, "instanovomemnovo"): ("MemNovo", "on InstaNovo"),
    # TSARseqNovo. Its table misspells pi-HelixNovo as 'pi-HelexiNovo'.
    (18, "pihelexinovo"): ("\u03c0-HelixNovo", None),
    # InstaNovo-FM, Tables S12 and S13. 'IN-FM' is InstaNovo-FM and 'IN' is
    # InstaNovo; S13's caption states the last: 'We abbreviate "InstaNovo (FM
    # size matched)" to "IN (FM-SM)"'. The bracketed words are the variant.
    (273, "infmfinetuned"): ("InstaNovo-FM", "fine-tuned"),
    # 'From abc to xyz', Table 1: each method with how it was run.
    (225, "peaksdenovo"): ("PEAKS", "de novo"),
    (225, "deepnovodenovo"): ("DeepNovo", "de novo"),
    (225, "deepnovodatabase"): ("DeepNovo", "database search"),
    # SeqNovo, Tables IV and V: 'Seq2Seq' is the paper's own plain
    # sequence-to-sequence model, the architecture SeqNovo improves on, not a
    # published method; the two improved variants are the paper's MLP and
    # attention forms.
    (42, "seq2seq"): ("SeqNovo", "plain Seq2Seq baseline"),
    (42, "seqnovomlp"): ("SeqNovo", "MLP"),
    (42, "seqnovoattention"): ("SeqNovo", "attention"),
    # Deep Novo A+, Fig. 3: DeepNovo with ONE of A+'s two changes each, which
    # are partial versions of A+, and A+ itself.
    (53, "deepnovo+aions"): ("Deep Novo A+", "a-ions only"),
    (53, "deepnovo+validation"): ("Deep Novo A+", "validation set only"),
    (53, "deepnovoa+"): ("Deep Novo A+", None),
    # PLMNovo, Table 1: the protein language model it aligns to, 'ESM-2 8M'
    # and 'ESM-2 650M', which the text layer glues into 'ESM-28M'.
    (434, "plmnovoesm28m"): ("PLMNovo", "ESM-2 8M"),
    (434, "plmnovoesm2650m"): ("PLMNovo", "ESM-2 650M"),
    (273, "infmfromscratch"): ("InstaNovo-FM", "from scratch"),
    (273, "infmfrozen"): ("InstaNovo-FM", "frozen"),
    (273, "inv12"): ("InstaNovo", "v1.2"),
    (273, "in"): ("InstaNovo", None),
    (273, "infmsm"): ("InstaNovo", "FM size matched"),
    # GyroNovo. 'Baseline' is LIPNovo as the authors reproduced it, beside
    # the NovoBench-quoted LIPNovo row: "we report both its NovoBench results
    # and our reproduced results ... We refer to the reproduced version as
    # the 'baseline' throughout the paper and in the result tables."
    (354, "baseline"): ("LIPNovo", "reproduced"),
    # Prime-DiffNovo, Table 1: 'IN v1.1' and 'IN+ v1.1' are InstaNovo and
    # InstaNovo+ at version 1.1, as its prose says ("For the InstaNovo
    # framework, IN+ v1.1 uniformly outperforms IN v1.1").
    (255, "inv11"): ("InstaNovo", "v1.1"),
    (255, "in+v11"): ("InstaNovo+", "v1.1"),
    # LIPNovo+. "we retrain CasaNovo with the same data splits and
    # training/inference configurations as our methods (reported as
    # Baseline)" -- the same reading as LIPNovo's own tables, above.
    (432, "baseline"): ("Casanovo", "retrained"),
}


# A TABLE THAT PRINTS DIFFERENCES INSTEAD OF BASELINES, and what to do about
# it. Recorded per table because the reading is a judgement about one paper's
# design, and each entry says how it was confirmed.
DIFFERENCE_TABLES: dict[tuple[int, str], dict] = {
    # TSARseqNovo, Table 1. "The 'vs' rows indicate the improvement of
    # TSARseqNovo over CasaNovo and pi-HelixNovo for each metric." So each
    # baseline's score is TSARseqNovo's MINUS the printed improvement -- in
    # PERCENTAGE POINTS, not relative per cent, and that is confirmed rather
    # than assumed: TSARseqNovo minus its pi-HelixNovo row reproduces the
    # pi-HelixNovo peptide recall that CrossNovo prints independently, exactly,
    # in all nine species (38.8, 39.2, 47.3, 48.3, 56.0, 56.0, 59.6, 56.8,
    # 62.3); the relative reading matches none of them. The reviewer asked for
    # the baselines to be shown like any other row, with the design footnoted.
    (18, "Table1"): {
        "unit": "percentage points",
        "note": ("The original table prints only TSARseqNovo's scores, each "
                 "followed by two 'vs' rows giving its improvement in percentage "
                 "points over CasaNovo and pi-HelixNovo. The CasaNovo and "
                 "pi-HelixNovo values here are computed as TSARseqNovo minus that "
                 "improvement; they are not printed in the paper. The table also "
                 "misspells pi-HelixNovo as 'pi-HelexiNovo'."),
    },
}


def apply_difference_table(tb: dict, pub_id) -> None:
    """Turn a registered table's 'vs X' rows into derived rows for X, in place.

    Each 'vs' row's cell becomes (the subject row above it) minus (the printed
    improvement), the row's label loses its 'vs', and the cell records the
    expression it came from, so a derived number is never mistaken for a
    printed one. The paper's own bold and underline on those rows marked the
    IMPROVEMENTS, not the scores, so they are cleared.
    """
    spec = DIFFERENCE_TABLES.get((pub_id, (tb.get("registry_label")
                                           or tb.get("table_label") or "").strip()))
    if not spec or tb.get("differences_applied"):
        return
    subject_cells = None
    for r in tb["body"]:
        if DELTA_LABEL.match(r["label"] or ""):
            if subject_cells is None:
                continue
            for k, c in list(r["cells"].items()):
                base = subject_cells.get(k)
                if base is None:
                    r["cells"].pop(k)
                    continue
                v = round(base["value"] - c["value"], 6)
                # At the precision of the numbers it came from: '{:g}' printed
                # 54.0 as '54', which reads like a different, rounder value.
                dp = max(len(x.split(".")[1]) if "." in x else 0
                         for x in (base["printed"], c["printed"]))
                r["cells"][k] = {
                    "value": v, "stddev": None, "printed": f"{v:.{dp}f}",
                    "parts": [(v, "")], "bold": False, "underlined": False,
                    "derived": f"{base['printed']} - {c['printed']}"}
            r["label"] = re.sub(r"(?i)^\s*(vs\.?|versus)\s*", "", r["label"])
            # A MISSPELT name is shown CORRECTED on a row this script built:
            # the row is ours, not the paper's, so it carries the right name,
            # and the design note records what the paper printed.
            fixed = PAPER_LABEL_ALIASES.get((pub_id, norm(r["label"])))
            if fixed:
                r["label"] = fixed[0]
            r["derived_row"] = True
        else:
            subject_cells = r["cells"]
    tb["differences_applied"] = True
    tb["design_note"] = spec["note"]


# WHAT EACH COLUMN IS SCORED ON, where a two-level column header cannot be
# read off the page. CrossNovo's antibody appendix puts the chain on one row
# ('HC  LC'), the enzyme on the next ('AspN Chymotrypsin Trypsin AspN') and an
# 'Average' column header a row above that, so two columns are both headed
# 'AspN' and are distinguished only by which chain group they fall in. The
# chain row is too sparse to assign by position and its page has no cmidrule
# stating the extents, so the grouping is read off the paper by a person.
#
# Without this the two AspN columns are one measurement, which G6 catches.
COLUMN_SUBSET_OVERRIDE: dict[tuple[int, str], list[str]] = {
    # BiATNovo, Table 2: the datasets over its columns (see SPANNER_OVERRIDE).
    (45, "Table 2"): ["OC", "OC", "UTI", "UTI", "Plasma", "Plasma", "Plasma"],
    (283, "Table 2"): ["OC", "OC", "UTI", "UTI", "Plasma", "Plasma"],
    # AdaNovo, Table 3: every number is on the Human test set, per its caption.
    (32, "Table3"): ["Human", "Human", "Human"],
    # CrossNovo, Table 6, WIgG1-Mouse: HC over AspN/Chymotrypsin/Trypsin,
    # LC over AspN, then Average.
    (9, "Table6"): ["HC AspN", "HC Chymotrypsin", "HC Trypsin",
                    "LC AspN", "Average"],
    # CrossNovo, Table 7, IgG1-Human: HC over six enzymes, LC over two,
    # then Average.
    (9, "Table7"): ["HC AspN", "HC Chymo.", "HC GluC", "HC LysC",
                    "HC Proteinase", "HC Trypsin",
                    "LC AspN", "LC LysC", "Average"],
}

# The same, for the row labels when the methods are the columns.
ROW_SUBSET_OVERRIDE: dict[tuple[int, str], list[str]] = {}


# A TABLE THAT STATES ITS METRIC ONLY IN THE BODY PROSE. Curated, because
# guessing a metric from nearby prose is the same error as guessing a dataset
# version from it: a results page names several metrics and picking one is a
# coin toss. The quote that licenses each entry is beside it.
# A single (metric, level) pair applies to the whole table; a LIST applies one
# pair per column, for a table whose metric groups cannot be read off its
# header.
# THE BASIS OF ONE METHOD IN ONE TABLE, where the paper states it in prose
# rather than in a legend a marker can carry. Keyed on (publication, printed
# label) then catalog method name, and every entry quotes the sentence, which
# is what `basis_cue` stores: B1 in the plan says a basis is licensed by a
# sentence, and a curated entry is that sentence written down.
TABLE_BASIS: dict[tuple[int, str], dict[str, tuple[str, str]]] = {
    # AdaNovo, every table: "We reproduce the results of Casanovo with the
    # settings and hypermeters of the original paper and report the published
    # results of DeepNovo and PointNovo as their pretrained weights are
    # unavailable." The basis search read 'published' as released weights and
    # gave all three 'released'. Casanovo's variants (re-weight, focal loss)
    # are AdaNovo's own ablations on Casanovo, retrained with it.
    **{(32, t): {"Casanovo": ("retrained", "We reproduce the results of Casanovo with the "
                                           "settings and hypermeters of the original paper"),
                 "DeepNovo": ("quoted", "report the published results of DeepNovo and "
                                        "PointNovo as their pretrained weights are unavailable"),
                 "PointNovo": ("quoted", "report the published results of DeepNovo and "
                                         "PointNovo as their pretrained weights are unavailable")}
       for t in ("Table1", "Table2", "Table3", "Table4", "Table5")},
    # Casanovo (2022 preprint), Table 2: "we rely on the pre-trained weights of
    # the former [DeepNovo] and the published results of the latter
    # [PointNovo]".
    (49, "Table2"): {
        "DeepNovo": ("released", "we rely on the pre-trained weights of the former"),
        "PointNovo": ("quoted", "and the published results of the latter (Qiao et al., "
                                "2021), since neither PointNovo's pre-trained weights nor "
                                "its predictions for the benchmark data set are available"),
    },
    # RefineNovo: "For a direct and equitable comparison with PrimeNovo ... we
    # utilized its publicly available weights", which the search missed.
    **{(16, t): {"π-PrimeNovo": ("released", "For a direct and equitable comparison with "
                                             "PrimeNovo ... we utilized its publicly "
                                             "available weights.")}
       for t in ("Table1", "Table2")},
    # Pairwise Attention, Tables 2 and 3: the caption says what the Casanovo
    # column is. The basis search, reading garbled text, had made it
    # 'released' from an unrelated sentence; with clean text its cues
    # conflict. The caption settles it.
    **{(4, t): {"Casanovo": ("quoted", "Casanovo is the reported numbers for "
                                       "Casanovo_bm in the original publication")}
       for t in ("Table 2", "Table2", "Table 3", "Table3")},
    # 'From abc to xyz', Table 1: the authors ran PEAKS beside DeepNovo.
    (225, "Table 1"): {
        "PEAKS": ("released", "We also included the de novo sequencing results of PEAKS."),
    },
    # GA-Novo, Table 5: the authors ran the PEAKS software themselves.
    (224, "Table 5"): {
        "PEAKS": ("released", "For each spectrum, the top scored sequence is taken as "
                              "the output of de novo sequencing by PEAKS. PEAKS was run "
                              "with an error tolerance of 0.5 Da and tryptic digestion."),
    },
    # SeqNovo, Table V: the basis search found the right paragraph, glued to
    # the line before it in a two-column layout; this is its sentence.
    (42, "TABLEV"): {
        "DeepNovo": ("retrained", "We trained DeepNovo using the same dataset and "
                                  "compared it with the best results of the above "
                                  "models, as shown in Table. V."),
    },
    # Deep Novo A+, Fig. 3: all four methods were trained on the paper's own
    # random split of one yeast dataset; DeepNovo is the base model it alters.
    # The cue the basis search found was a sentence interleaved with the
    # paper's pseudocode, so the licensing sentence is pinned here.
    (53, "Fig. 3"): {
        "*": ("retrained", "To ensure an unbiased evaluation, the dataset is randomly "
                           "partitioned into training, validation, and testing sets with "
                           "90%, 5% and 5%."),
    },
    # LIPNovo, Table 4. "Training GraphNovo is resource-intensive, making it
    # impractical to retrain on benchmark datasets. To ensure a fair
    # comparison, we trained LIPNovo on the dataset collected in GraphNovo."
    # So GraphNovo's number is GraphNovo's own, on GraphNovo's own data, and
    # this paper ran only its own model. 'unclear' understated what the paper
    # actually says.
    (17, "Table4"): {
        "GraphNovo": ("quoted",
                      "Training GraphNovo is resource-intensive, making it "
                      "impractical to retrain on benchmark datasets"),
    },
    # GyroNovo, Tables 1 and 2: 'Baseline' is LIPNovo as the authors
    # reproduced it, beside LIPNovo's NovoBench numbers.
    # Every other baseline is quoted: "Unless otherwise noted, the reported
    # results are taken from NovoBench." That includes LIPNovo+, whose
    # numbers NovoBench cannot hold -- they match LIPNovo+'s own paper
    # exactly (0.831 / 0.831 / 0.619) -- so 'quoted' is right even where the
    # source the sentence names is not.
    **{(354, t): {
        "LIPNovo@reproduced": (
            "retrained", "we report both its NovoBench results and our "
            "reproduced results to ensure a fair comparison. We refer to the "
            "reproduced version as the \u201cbaseline\u201d"),
        "*": ("quoted", "Unless otherwise noted, the reported results are "
                        "taken from NovoBench.")} for t in ("Table1", "Table2")},
    # LIPNovo+, Tables 2, 3 and 4: 'Baseline' is Casanovo, retrained.
    **{(432, t): {"Casanovo@retrained": (
        "retrained", "we retrain CasaNovo with the same data splits and "
        "training/inference configurations as our methods (reported as "
        "Baseline)")} for t in ("Table2", "Table3", "Table4")},
}


# THE DATASET A TABLE RAN ON, where resolving it is a judgement call about
# prose rather than a cue the table carries. Keyed on (publication, printed
# label) like the other per-table registries, and every entry quotes the
# sentence that licenses it, because the alternative is a guess nobody can
# check. The value is (dataset name as the catalog holds it, as printed).
# A TABLE WHOSE ROWS ARE DATASETS has no single dataset, and filing it under
# the one its caption happens to name is a wrong attribution for most rows.
# InstaNovo's results tables (Supplementary Tables 2-3 of the preprint,
# Extended Data Tables 2-3 of the paper) are eleven to fourteen evaluation
# sets down the side, and only HC-PT and AC-PT are ProteomeTools; the caption
# mentioned ProteomeTools, so the whole table was filed under it. The paper's
# Data Availability statement says where every row lives, so the mapping is a
# registry, per paper, from the printed label to (dataset, version, canonical
# name). A row the registry does not name -- 'mean' -- keeps the table's
# dataset, which for these tables is "one per row".
#
# HC-PT HERE IS NOT NOVOBENCH'S HC-PT. InstaNovo defines it as the full
# high-confidence ProteomeTools set (2.6M spectra, best PSM per peptide); the
# 'HC-PT' every NovoBench-derived paper prints is a 10% subsample of it. Same
# name, different spectra, so in these papers it pins the InstaNovo version.
_INSTANOVO_DEPOSIT = ("InstaNovo enables diffusion-powered de novo peptide "
                      "identification in large scale proteomics experiments")
_INSTANOVO_ROWS: list[tuple[re.Pattern, tuple[str, str | None, str | None]]] = [
    (re.compile(r"(?i)^hela ?single"), (_INSTANOVO_DEPOSIT, None, "HeLa single-shot")),
    (re.compile(r"(?i)^hela ?degradome"), (_INSTANOVO_DEPOSIT, None, "HeLa degradome")),
    (re.compile(r"(?i)nanobod"), (_INSTANOVO_DEPOSIT, None, "Nanobodies")),
    (re.compile(r"(?i)brodae"), (_INSTANOVO_DEPOSIT, None, "Candidatus Scalindua brodae")),
    (re.compile(r"(?i)^immunopeptidomics"),
     ("High-throughput MS-based immunopeptidomics", None, "Immunopeptidomics")),
    (re.compile(r"(?i)^snake ?venom"),
     ("High-throughput proteomics of the 26 medically most important elapids and "
      "vipers from sub-Saharan Africa", None, "Snake venoms")),
    (re.compile(r"(?i)^wound"),
     ("Longitudinal evaluation of biomarkers in wound fluids of venous leg ulcers "
      "treated with a protease-modulating wound dressing", None, "Wound exudates")),
    (re.compile(r"(?i)^herceptin"),
     ('Supplementary Data for "Comprehensive evaluation of peptide de novo sequencing '
      'tools for monoclonal antibody assembly"', None, "Herceptin")),
    (re.compile(r"(?i)^HC-?PT"), ("ProteomeTools", "high-confidence (InstaNovo)", "HC-PT")),
    (re.compile(r"(?i)^AC-?PT"), ("ProteomeTools", "all-confidence (InstaNovo)", "AC-PT")),
]
ROW_DATASET: dict[int, list[tuple[re.Pattern, tuple[str, str | None, str | None]]]] = {
    # pi-PrimeNovo's Supplementary Table 6: four test sets down the side, filed
    # under nine-species because the page mentions it. Its Data Availability
    # names each: HCC from iProX IPX0000937000; IgG1-Human-HC the antibody set
    # MSV000079801; PT from PXD004732 under the paper's OWN split (58,000 test
    # PSMs), which is none of the catalog's ProteomeTools versions, so NULL;
    # and three-species, GraphNovo's test set (A. thaliana, C. elegans,
    # E. coli) "shared by the GraphNovo authors on Zenodo (zenodo.8000316)",
    # the same deposit LIPNovo's Table 4 and LIPNovo+'s Table 5 compare on.
    21: [(re.compile(r"(?i)^HCC$"), ("Proteomics identifies new therapeutic targets of "
                                      "early-stage hepatocellular carcinoma", None, "HCC")),
         (re.compile(r"(?i)^IgG1"), ("Monoclonal antibody de novo assembly", None,
                                     "IgG1-Human-HC")),
         (re.compile(r"(?i)^PT$"), ("ProteomeTools", None, "PT")),
         (re.compile(r"(?i)^three-?species"), ("GraphNovo dataset and checkpoint", None,
                                               "Three-species"))],
    # pi-PrimeNovo's preprint prints the same table.
    107: [],
    # ReNovo's Table 8: the nine-species test set, whole and with near
    # duplicates of training peptides removed. "(Original)" means UNFILTERED
    # here, not the 2017 original version, so the version stays NULL as in
    # ReNovo's Table 1. The canonical names say what each set is.
    22: [(re.compile(r"(?i)^nine-?species\s*dataset\s*\(original\)"),
          ("Nine-species benchmark", None, "test set, unfiltered")),
         (re.compile(r"(?i)^nine-?species\s*test\s*dataset\s*\(>\s*3\)"),
          ("Nine-species benchmark", None, "test set, min. Levenshtein distance to training >= 3")),
         (re.compile(r"(?i)^nine-?species\s*test\s*dataset\s*\(>\s*5\)"),
          ("Nine-species benchmark", None, "test set, min. Levenshtein distance to training >= 5"))],
    # PLMNovo's Table 1: both test sets come from "Data for 'accounting for
    # digestion enzyme bias in Casanovo'" (its reference [32], Zenodo
    # 12587317): the MSKB split's 200,000 tryptic test spectra, and the
    # held-out non-tryptic multi-enzyme set.
    # MSKB is MassIVE-KB ("originates from the MassIVE Knowledge Base ... we
    # refer to this dataset as the MSKB dataset"), under the train/test split
    # Melendez et al. published in that deposit. The split mixes the spectra
    # Casanovo v4 trained on (MassIVE-KB v1 plus v2.0.15), so neither catalog
    # version is it and the version stays NULL.
    434: [(re.compile(r"(?i)^MSKB"), ("MassIVE-KB", None, "MSKB (tryptic)")),
          (re.compile(r"(?i)^multi-?enzyme"), ("Casanovo digestion enzyme bias data", None,
                                              "Multi-enzyme (non-tryptic)"))],
    # InstaNovo-FM's six validation sets ARE InstaNovo's application sets:
    # "we made use of the biological validation dataset featuring six smaller
    # sets ... we exclude the Immuno and Herceptin datasets used in the
    # InstaNovo paper", eight minus two. Four keep InstaNovo's names (TPL
    # Antibodies is glossed "(nanobodies)"); GluC is InstaNovo's HeLa
    # degradome, which that paper calls the "HeLa GluC degradome" (Extended
    # Data Fig. 7); which leaves Hela QC as HeLa single-shot. Winnow's 'HeLa
    # QC' is a different set and is not meant here.
    273: [(re.compile(r"(?i)^GluC$"), (_INSTANOVO_DEPOSIT, None, "HeLa degradome")),
          (re.compile(r"(?i)^hela ?qc$"), (_INSTANOVO_DEPOSIT, None, "HeLa single-shot")),
          (re.compile(r"(?i)^TPL"), (_INSTANOVO_DEPOSIT, None, "Nanobodies"))]
         + [e for e in _INSTANOVO_ROWS if e[1][2] in
            ("Candidatus Scalindua brodae", "Snake venoms", "Wound exudates")],
    # The preprint's yeast row is "Exc. Yeast": trained on nine-species
    # excluding yeast, evaluated on yeast, which in 2023 could only be the
    # original 2017 benchmark.
    1: _INSTANOVO_ROWS + [(re.compile(r"(?i)yeast"),
                           ("Nine-species benchmark", "original (DeepNovo, 2017)",
                            "Saccharomyces cerevisiae"))],
    # The paper prints Yeast, Bacillus and Mouse and does not say which curated
    # version they came from, so the version stays NULL: the finding, not a gap.
    2: _INSTANOVO_ROWS + [(re.compile(r"(?i)^(yeast|bacillus|mouse)"),
                           ("Nine-species benchmark", None, None))],
}


ROW_DATASET[107] = ROW_DATASET[21]

# A SUBSET THE DATASET'S OWN NAME QUALIFIES. pi-PrimeNovo's Supplementary
# Table 7 heads its columns 'AspN', 'Trypsin', ... in "the IgG1-Human-HC
# dataset": every digest is of the heavy chain. CrossNovo prints the same
# digests as 'HC AspN', 'HC Trypsin', so without the chain the two papers'
# numbers never meet on one subset. The prefix is the dataset's name, not a
# guess.
SUBSET_PREFIX: dict[tuple[int, str], str] = {
    (21, "SupplementaryTable7"): "HC",
    (107, "SupplementaryTable7"): "HC",
}


def row_dataset(con, pub_id, label: str) -> tuple[int | None, int | None, str | None] | None:
    """(dataset_id, dataset_version_id, canonical) for a row label, or None."""
    for rx, (name, version, canon) in ROW_DATASET.get(pub_id, []):
        if rx.search((label or "").strip()):
            got = con.execute(
                "SELECT d.id, v.id FROM dataset d LEFT JOIN dataset_version v "
                "ON v.dataset_id = d.id AND v.version = ? WHERE d.name = ?",
                (version, name)).fetchone()
            if not got or (version and got[1] is None):
                raise Reject(f"D1 ROW_DATASET names {name!r} {version or ''}, absent")
            vid = got[1]
            if not version:
                # A deposit with ONE version has nothing to be ambiguous about;
                # a benchmark with several stays NULL, the finding.
                only = con.execute("SELECT id FROM dataset_version WHERE dataset_id = ?",
                                   (got[0],)).fetchall()
                vid = only[0][0] if len(only) == 1 else None
            return got[0], vid, canon
    return None


# WHAT A READER OF ONE TABLE NEEDS TO KNOW that the table itself does not say:
# a label the paper uses in an unexpected sense, or a number the paper's own
# text contradicts. Appended to the design note, so it reaches the review page
# and paper_comparison.design_note. Each entry quotes its evidence with a page.
_EXC_YEAST = ("'Exc. Yeast' is measured ON yeast: it names the leave-one-out "
              "setup in which the model is trained on the other eight species. The "
              "paper: 'the models were trained on nine-species excluding yeast, and "
              "then evaluated on yeast' (p. 8), and 'For Yeast, we use the splits as "
              "defined in DeepNovo and PointNovo'. Fig. 2d's caption reads 'accuracy "
              "... on the high-resolution nine-species dataset excluding yeast' before "
              "saying 'evaluated on yeast', which invites the opposite reading.")
_FM_SETS = ("No accession is printed for the six validation sets. They are mapped "
            "from the Methods, which call them InstaNovo's application datasets "
            "minus 'the Immuno and Herceptin datasets': GluC is InstaNovo's 'HeLa "
            "GluC degradome', TPL Antibodies its nanobodies, and Hela QC, by "
            "elimination, its HeLa single-shot set.")
TABLE_NOTE: dict[tuple[int, str], str] = {
    (53, "Fig. 3"): ("The data are the nine-species benchmark's yeast submission "
                     "(PXD003868, the paper's reference [33]), but NOT under the "
                     "benchmark's protocol: all 277,077 spectra were re-searched with "
                     "PEAKS DB and split at random 90/5/5, so the models were trained and "
                     "tested on the same species, very likely with shared peptides. That "
                     "is why peptide accuracy reaches 0.91 here against roughly 0.5 for "
                     "yeast held out of nine-species training. These numbers are not "
                     "comparable with leave-one-species-out results on the benchmark."),
    (22, "Table8"): ("The filtered test sets are defined on p. 14: the authors computed, "
                     "for each nine-species test peptide, the minimum Levenshtein distance "
                     "to any training peptide, and 'filtered out test sequences with a "
                     "minimum Levenshtein distance <3 and <5, respectively, resulting in "
                     "Nine-species Test Dataset (>3) and Nine-species Test Dataset (>5)'. "
                     "Removing distances below 3 keeps distances of 3 or more, so the "
                     "printed '>3' and '>5' mean >= 3 and >= 5. '(Original)' is the "
                     "unfiltered test set, not the 2017 original version of the benchmark."),
    (434, "Table 1"): ("MSKB is MassIVE-KB, under the train/test split (200,000 tryptic "
                       "test spectra) published with Melendez et al.'s enzyme-bias data "
                       "on Zenodo 12587317, the paper's reference [32]; that split mixes "
                       "MassIVE-KB v1 and v2.0.15, so no version is recorded. The "
                       "multi-enzyme rows are the held-out non-tryptic set from the same "
                       "deposit."),
    (273, "Table S12"): _FM_SETS,
    (273, "Table S13"): _FM_SETS,
    (1, "Supplementary Table 2"): _EXC_YEAST,
    (1, "Supplementary Table 3"): _EXC_YEAST + (
        " THE TEXT AND THIS TABLE DISAGREE: p. 10 says that after fine-tuning on "
        "nine-species exc. yeast 'IN+ reaching a peptide-level accuracy of 54.6%', "
        "while this table's Exc. Yeast row prints 0.579 \u00b1 0.006 peptide accuracy "
        "(and 0.556 AUC). The two do not match, and the paper does not say which run "
        "each comes from. The table's value is recorded, as printed."),
}


# FIGURES WHOSE VALUES ARE PRINTED ON THE CHART. A bar chart that labels each
# bar with its value carries the same numbers a table would, and those labels
# are in the text layer, so they are EXTRACTED, never transcribed: the entry
# states only the layout -- page, legend order (which is the bar order within
# each category), the x-range and metric of each panel, and categories to
# leave out -- and bar_figure_grid() reads the values off the page. Opt-in per
# figure, because most charts print no values, and a value read from a chart
# is marked extraction='figure' all the way into the database.
FIGURE_TABLES: dict[tuple[int, str], dict] = {
    # Deep Novo A+, Fig. 3: amino-acid and peptide accuracy of DeepNovo, of
    # DeepNovo with each of A+'s two changes alone (a-ions; validation-set
    # early stopping), and of A+ itself. 'train' is accuracy on the TRAINING
    # set, not an evaluation, so it is left out.
    (53, "Fig. 3"): {
        "page": 5,
        "caption": ("Average accuracy at the amino acid level (left) and the peptide "
                    "level (right) of the four methods."),
        "series": ["DeepNovo", "DeepNovo+A_Ions", "DeepNovo+Validation", "DeepNovo A+"],
        "panels": [(60.0, 306.0, "Amino acid accuracy"), (306.0, 590.0, "Peptide accuracy")],
        "categories": r"^(train|test_length\(\d+\))$",
        "drop": ["train"],
        "bbox": (60.0, 50.0, 585.0, 262.0),
        # Read, then refused at the reviewer's call: the yeast data were split
        # at random rather than held out (see its TABLE_NOTE), so the only
        # thing it supports is DeepNovo against Deep Novo A+ inside this paper.
        "veto": ("random within-yeast split; supports only DeepNovo against "
                 "Deep Novo A+ inside this paper"),
    },
}


def bar_figure_grid(page, spec: dict) -> list[list[str]]:
    """A registered bar chart's printed values as a grid, header rows first.

    Each value label is assigned to the x-axis category nearest its centre,
    then ordered left to right within the category, which is the legend's
    order. Rotated labels come out of the text layer reversed ('1489.0' is
    0.9841) and are turned back. A category holding any other number of
    labels than there are series raises: a missing or extra label would
    silently shift every value after it into the wrong series.
    """
    words = page.extract_words()
    cat_rx = re.compile(spec["categories"])
    head_metric, head_cat = ["Method"], ["Method"]
    cols: list[list[str]] = []
    for x0, x1, metric in spec["panels"]:
        cats = sorted((w for w in words if cat_rx.match(w["text"])
                       and x0 <= (w["x0"] + w["x1"]) / 2 < x1), key=lambda w: w["x0"])
        labels = []
        for w in words:
            if w.get("upright", True) or not (x0 <= (w["x0"] + w["x1"]) / 2 < x1):
                continue
            txt = w["text"][::-1]
            if numeric(txt) is not None:
                labels.append((w, txt))
        by_cat: dict[str, list] = {c["text"]: [] for c in cats}
        for w, txt in labels:
            cx = (w["x0"] + w["x1"]) / 2
            near = min(cats, key=lambda c: abs((c["x0"] + c["x1"]) / 2 - cx))
            by_cat[near["text"]].append((cx, txt))
        for c in cats:
            vals = [t for _x, t in sorted(by_cat[c["text"]])]
            if len(vals) != len(spec["series"]):
                raise Reject(f"F1 figure category {c['text']!r} carries {len(vals)} "
                             f"value labels for {len(spec['series'])} series")
            if c["text"] in spec.get("drop", []):
                continue
            head_metric.append(metric)
            head_cat.append(c["text"])
            cols.append(vals)
    rows = [[name] + [col[i] for col in cols] for i, name in enumerate(spec["series"])]
    return [head_metric, head_cat] + rows


# COLUMNS THAT ARE NOT MEASUREMENTS, named where the header cannot say so:
# column index (after the stub) -> why. The cells stay in the printed layer
# as a not-recorded column, exactly like a year or a speed column.
NOT_RECORDED_COLUMNS: dict[tuple[int, str], dict[int, str]] = {
    # GA-Novo, Table 5: the last two columns are 'avg. len. of partial
    # matches' and 'avg. len. of predicted sequences', lengths in residues.
    # Their two-line headers interleave, so 'avg. len.' attaches to the wrong
    # column and the header rule caught only one of them.
    (224, "Table 5"): {3: "length", 4: "length"},
}


# A TABLE'S OWN METHOD, where the catalog's link for the paper names
# something else. 'Protein identification with deep learning: from abc to
# xyz' is catalogued as describing a deep-learning identification primer, but
# its Table 1 is DeepNovo's authors running DeepNovo (de novo and database
# search) beside PEAKS. Without this the table has no column for the paper's
# own method and N2 refuses it.
TABLE_SELF: dict[tuple[int, str], list[str]] = {
    (225, "Table 1"): ["DeepNovo"],
}


TABLE_DATASET: dict[tuple[int, str], tuple[str | None, str]] = {
    (434, "Table 1."): (None, "one per row group (MassIVE-KB; Zenodo 12587317)"),
    (434, "Table 1"): (None, "one per row group (MassIVE-KB; Zenodo 12587317)"),
    # pi-PrimeNovo's four-test-set table, likewise.
    (21, "SupplementaryTable6"): (None, "one per row (Data availability)"),
    (107, "SupplementaryTable6"): (None, "one per row (Data availability)"),
    # Deep Novo A+, Fig. 3: "the high-resolution Saccharomyces Cerevisiae
    # (Baker's yeast) dataset ... 5 raw files and 277,077 spectra acquired from
    # the Thermo Scientific Q-Exactive", split at random 90/5/5. No accession.
    # Its reference [33] is Seidel et al., the yeast PBP1 study, i.e. PXD003868,
    # the nine-species benchmark's own yeast provenance submission. So it is
    # the benchmark at NO KNOWN VERSION (the pNovo 3 rule): the raw files were
    # re-searched with PEAKS DB and split at random, which is none of the
    # curated versions.
    (53, "Fig. 3"): ("Nine-species benchmark",
                     "yeast only, from PXD003868 (Seidel et al.), 5 raw files and 277,077 "
                     "spectra re-searched with PEAKS DB, random 90/5/5 split"),
    # SeqNovo, Tables IV and V: "an open-source peptide mass spectrometry
    # library dataset stored in the MSP format [36]", reference [36] being
    # Zolg et al., ProteomeTools. Screened to 163,924 spectra and split 9:1 by
    # the paper itself, which is none of the catalog's versions.
    (42, "TABLEIV"): ("ProteomeTools", "MSP spectral library (Zolg et al. 2017), "
                      "screened to 163,924 spectra, own 9:1 split"),
    (42, "TABLEV"): ("ProteomeTools", "MSP spectral library (Zolg et al. 2017), "
                     "screened to 163,924 spectra, own 9:1 split"),
    # GA-Novo, Table 5: "120 MS/MS spectra" of "the comprehensive full
    # factorial LC-MS/MS benchmark dataset ... 50 protein samples extracted
    # from Escherichia coli K12" (Wessels et al. 2012), which is not catalogued.
    (224, "Table 5"): (None, "120 spectra of the Wessels et al. 2012 full factorial "
                       "LC-MS/MS benchmark (E. coli K12, LTQ-FT); no accession"),
    # 'From abc to xyz', Table 1: "a dataset of Saccharomyces cerevisiae
    # proteome [18]" -- Hebert et al. 2014, The one hour yeast proteome --
    # on an Orbitrap Fusion, with PEAKS DB results as ground truth.
    (225, "Table 1"): (None, "S. cerevisiae proteome of Hebert et al. 2014 (The one "
                       "hour yeast proteome), Orbitrap Fusion HCD; no accession"),
    # InstaNovo's results tables: one dataset PER ROW, from ROW_DATASET.
    (1, "Supplementary Table 2"): (None, "one per row (Data availability)"),
    (1, "Supplementary Table 3"): (None, "one per row (Data availability)"),
    (2, "Extended Data Table 2"): (None, "one per row (Data availability)"),
    (2, "Extended Data Table 3"): (None, "one per row (Data availability)"),
    # LIPNovo, Table 4. Its caption names no benchmark and its page mentions
    # three, so the cue scan could only report the ambiguity. The paper says
    # which: "Training GraphNovo is resource-intensive, making it impractical
    # to retrain on benchmark datasets. To ensure a fair comparison, we trained
    # LIPNovo on the dataset collected in GraphNovo." So the comparison is on
    # GraphNovo's OWN deposit, which this catalog holds as dataset 25, and on
    # none of the three benchmarks the page discusses.
    (17, "Table4"): ("GraphNovo dataset and checkpoint",
                     "the dataset collected in GraphNovo"),
    # LIPNovo+, Table 5: "Comparison with state-of-the-art methods on
    # GraphNovo dataset [11]" -- the same comparison as LIPNovo's Table 4,
    # extended by LIPNovo+, on the same deposit.
    (432, "Table5"): ("GraphNovo dataset and checkpoint",
                      "GraphNovo dataset [11]"),
    # Transformer-DIA (the arXiv postprint of DiaTrans), TABLE I. Already
    # resolved to MSV000082368 through the paper's own catalog link; what this
    # adds is the SPLIT, in the paper's words: "three distinct DIA datasets of
    # Homo sapiens: urinary tract infection (UTI), ovarian cysts (OC), and
    # plasma ... 206,477 spectra, 203,780 spectra, and 1,097,400 spectra ...
    # randomly partitioned into separate training, validation, and testing
    # sets with ratios of 0.9, 0.05, and 0.05 ... no shared peptide sequences".
    # That is DeepNovo-DIA's own protocol, which states the same 90/5/5 and
    # "did not share common peptides". Whether it is the same DRAW is not
    # stated -- "randomly" suggests a fresh one -- so no version is asserted.
    # BiATNovo, Table 2: OC, UTI and plasma are the three DIA datasets of
    # MSV000082368, the DeepNovo-DIA deposit.
    (45, "Table 2"): ("De novo sequencing of DIA data",
                      "MSV000082368: OC, UTI and plasma"),
    # CrossNovo, Tables 6 and 7: antibodies, as the captions say ("... on
    # WIgG1-Mouse", "... on IgG1-Human"). With no dataset named in a header,
    # the cue scan fell back to the paper's nine-species link and filed both
    # under nine-species, which put eight enzyme columns into that benchmark.
    # The paper gives no accession, so the antibody is recorded as printed.
    (9, "Table6"): (None, "WIgG1-Mouse antibody (CrossNovo); no accession stated"),
    (9, "Table7"): (None, "IgG1-Human antibody (CrossNovo); no accession stated"),
    # pi-PrimeNovo's preprint, Supplementary Table 3: "Peptide recall on the
    # nine-species benchmark dataset" -- the original, by its caption; its
    # Supplementary Table 5 is the one "on the revised nine-species benchmark",
    # and that caption, on a page nearby, leaked 'revised' into this table's
    # version. The same table in the published supplement resolves with no
    # version, which is what the caption supports.
    (107, "Supplementary Table 3"): ("Nine-species benchmark",
                                     "the nine-species benchmark dataset"),
    # pi-PrimeNovo, Supplementary Table 10 (both versions): "Peptide recall on
    # the Pepnet test dataset under zero-shot setting". The catalog holds no
    # PepNet test set, so it is recorded as printed.
    **{(pid, "SupplementaryTable10"): (None, "the PepNet test dataset, zero-shot; "
                                             "no accession stated") for pid in (21, 107)},
    # ReNovo, Table 8: every row's test set is a filtered nine-species split,
    # 'Nine-species Dataset (Original)', '(>3)' and '(>5)'.
    (22, "Table8"): ("Nine-species benchmark", "Nine-species, original and two "
                     "filtered test sets (>3, >5)"),
    (283, "Table 2"): ("De novo sequencing of DIA data",
                       "MSV000082368: OC, UTI and plasma"),
    # InstaNovo-FM, Tables S12 and S13: "the six held-out biological
    # validation datasets", one per row. No accession is printed, but the
    # Methods say what they are, so ROW_DATASET maps them (see there).
    (273, "Table S12"): (None, "one per row (InstaNovo's application datasets)"),
    (273, "Table S13"): (None, "one per row (InstaNovo's application datasets)"),
    (30, "TABLE I"): ("De novo sequencing of DIA data",
                      "MSV000082368: UTI, OC and plasma (206,477 / 203,780 / "
                      "1,097,400 spectra), each split randomly 0.9 / 0.05 / 0.05 "
                      "with no peptide shared between sets -- DeepNovo-DIA's "
                      "protocol; whether the same draw is not stated"),
}


# (metric, level) for a table whose own page does not say it. Three shapes:
# one pair for the whole table, a LIST of pairs one per column, or
# {"rows": [...]} for one per row that carries values.
TABLE_METRIC: dict[tuple[int, str],
                   tuple[str, str] | list[tuple[str, str]] | dict] = {
    # Pairwise Attention, Table 3. Its caption says only "Performance after
    # training the models on the nine-species V2 dataset"; the paragraph above
    # it says "we tested on each of the species in the nine-species dataset and
    # report PEPTIDE PRECISION AT 100% COVERAGE".
    (4, "Table3"): ("precision@cov1", "peptide"),
    # DiffNovo, Table 1. Four columns of amino-acid recall then four of
    # amino-acid precision. Its header rows are shredded by the text layer --
    # 'Cascadia' lands a row above its group and 'DeepNovo-DIAPepNet' arrives
    # glued -- so neither the methods nor the metric groups can be read from
    # the page. Both are recorded from the paper, and the reviewer's own
    # reading of the prose confirms the column order: DiffNovo's UTI recall
    # (0.665) against the next best (0.566), and its UTI precision (0.675)
    # against the next best (0.612).
    (13, "Table 1"): [("recall", "amino acid")] * 4 + [("precision", "amino acid")] * 4,
    # GA-Novo, Table 5: 'Precision' and 'Recall' are equations 7 and 8, which
    # "measure the accuracy of the results in amino acid level"; the third
    # column is equation 9, recall "in peptide level". The split header
    # 'recall / pep. level' had lent its level to Precision too.
    (224, "Table 5"): [("precision", "amino acid"), ("recall", "amino acid"),
                       ("recall", "peptide")],
    # RefineNovo, Table 6. Its caption says only "Performance comparison on
    # the NovoBench benchmark (yeast test species)". The metric is NovoBench's
    # peptide-level precision, which the numbers themselves confirm: its
    # Casanovo* reads 0.48 and BENCHMARKS.md records NovoBench's retrained
    # Casanovo at 0.481 on nine-species.
    (16, "Table6"): ("precision", "peptide"),
    # MemNovo, Table 6: AA Pr., AA Re., Pep. Pr., Pep. Re. under each model;
    # the last two (Delta) columns carry no measurement.
    (202, "Table 6"): [("precision", "amino acid"), ("recall", "amino acid"),
                       ("precision", "peptide"), ("recall", "peptide")] * 2
                      + [("precision", "amino acid"), ("recall", "peptide")],
    # TSARseqNovo, Table 1. Three groups of three rows, and the metric of each
    # group is stated only POSITIONALLY, in the caption: "peptide precision
    # (top), amino acid precision (middle), and amino acid recall (bottom)".
    # Nothing in the grid says it -- there is no group label and no repeat of a
    # method to delimit the groups -- so it cannot be read off the page. Nine
    # entries, three per group, because DIFFERENCE_TABLES turns each group's
    # two 'vs' rows into derived CasaNovo and pi-HelixNovo rows.
    # LIPNovo, Table 3. Its spanner reads 'AminoAcid | Peptide | PTM' over six
    # columns and its own header row arrives shredded one character at a time
    # -- 'P r e c . R e c a ll P r e c . A U C P r e c . R e c a ll' -- so the
    # metric cannot be read from the page even though the level can. The order
    # matches its Tables 1 and 2, and the paper's own prose confirms the three
    # precision columns: "LIPNovo outperforms the baseline by +5.3%, +4.5%, and
    # +2.3% in precision across the three performance levels", against a
    # measured +5.3% on column 0 from this parse.
    (17, "Table3"): [("precision", "amino acid"), ("recall", "amino acid"),
                     ("precision", "peptide"), ("auc", "peptide"),
                     ("precision", "ptm"), ("recall", "ptm")],
    (18, "Table1"): {"rows": [("precision", "peptide")] * 3
                             + [("precision", "amino acid")] * 3
                             + [("recall", "amino acid")] * 3},
}


def try_resolve(label, vocab, index, pub_id=None):
    """resolve_method() as an Optional, for deciding orientation."""
    try:
        return resolve_method(label, vocab, index, pub_id)
    except Reject:
        return None


def orientation(tb, vocab, index, subject, pub_id=None) -> tuple[str, dict, dict, dict]:
    """Decide whether the METHODS are the columns or the rows.

    This is not cosmetic and it was the single largest source of loss. The
    literature uses both layouts freely:

      ContraNovo Table 1   methods across the top, species down the side
      CrossNovo  Table 2   species across the top, methods down the side
      DiffuNovo  Table 2   metrics across the top under a dataset spanner,
                           methods down the side

    Assuming methods-in-columns rejected 47 of the 89 captioned tables for
    'unresolved header', when the headers were never method names: they were
    'Mouse', 'Yeast', 'Prec.', 'AUC'. Both axes are now tried and whichever
    carries at least two distinct methods wins.
    """
    n = len(tb["edges"])
    # A CURATED COLUMN-TO-METHOD MAPPING WINS over any reading of the page.
    over = SPANNER_OVERRIDE.get((pub_id, (tb.get("registry_label") or tb["table_label"] or "").strip()))
    if over:
        if len(over) != n:
            raise Reject(f"N1 SPANNER_OVERRIDE lists {len(over)} columns, "
                         f"the table has {n}")
        got = {}
        for k, printed in enumerate(over):
            if printed is None:
                continue                  # a difference column, not a measurement
            if norm(printed) in SELF_WORDS and subject:
                got[k] = (subject["id"], subject["name"], None)
            else:
                got[k] = resolve_method(printed, vocab, index, pub_id)
        return "columns", got, {}, {k: v for k, v in enumerate(over) if v is not None}

    # THE METHOD NAMES MAY BE THE SPANNER. ContraNovo's Table 1 has the methods
    # as column headers with the metric spanning them; Casanovo's Table 2 is
    # the other way round -- 'DeepNovo PointNovo Casanovo' spans, and each
    # column's own header is 'Prec.' or 'Cov.' -- because Casanovo reports
    # three metrics for itself and one for each baseline, so the names cannot
    # align one-to-one with the columns. Reading only the column header found
    # no methods at all and rejected the most-compared-against paper in the
    # field. The column's own header is tried first, so a table of the
    # ContraNovo shape is unaffected.
    # ONE DICT PER AXIS. A single dict keyed by index was written by both the
    # column loop and the row loop, and since the row loop runs second its
    # labels overwrote the columns'. Publication 4 has BASE | PA | CASANOVO
    # across the top and species down the side, and the review page showed its
    # methods as 'ApisMellifera, BacillusSubtilis, CandidatusEndoloripes'.
    printed_cols: dict = {}
    printed_rows: dict = {}
    cols, method_source = {}, "own"
    amb = tb.get("span_ambiguous") or set()
    span_blocked = False
    for source in ("own", "span"):
        if source == "span" and amb:
            # The spanner's grouping was guessed by proximity, so it cannot say
            # which column belongs to which method. SKIP it and carry on: most
            # spanners carry the METRIC, not the methods, and aborting here
            # stopped the row axis from ever being tried, which took 7 tables
            # that read correctly off the accepted list.
            span_blocked = True
            continue
        got = {}
        for k in range(n):
            head = " ".join(tb[source].get(k, [])).strip()
            printed_cols[k] = head
            if norm(head) in SELF_WORDS and subject:
                got[k] = (subject["id"], subject["name"], None)
            else:
                got[k] = try_resolve(head, vocab, index, pub_id)
        if len({(v[0], v[2] or "") for v in got.values() if v}) >= 2:
            cols, method_source = got, source
            break
        if source == "own":
            cols = got
    rows, leftovers = {}, {}
    deltas: set[int] = set()
    for i, r in enumerate(tb["body"]):
        hit, rest, matched = resolve_in_label(
            r["label"], vocab, index, subject, pub_id)
        # Matched against the RAW label only. Running it over the unsquashed
        # form as well put the separator back inside 'DiffNovo' ('Diff Novo')
        # and matched it; the camel-case branch above already reads
        # 'vsCasaNovo' straight off the raw text, so the second attempt bought
        # nothing and cost a method name.
        if DELTA_LABEL.match(r["label"] or ""):
            deltas.add(i)
        rows[i] = hit
        printed_rows[i] = matched or r["label"]
        # Words from an interstitial label row count as this row's leftovers:
        # they are the group label, just set on a line of their own.
        leftovers[i] = rest + list(r.get("extra") or [])
    # A PLUG-IN ROW IS THE SUBJECT APPLIED TO THE BASE ABOVE IT. CausalNovo
    # prints its results as '+CausalNovo (Ours)' under each base model it is
    # added to -- CasaNovo, AdaNovo, pi-HelixNovo -- so three rows resolved to
    # one bare 'CausalNovo' and collided (G6). Each takes the base it sits
    # under as its variant, 'on Casanovo' and so on, which is what the row
    # means. Only a '+' row that resolves to the SUBJECT is touched: AdaNovo's
    # '+Re-weight' is an alternative applied to Casanovo and is aliased as such.
    if subject:
        last_base = None
        for i in range(len(tb["body"])):
            v = rows.get(i)
            lab = (tb["body"][i]["label"] or "").lstrip()
            if v and lab.startswith("+") and v[0] == subject["id"] and last_base:
                rows[i] = (v[0], v[1], f"on {last_base}")
            elif v and not lab.startswith("+"):
                # The row DIRECTLY above, as printed, marker included: the line
                # above each '+CausalNovo' is the RETRAINED base ('†CasaNovo'),
                # and the plug-in is applied to that model, not the quoted one.
                last_base = CITE_TAIL.sub("", lab).strip() or v[1]
    n_col = len({(v[0], v[2] or "") for v in cols.values() if v})
    # Delta rows do not count towards the row axis carrying methods, and are
    # dropped from it, for the reason DELTA_LABEL records.
    kept = {i: v for i, v in rows.items() if i not in deltas}
    n_row = len({(v[0], v[2] or "") for v in kept.values() if v})
    if n_col >= 2 and n_col >= n_row:
        return "columns", cols, {}, printed_cols
    if n_row >= 2:
        tb["delta_rows"] = sorted(deltas)
        return "rows", kept, {i: leftovers[i] for i in kept}, printed_rows
    if deltas and n_row:
        # A DIFFERENCE TABLE IS STILL A COMPARISON, and refusing it threw away
        # the only numbers TSARseqNovo's Table 1 prints: its own three rows,
        # correctly labelled, under three metrics. What its 'vs CasaNovo' and
        # 'vs pi-HelexiNovo' rows hold is an improvement in percentage points,
        # which is not a measurement and has no grain here, so those rows
        # record NO VALUE -- but they do name the comparators, which is the
        # edge the cross-paper graph is actually built from. So the table is
        # accepted with the subject's values and the comparators are carried
        # as named-but-unmeasured.
        tb["delta_rows"] = sorted(deltas)
        tb["delta_methods"] = [(printed_rows[i], rows.get(i)) for i in sorted(deltas)]
        return "rows", kept, {i: leftovers[i] for i in kept}, printed_rows
    if span_blocked:
        raise Reject(f"N1 the method names are in a spanner whose grouping no "
                     f"rule states, so columns {sorted(amb)} are undecidable; "
                     f"not guessed")
    # A TABLE OF THE PAPER'S OWN RESULTS has no method axis at all: InstaNovo's
    # Supplementary Table 2 is datasets down the side and metrics across, and
    # names its method only in the caption ("InstaNovo evaluation results on
    # all datasets"). It compares nothing, so it is accepted as a different
    # KIND -- own results -- and only when the caption names exactly ONE
    # catalog method (longest name first, so 'InstaNovo+' is not 'InstaNovo')
    # and the paper itself describes that method.
    if n_col == 0 and n_row == 0 and tb.get("own_methods"):
        cap = tb.get("caption") or ""
        found, taken = [], []
        for aid, name in sorted(tb.get("catalog_names") or [], key=lambda t: -len(t[1])):
            for m in re.finditer(r"(?<![\w+])" + re.escape(name) + r"(?![\w+])", cap):
                if not any(a <= m.start() < b for a, b in taken):
                    found.append((aid, name))
                    taken.append((m.start(), m.end()))
        ids = {aid for aid, _ in found}
        own = dict(tb["own_methods"])
        if len(ids) == 1 and next(iter(ids)) in own:
            aid = next(iter(ids))
            tb["kind"] = "own_results"
            printed = next(nm for a, nm in found if a == aid)
            return ("columns", {k: (aid, own[aid], None) for k in range(n)}, {},
                    {k: printed for k in range(n)})
    raise Reject(f"N1 neither axis carries >=2 methods "
                 f"(columns {n_col}, rows {n_row}); "
                 f"headers {[' '.join(tb['own'].get(k, [])) or '?' for k in range(n)]}")


def split_by_dataset(tb) -> dict[str, list[int]] | None:
    """Group a table's columns by the dataset each column's SPANNER names.

    One printed table often reports several datasets side by side: DiffuNovo's
    Table 2 is precision and AUC under each of 'Seven-species Dataset',
    'Nine-species Dataset' and 'HC-PT Dataset'. Held as one table it has one
    `dataset_id`, which would be a lie, and every method then claims the same
    measurement three times over -- which G6 caught, correctly but with a
    verdict that read like a parsing bug rather than the structure it is.

    Split, each part is an honest single-dataset comparison, so these tables
    yield three rows of evidence instead of a rejection. Every column must name
    a dataset for the split to be taken: one unattributed column would land in
    no part and be silently dropped.
    """
    groups: dict[str | None, list[int]] = {}
    for k in range(len(tb["edges"])):
        # The spanner AND the column's own header. A dataset is named in either
        # place: DiffuNovo's Table 2 puts the three benchmarks in the spanner
        # over metric columns, while its Table 3 makes them the column headers
        # themselves. Reading only the spanner accepted Table 3 whole and filed
        # its HC-PT and Seven-species numbers under the nine-species benchmark,
        # which is a wrong attribution rather than a missing one.
        sp = " ".join(tb["span"].get(k, [])) + " " + " ".join(tb["own"].get(k, []))
        hits = {nm for rx, nm in DATASET_CUES if rx.search(sp)}
        groups.setdefault(hits.pop() if len(hits) == 1 else None, []).append(k)
    # ONE DATASET, SEVERAL VERSIONS. Pairwise's Table 2 sets 'NINE-SPECIES V1'
    # and 'NINE-SPECIES V2' over the same three methods: one dataset to the
    # cue scan, so no split, and two measurements per method and species to G6.
    # When every column names the same dataset and the spanners name two or
    # more of its VERSIONS (the per-dataset lexicon decides), the split is on
    # the version, and each part is pinned to it through DATASET_TARGETS.
    if None not in groups and len(groups) == 1:
        (name,) = groups
        target = DATASET_TARGETS.get(name, (name, None))[0]
        lex = VERSION_LEXICON.get(target) or []
        by_ver: dict[str | None, list[int]] = {}
        for k in range(len(tb["edges"])):
            sp = " ".join(tb["span"].get(k, [])) + " " + " ".join(tb["own"].get(k, []))
            ver = next((v for rx, v in lex if rx.search(sp)), None)
            by_ver.setdefault(ver, []).append(k)
        if None not in by_ver and len(by_ver) >= 2:
            return {f"{name} \u00b7 {ver}": cols for ver, cols in by_ver.items()}
    if None in groups or len(groups) < 2:
        return None
    return {k: v for k, v in groups.items() if k}


def table_dataset_label(tb) -> str | None:
    """The one dataset the table's OWN header names, if it names exactly one.

    The spanner and the column headers are inside the table and so are more
    specific than the caption or the prose around it. Pairwise Attention's
    Table 3 spans 'NINE-SPECIES V2' over its three method columns while the
    paragraph above it mentions the MassIVE-KB set the models were TRAINED on,
    and reading both scopes together made that a multi-dataset table. The
    table says which data it scores on; the prose was talking about something
    else.
    """
    labels = set()
    for k in range(len(tb["edges"])):
        ctx = " ".join(tb["span"].get(k, [])) + " " + " ".join(tb["own"].get(k, []))
        labels |= {nm for rx, nm in DATASET_CUES if rx.search(ctx)}
    stub_labels = {nm for rx, nm in DATASET_CUES if rx.search(tb.get("stub") or "")}
    labels |= stub_labels
    return labels.pop() if len(labels) == 1 else None


def subtable(tb, cols: list[int], name: str) -> dict:
    """`tb` restricted to `cols`, renumbered, tagged with its dataset."""
    idx = {old: new for new, old in enumerate(cols)}
    lo_x, hi_x = tb["edges"][cols[0]][0], tb["edges"][cols[-1]][1]
    body = []
    for r in tb["body"]:
        cells = {idx[k]: c for k, c in r["cells"].items() if k in idx}
        gone = {idx[k]: t for k, t in (r.get("absent") or {}).items() if k in idx}
        if cells or gone:
            body.append({"label": r["label"], "cells": cells, "absent": gone,
                         "extra": list(r.get("extra") or []),
                         "top": r.get("top"), "extra_top": r.get("extra_top")})
    return {"edges": [tb["edges"][k] for k in cols],
            "own": {idx[k]: tb["own"][k] for k in cols if k in tb["own"]},
            "span": {idx[k]: tb["span"][k] for k in cols if k in tb["span"]},
            "stub": tb["stub"], "body": body,
            "dropped_rows": tb["dropped_rows"], "caption": tb["caption"],
            # Only what falls inside THIS part's columns. A column left of
            # every part (a year column before the first dataset) goes to the
            # leftmost part, so a rejoined table shows it once.
            "not_recorded_rows": [
                {**nr, "words": [(x, t) for x, t in nr["words"]
                                 if lo_x <= x <= hi_x]}
                for nr in tb.get("not_recorded_rows") or []],
            "not_recorded_cols": [
                nc for nc in tb.get("not_recorded_cols") or []
                if lo_x <= nc["x"] <= hi_x
                or (cols[0] == 0 and nc["x"] < lo_x)],
            # The note is printed under the whole table, so every part of a
            # split one carries it: the markers it defines appear in all of
            # them.
            "footnote": tb.get("footnote") or "",
            "header_raw": tb.get("header_raw") or "",
            "design_note": tb.get("design_note") or "",
            "table_label": f"{tb['table_label']} [{name}]",
            # THE REGISTRIES ARE KEYED ON THE PRINTED LABEL, and a split part
            # renames itself to say which dataset it holds. Without the parent
            # label every curated entry stopped applying the moment a table
            # was split, which is how RefineNovo's Table 6 lost the metric it
            # had been given.
            "registry_label": tb.get("registry_label") or tb["table_label"],
            "dataset_hint": name,
            "bbox": tb.get("bbox"), "page": tb.get("page"),
            "span_ambiguous": {idx[k] for k in cols
                               if k in (tb.get("span_ambiguous") or set())}}


def emit(con, base, tb, vocab, index, subject, near, whole, audit, tally, show,
         split=True, collect: list | None = None) -> int:
    """Resolve one parsed table, append its audit row, return parts accepted.

    Raises Reject. A split table returns the count of its parts that survived,
    so the summary counts TABLES RECORDED rather than blocks attempted.
    """
    apply_difference_table(tb, base.get("publication_id"))
    if split:
        parts = split_by_dataset(tb)
        if parts:
            ok = 0
            for name, cols in parts.items():
                part = subtable(tb, cols, name)
                row = {**base, "table_label": part["table_label"],
                       "caption": part["caption"][:260]}
                try:
                    ok += emit(con, row, part, vocab, index, subject, near,
                               whole, audit, tally, show, split=False,
                               collect=collect)
                except Reject as exc:
                    tally[f"rejected: {str(exc).split('(')[0].strip()}"] += 1
                    audit.append({**row, "verdict": "rejected", "reason": str(exc)})
                    if collect is not None:
                        collect.append({"verdict": "rejected", "reason": str(exc),
                                        "table_label": part["table_label"],
                                        "caption": part["caption"],
                                        "bbox": part.get("bbox"),
                                        "page": part.get("page")})
            return ok
    edges, own, span = tb["edges"], tb["own"], tb["span"]
    # For orientation()'s own-results case: the methods this paper describes,
    # and every catalog name (with aliases) a caption could name.
    tb["own_methods"] = [tuple(r) for r in con.execute(
        "SELECT a.id, a.name FROM publication_algorithm pa JOIN algorithm a "
        "ON a.id = pa.algorithm_id WHERE pa.publication_id = ? AND pa.role = 'describes'",
        (base.get("publication_id"),))]
    _key = (base.get("publication_id"),
            (tb.get("registry_label") or tb["table_label"] or "").strip())
    tb["own_methods"] += [tuple(r) for nm in TABLE_SELF.get(_key, [])
                          for r in con.execute("SELECT id, name FROM algorithm WHERE name = ?",
                                               (nm,))]
    tb["catalog_names"] = [(aid, nm.strip()) for aid, name, aliases in con.execute(
        "SELECT id, name, COALESCE(aliases, '') FROM algorithm")
        for nm in [name] + aliases.split(",") if len(nm.strip()) >= 4]
    axis, resolved_axis, leftovers, printed_of = orientation(
        tb, vocab, index, subject, base.get("publication_id"))
    col_head = [" ".join(own.get(k, [])).strip() or "?" for k in range(len(edges))]

    methods = {k: v for k, v in resolved_axis.items() if v}
    unresolved = [(col_head[k] if axis == "columns" else tb["body"][k]["label"]) or "?"
                  for k, v in resolved_axis.items() if not v]
    base.update({"n_columns": len(edges), "n_data_rows": len(tb["body"]),
                 "dropped_rows": tb["dropped_rows"],
                 "methods_printed": "|".join(
                     col_head if axis == "columns"
                     else [r["label"] or "?" for r in tb["body"]]),
                 "methods_resolved": "|".join(
                     f"{v[1]}@{v[2]}" if v[2] else v[1] for v in methods.values()),
                 "methods_unresolved": "|".join(unresolved),
                 "reason": f"axis={axis}"})

    # N1: on the METHOD axis every entry must resolve. An entry on the other
    # axis is a species or a metric and is not expected to be a method.
    #
    # AN ABSENT LABEL IS NOT AN UNRESOLVED NAME. A row with no label at all is
    # a different failure from a row naming a method we cannot identify: the
    # first loses one measurement, the second would change which baselines the
    # paper chose, which is what this guard protects. So a bare row is dropped
    # and COUNTED, and a named one still refuses the table. (Its first case,
    # LIPNovo's Table 3, turned out to have a label 3.2 pt above its values,
    # which the body builder now recovers; the rule remains for a label that
    # is genuinely absent.)
    if axis == "rows":
        bare = [i for i, v in resolved_axis.items()
                if not v and not (tb["body"][i]["label"] or "").strip()]
        if bare and len(bare) < len(resolved_axis) - 1:
            for i in bare:
                resolved_axis.pop(i, None)
            tb["dropped_rows"] = (tb.get("dropped_rows") or 0) + len(bare)
            unresolved = [(tb["body"][k]["label"]) or "?"
                          for k, v in resolved_axis.items() if not v]
            base["dropped_rows"] = tb["dropped_rows"]
    if unresolved:
        raise Reject(f"N1 unresolved on the method axis ({axis}): {unresolved}")
    # ANY method the paper describes counts as its own, not just the first:
    # InstaNovo's preprint describes InstaNovo and InstaNovo+, and its
    # InstaNovo+ results table was refused for lacking an InstaNovo column.
    own_ids = {aid for aid, _n in tb.get("own_methods") or []} or (
        {subject["id"]} if subject else set())
    if own_ids and not any(v[0] in own_ids for v in methods.values()):
        raise Reject("N2 no self column")
    # AN ABLATION BY STRUCTURE, whatever its caption says. C2 reads the
    # caption, and CausalNovo's Table 12 is captioned "Experiment results on
    # different peak distinction strategies", which names no ablation; its rows
    # are Baseline, CausalNovo and CausalNovo with 18 ion types -- the paper's
    # own design choices. It had been refused only by accident, as a
    # multi-dataset table, and recording an unstated dataset instead let it
    # through. So: the subject in two or more variants with no OTHER method
    # named in its own right is a study of the subject, not a comparison. A
    # comparator printed only as 'Baseline' does not count as named. Pairwise
    # Attention's BASE / PA / CASANOVO stays a comparison because Casanovo is
    # named; DiffuNovo's (Logits) / (MBR) stays one because five others are.
    # NOT a '+X' rule. A '+' row reads like an increment, and in CausalNovo's
    # Table 12 it is one, but CausalNovo is a PLUG-IN: its main results are
    # printed as 'CasaNovo' against '+CausalNovo' for half a dozen base
    # models, the same notation with the opposite meaning. A rule on the
    # notation called its main comparison tables ablations, which hid the
    # real reasons they fail. What separates the two is whether anything
    # besides the subject and the study's own baseline is in the table.
    if subject:
        own_variants = {v[2] or "" for v in methods.values() if v[0] == subject["id"]}
        # A comparator the paper itself labels as THE baseline of the study
        # ('CasaNovo (Baseline)') is the base being built on, not a method
        # named in its own right.
        named_others = [k for k, v in methods.items() if v[0] != subject["id"]
                        and "baseline" not in (printed_of.get(k) or "").lower()
                        and norm(re.sub(r"[*\u2020\u2021]+$", "",
                                        printed_of.get(k) or "")) not in
                        {"base", "vanilla", "ours"}]
        if len(own_variants) >= 2 and not named_others:
            raise Reject(f"C2 ablation by structure: every row is a variant of "
                         f"{subject['name']} or the study's baseline")

    # M1/M2. THE METRIC AXIS IS INDEPENDENT OF THE METHOD AXIS, and assuming
    # otherwise cost a whole class of tables. Three layouts are all in use:
    #
    #   ContraNovo Table 1   methods in columns, metric in the column spanner
    #   DiffuNovo  Table 2   methods in ROWS,   metric in the column headers
    #   CrossNovo  Table 1   methods in ROWS,   metric in a ROW-GROUP label
    #
    # So the metric is looked for along the columns first and along the rows
    # second, whichever axis carries the methods.
    #
    # The order matters and is not arbitrary. The column pass is STRICT: the
    # metric must come from the columns' own spanner or headers, never from the
    # caption. Allowing the caption in would be actively wrong on a stacked
    # table, where a caption mentioning precision would stamp 'precision' on
    # every column and silently relabel the recall half of the table. The level
    # may come from the caption, because it does not vary down a stacked
    # table's groups without the metric varying too.
    # THE TABLE'S OWN HEADER OUTRANKS ITS CAPTION as a statement of the metric,
    # and it has to be read raw, because the header model keeps only phrases it
    # can map onto columns. CrossNovo-era AdaNovo's Table 2 centres 'PTMs
    # precision' over its four method columns; the header model could not
    # place a phrase that covers no single column and dropped it, and the
    # table had been getting its metric only because the caption used to run
    # on into the header. Clean captions removed the accident.
    ctx_extra = " ".join([tb.get("header_raw") or "", tb["caption"], tb["stub"]])
    # ...AND EACH SCOPE IS ASKED ON ITS OWN, header first. Joining them made the
    # answer depend on the ORDER OF THE PATTERNS rather than on which text
    # said it: AdaNovo's Table 2 heads its columns 'PTMs precision' and
    # captions itself "...identifying amino acids with PTMs", and since the
    # amino-acid pattern is tried before the PTM one, the caption won and the
    # table was recorded as amino-acid precision.
    _scopes = [tb.get("header_raw") or "", tb["caption"], tb["stub"]]

    def scoped(fn):
        for sc in _scopes:
            v = fn(sc) if sc else None
            if v:
                return v
        return None
    subsets = {}

    def along_columns():
        out = {}
        for k in range(len(edges)):
            ctx = " ".join(span.get(k, [])) + " " + col_head[k]
            metric = metric_of(ctx)
            level = level_of(ctx) or scoped(level_of)
            if not metric or not level:
                return None
            out[k] = (metric, level)
        return out

    def row_ctx(i: int) -> str:
        """The text that might name row i's metric.

        When the methods are the ROWS this is what was left over after the
        method name was taken out of the label; when the methods are the
        COLUMNS the whole row label is a candidate, because a table can put
        methods across the top and metrics down the side. Publication 4 does
        exactly that: BASE | PA | CASANOVO across, one metric per row.
        """
        if axis == "rows":
            return " ".join(leftovers.get(i, []))
        return tb["body"][i]["label"] or ""

    def segments() -> list[list[int]]:
        """Split the rows where the method list restarts.

        A stacked table lists the same methods once per metric, so a repeat is
        an unambiguous boundary and needs no typography. Nearest-centre is
        wrong here: the group label is set near the TOP of its run rather than
        at its centre.
        """
        out: list[list[int]] = []
        current: set = set()
        for i in range(len(tb["body"])):
            key = (methods[i][0], methods[i][2] or "") if i in methods else None
            if key and key in current:
                out.append([])
                current = set()
            if not out:
                out.append([])
            out[-1].append(i)
            if key:
                current.add(key)
        return out

    def along_rows():
        """One metric per SEGMENT, from the label words its rows carry.

        The group label arrives scattered: 'Amino' / 'Acid' / 'Precision' down
        three row labels, or on interstitial lines of its own between data
        rows. Collecting it PER SEGMENT rather than by clustering adjacent
        marker rows is what makes it correct: CrossNovo's appendix puts
        'AminoAcid' and 'Precision' on rows 1 and 2 and 'Peptide' and 'Recall'
        on rows 4 and 5, and a cluster that tolerates a two-row gap bridges
        row 3 and swallows all four words into one group, which made every row
        of the table claim amino-acid precision and tripped G6.
        """
        # WHEN THE METHODS ARE THE COLUMNS, each row is its own group: the
        # row label names that row's metric or level and there is nothing to
        # segment. Publication 433 puts 'Peptide' on one row and 'Amino Acid'
        # on the next with the metric in the caption, and segmenting by a
        # method list that is not on this axis lumped both rows together.
        segs = ([[i] for i in range(len(tb["body"]))] if axis == "columns"
                else segments())
        out = {}
        texts = [" ".join(row_ctx(i) for i in seg).strip() for seg in segs]
        # THE ROW AXIS MUST ACTUALLY CARRY THE METRIC. Without this check it
        # "succeeded" on the caption fallback alone, which made the row axis the
        # metric axis for a table whose rows are datasets -- and the subset is
        # then dropped, so every method collapsed onto one measurement per
        # metric. DiffNovo's Table 2 has UTI / OC / Plasma down the side and one
        # precision for the whole table.
        if not any(metric_of(t) or level_of(t) for t in texts):
            return None
        for si, seg in enumerate(segs):
            ctx = texts[si]
            # A segment with no label of its own: the table states one metric
            # for all of them, which comes from the scopes in order.
            for i in seg:
                metric = metric_of(ctx) or scoped(metric_of)
                level = level_of(ctx) or scoped(level_of)
                if not metric or not level:
                    return None
                out[i] = (metric, level)
        return out

    # A CURATED ENTRY FIRST. It exists because the page cannot be read, so
    # letting along_columns try before it meant a mangled header still decided:
    # DiffNovo's Table 1 has 'Recall' and 'Precision' scattered through its
    # shredded header rows, which was enough for the column pass to succeed
    # with the groups in the wrong place.
    pinned = TABLE_METRIC.get((base.get("publication_id"),
                               (tb.get("registry_label") or tb["table_label"] or "").strip()))
    found, metric_axis = None, "columns"
    if pinned:
        if isinstance(pinned, dict):
            # PER ROW, in the order the rows carrying values appear. A stacked
            # table can state its groups' metrics only positionally, in prose,
            # with nothing in the grid to key on; see TSARseqNovo's Table 1.
            keys = sorted(methods)
            if len(pinned["rows"]) != len(keys):
                raise Reject(f"M1 TABLE_METRIC lists {len(pinned['rows'])} rows, "
                             f"the table has {len(keys)} carrying methods")
            found = dict(zip(keys, pinned["rows"]))
            metric_axis = "rows"
        elif isinstance(pinned, list):
            if len(pinned) != len(edges):
                raise Reject(f"M1 TABLE_METRIC lists {len(pinned)} columns, "
                             f"the table has {len(edges)}")
            found = dict(enumerate(pinned))
        else:
            found = {k: pinned for k in range(len(edges))}
    if found is None:
        found = along_columns()
    if found is None:
        # The metric may run down the rows whichever axis carries the methods.
        found = along_rows()
        metric_axis = "rows"
    if found is None:
        # Last resort: the caption names ONE METRIC for the whole table, while
        # the LEVEL may still vary by column. Publication 30 is the example:
        # its caption says 'Precision comparison of ...' and its spanner says
        # 'Peptide-level performance' over the first three columns and
        # 'Amino acid-level performance' over the last three. Taking both from
        # the caption gave every column the level that happened to match
        # first, which collapsed the two halves into one measurement and
        # tripped G6. The metric is global here; the level is not.
        metric = scoped(metric_of)
        if not metric:
            raise Reject(f"M1 no metric on either axis or in the caption; "
                         f"headers {col_head}")
        found = {}
        for k in range(len(edges)):
            ctx = " ".join(span.get(k, [])) + " " + col_head[k]
            level = level_of(ctx) or scoped(level_of)
            if not level:
                # Distinguished from M1 on purpose: the metric was found and
                # only the level is missing, a different thing to go and fix.
                raise Reject(f"M2 metric {metric!r} found but no level; "
                             f"headers {col_head}")
            found[k] = (metric, level)
        metric_axis = "columns"
    metrics = {j: v[0] for j, v in found.items()}
    levels = {j: v[1] for j, v in found.items()}

    # The SUBSET is whatever the non-method, non-metric axis names. When the
    # methods are rows it is the column, and it must carry the SPANNER as well
    # as the header: CrossNovo's antibody tables print 'AspN' twice, once per
    # chain, distinguished only by the spanner above it, and keying on the
    # header alone made the two columns one measurement.
    sub_over = COLUMN_SUBSET_OVERRIDE.get(
        (base.get("publication_id"), (tb.get("registry_label") or tb["table_label"] or "").strip()))
    if sub_over and len(sub_over) != len(edges):
        raise Reject(f"D3 COLUMN_SUBSET_OVERRIDE lists {len(sub_over)} columns, "
                     f"the table has {len(edges)}")
    for k in range(len(edges)):
        if sub_over:
            subsets[k] = sub_over[k]
        elif axis == "columns":
            subsets[k] = ""
        elif tb.get("dataset_hint"):
            # A SPLIT PART ALREADY KNOWS ITS DATASET, so the spanner that named
            # it has nothing left to contribute and must not be mined for a
            # subset: on LIPNovo's PTM table the spanner carries the
            # neighbouring column's prose, and 'andevaluation.' became the
            # subset of every cell.
            # The dataset's own name is already recorded, so strip it out of
            # the header and keep only what it adds: a column headed
            # '9Species (yeast)' contributes the test species, not the
            # benchmark's name a second time.
            head = col_head[k]
            # Strip the dataset's own name ONLY IF SOMETHING IS LEFT. For
            # '9Species (yeast)' that leaves the test species, which is the
            # useful part. But pNovo 3's columns ARE species names, and a
            # species name is itself a nine-species cue, so stripping emptied
            # every subset and its four methods collapsed onto one measurement.
            stripped = head
            for rx, _nm in DATASET_CUES:
                stripped = rx.sub(" ", stripped)
            stripped = re.sub(r"\s+", " ", re.sub(r"[()\[\]]", " ", stripped)).strip()
            if stripped:
                head = stripped
            subsets[k] = ("" if not head or head == "?" or metric_of(head)
                          or level_of(head) else unsquash_label(head))
        else:
            head = col_head[k]
            # A SPAN SHARED BY EVERY COLUMN DISTINGUISHES NOTHING, so it is no
            # part of a subset: 'Species' sits over all nine species columns
            # and says only what the row of headers beneath it already says.
            shared = {t for t in span.get(0, [])
                      if all(t in (span.get(j) or []) for j in range(len(edges)))}
            sp = " ".join(t for t in span.get(k, []) if t not in shared).strip()
            keep = [x for x in (sp, head)
                    if x and x != "?" and not metric_of(x) and not level_of(x)]
            # DROPPING A DATASET CUE MUST NOT EMPTY THE SUBSET. A species name
            # IS a nine-species cue, so 'M.mazei' was removed as if it were the
            # dataset's name and that column fell back to its raw header while
            # its neighbours were cleaned up. Excluded only when something else
            # remains to name the column.
            narrowed = [x for x in keep
                        if not any(rx.search(x) for rx, _ in DATASET_CUES)]
            subsets[k] = unsquash_label(" ".join(narrowed or keep))
    base.update({"metrics_resolved": "|".join(metrics[j] for j in sorted(metrics)),
                 "levels": "|".join(levels[j] for j in sorted(levels)),
                 "reason": f"axis={axis} metric_axis={metric_axis}"})

    # G6: no two cells may claim the same measurement. The key carries the
    # metric, the level and the subset, matching paper_comparison_result's own
    # UNIQUE constraint, because one method legitimately appears many times in
    # one table: ContraNovo's Table 1 is six methods crossed with amino-acid
    # and peptide precision, so 'Peaks.' is columns 1 and 7.
    legend = CELL_MARKERS.get((base.get("publication_id"),
                               (tb.get("registry_label") or tb["table_label"] or "").strip()), {})
    compound_cells = [c for r in tb["body"] for c in r["cells"].values()
                      if len(c.get("parts") or []) > 1]
    if compound_cells and not legend:
        raise Reject(f"G4 multi-valued cell {compound_cells[0]['printed']!r} "
                     f"and no CELL_MARKERS legend for this table")
    unknown = {m for c in compound_cells for _v, m in c["parts"]
               if m and m not in legend}
    if unknown:
        raise Reject(f"G4 cell marker(s) {sorted(unknown)} are not in this "
                     f"table's CELL_MARKERS legend")

    def parts_of(cell, metric, level):
        """Every measurement in one cell, as (value, printed, metric, level, basis).

        A plain cell yields one. A footnoted cell yields one per marker, each
        taking whatever the table's legend says the marker means and otherwise
        inheriting the column.
        """
        out = []
        for value, marker in (cell.get("parts") or [(cell["value"], "")]):
            over = legend.get(marker, {}) if marker else {}
            out.append((value, cell["printed"],
                        over.get("metric", metric), over.get("level", level),
                        over.get("basis")))
        return out

    # THE BASIS IS DECIDED BEFORE G6, because it is part of what makes two
    # numbers different measurements. One table legitimately reports the same
    # method twice under two bases: LIPNovo's Table 1 lists AdaNovo with
    # NovoBench's numbers and AdaNovo-dagger with its own retraining, which is
    # the retrained-versus-released distinction BENCHMARKS.md is about. Keyed
    # without it they were one measurement and G6 refused the table.
    bases = {k: find_basis(whole, v[1]) for k, v in methods.items()}
    mk_legend = METHOD_MARKERS.get(
        (base.get("publication_id"),
         (tb.get("registry_label") or tb["table_label"] or "").strip()), {})
    if mk_legend:
        for k in list(bases):
            printed = (printed_of.get(k) or "").strip()
            marker = label_marker(printed)
            # A LEGEND'S UNMARKED CASE DOES NOT APPLY TO THE PAPER'S OWN
            # METHOD. LIPNovo's legend reads "† denotes our retrained results,
            # and other results are provided by NovoBench", and LIPNovo's own
            # row carries no dagger -- so read literally the legend says this
            # paper's numbers for its own method came from NovoBench, which is
            # absurd and was recorded as basis 'quoted' on all six of its
            # tables. The authors describe their own run: "We compare LIPNovo
            # with several established de novo sequencing competitors ...
            # Notably, we retrain a CasaNovo as the direct baseline of our
            # LIPNovo with the same configurations." So the subject keeps
            # whatever its own prose says, and an EXPLICIT marker still
            # applies: a dagger on the subject's row would mean what it says.
            if not marker and methods[k][0] == (subject["id"] if subject else None):
                continue
            over = mk_legend.get(marker)
            if over and over.get("basis"):
                bases[k] = (over["basis"],
                            f"the table's legend marks {printed!r}", False)

    # A CURATED PER-METHOD BASIS WINS over both the legend and the prose scan,
    # because it exists where neither can see the answer.
    for k, v in methods.items():
        over = TABLE_BASIS.get(
            (base.get("publication_id"),
             (tb.get("registry_label") or tb["table_label"] or "").strip()), {})
        # 'Name@variant' wins over the bare name, for a table that prints one
        # method twice under two bases: GyroNovo quotes LIPNovo from NovoBench
        # AND reports its own reproduction, as 'Baseline'.
        key = f"{v[1]}@{v[2]}" if f"{v[1]}@{v[2]}" in over else v[1]
        # '*' covers every OTHER method in the table, never the paper's own:
        # "Unless otherwise noted, the reported results are taken from
        # NovoBench" is a statement about the baselines.
        if key not in over and "*" in over and not (subject and v[0] == subject["id"]):
            key = "*"
        if key in over:
            bases[k] = (over[key][0], over[key][1], False)

    # A ROW-GROUP LABEL THAT NAMES NO METRIC IS THE SUBSET, and it carries
    # DOWN the group. When the methods are the rows and the metrics are the
    # columns, the only thing left for a row-group label to be is the subset --
    # a species, an enzyme. Without this LIPNovo's Table 3, which sets one
    # species over each Baseline-dagger/LIPNovo pair, had all nine LIPNovo rows
    # claim one measurement and G6 refused it. A label CENTRED between two rows
    # belongs to both (see below); otherwise a label applies to the rows under
    # it until the next one. A group label that DOES name a metric is the
    # metric axis's
    # business and is left to it.
    row_subset: dict[int, str] = {}
    if axis == "rows" and metric_axis == "columns":
        # A LABEL CENTRED BETWEEN TWO ROWS BELONGS TO BOTH. A multirow label
        # is set vertically in the middle of its group, so in the text layer it
        # lands BETWEEN the group's rows, and attaching it to the row below
        # paired every species with the wrong rows: LIPNovo's Table 3 prints
        # each species over a Baseline-dagger row and a LIPNovo row, and the
        # label-above reading gave each species the previous species'
        # baseline. Measured, a centred label sits within a quarter of the gap
        # of the midpoint of the rows either side. The prose confirms the
        # pairing exactly: the Mean group then reads 0.751 against 0.804, the
        # +5.3% the paper states, and +4.5% and +2.3% on the other two.
        centred: dict[int, str] = {}
        body_ = tb["body"]
        for ri in range(1, len(body_)):
            lt, tn, tp = (body_[ri].get("extra_top"), body_[ri].get("top"),
                          body_[ri - 1].get("top"))
            if lt is None or tn is None or tp is None or tn <= tp:
                continue
            if abs(lt - (tp + tn) / 2.0) <= 0.25 * (tn - tp):
                words = [w for w in (body_[ri].get("extra") or [])
                         if isinstance(w, str) and sum(c.isalpha() for c in w) >= 2
                         and not prosey(w)]
                txt = unsquash_label(" ".join(words)).strip()
                if txt and not metric_of(txt) and not level_of(txt):
                    centred[ri - 1] = centred[ri] = txt
        cur = ""
        for ri in range(len(tb["body"])):
            if centred:
                row_subset[ri] = centred.get(ri, "")
                continue
            # ONLY THE INTERSTITIAL LABEL, and only its words. Taking every
            # leftover set the subset from two things that are not group
            # labels at all: the residue of resolving 'Baseline†' (the word
            # 'Baseline'), and a row of ablation ticks from the neighbouring
            # table ('✓ ✗'). Either one then carried down onto the next
            # species' rows and merged them.
            words = [w for w in (tb["body"][ri].get("extra") or [])
                     if isinstance(w, str) and sum(c.isalpha() for c in w) >= 2
                     and not prosey(w)]
            txt = unsquash_label(" ".join(words)).strip()
            if txt and not metric_of(txt) and not level_of(txt):
                cur = txt
            row_subset[ri] = cur
        # A GROUP LABEL ON A ROW OF ITS GROUP, typically the middle one.
        # LIPNovo+'s leave-one-out Table 4 prints each species on the
        # LIPNovo row of its Baseline / LIPNovo / LIPNovo+ triple, so the
        # label is neither on a line of its own nor between two rows; it
        # arrives as the leftover of resolving 'Bacillus LIPNovo', and all
        # nine Baseline rows fell into one measurement. Groups are read from
        # the spacing, which is wider between groups (16.4 pt) than within
        # them (9-11 pt), and the reading is used only if EVERY group holds
        # exactly one such label.
        body_ = tb["body"]
        if not any(row_subset.values()) and len(body_) >= 4:
            own_lab = {}
            for ri, r in enumerate(body_):
                extra = set(r.get("extra") or [])
                words = [w for w in leftovers.get(ri, [])
                         if isinstance(w, str) and w not in extra
                         and sum(c.isalpha() for c in w) >= 2 and not prosey(w)]
                txt = unsquash_label(" ".join(words)).strip()
                if txt and not metric_of(txt) and not level_of(txt):
                    own_lab[ri] = txt
            tops = [r.get("top") for r in body_]
            # A SUBSET COLUMN: every method row carries its own leftover label,
            # because the table prints the test set in a column of its own.
            # ReNovo's Table 8 lists ReNovo and AdaNovo on 'Nine-species Dataset
            # (Original)', '(>3)' and '(>5)', and without this all three of a
            # method's rows were one measurement (G6).
            # Only where the COLUMNS carry no subset, and every leftover reads
            # as a test set: MemNovo's Table 2 prints a 'Backbone' column
            # (CNN-LSTM, Transformer) beside species columns, and taking that
            # for a subset collapsed every species into one measurement.
            method_rows = [ri for ri in range(len(body_)) if methods.get(ri)]
            if len(method_rows) >= 2 and all(ri in own_lab for ri in method_rows) and \
                    len({own_lab[ri] for ri in method_rows}) >= 2 and \
                    not any(subsets.values()) and all(
                        re.search(r"(?i)dataset|test|species|\bset\b", own_lab[ri])
                        for ri in method_rows):
                for ri in method_rows:
                    row_subset[ri] = own_lab[ri]
            elif len(own_lab) >= 2 and all(t is not None for t in tops):
                gaps = [b - a for a, b in zip(tops, tops[1:])]
                cut = 1.3 * statistics.median(gaps)
                groups, curg = [], [0]
                for ri in range(1, len(body_)):
                    if gaps[ri - 1] > cut:
                        groups.append(curg)
                        curg = []
                    curg.append(ri)
                groups.append(curg)
                if len(groups) >= 2 and all(
                        sum(1 for ri in g if ri in own_lab) == 1 for g in groups):
                    for g in groups:
                        lab = next(own_lab[ri] for ri in g if ri in own_lab)
                        for ri in g:
                            row_subset[ri] = lab
                else:
                    # EVENLY SPACED GROUPS, separated by a RULE rather than a
                    # wider gap: PLMNovo's Table 1 prints 'MSKB (Tryptic)' and
                    # 'Multi-Enzyme (Non-Tryptic)' on the middle row of three
                    # rows each, every row 10-12 pt apart. Each row joins the
                    # nearest label, and the reading is kept only if no row is
                    # equidistant from two labels and every label sits at the
                    # middle of the rows it gathered.
                    labs = sorted(own_lab)
                    nearest: dict[int, int] = {}
                    for ri in range(len(body_)):
                        d = sorted((abs(ri - li), li) for li in labs)
                        if len(d) > 1 and d[0][0] == d[1][0]:
                            nearest = {}
                            break
                        nearest[ri] = d[0][1]
                    gathered: dict[int, list[int]] = {}
                    for ri, li in nearest.items():
                        gathered.setdefault(li, []).append(ri)
                    if nearest and all(
                            len(g) >= 2 and abs(li - (g[0] + g[-1]) / 2) <= 0.5
                            for li, g in gathered.items()):
                        for ri, li in nearest.items():
                            row_subset[ri] = own_lab[li]

    def cell_meta(k, ri):
        """(method, metric, level, subset) for one cell, whichever the layout."""
        m = methods.get(k if axis == "columns" else ri)
        if not m:
            # A row that carries no method carries no measurement either, and
            # asking for its metric raises: a difference table's 'vs X' rows
            # are excluded from the method axis, so they have no entry on the
            # metric axis to look up.
            return None, None, None, None
        j = k if metric_axis == "columns" else ri
        # When the methods are columns the row label is normally the subset --
        # a species, an enzyme. But if the row label is what NAMES THE METRIC,
        # it is not also a subset, or every cell would claim a subset of
        # 'Peptide precision'.
        sub = (subsets[k] if sub_over
               else "" if (axis == "columns" and metric_axis == "rows")
               else unsquash_label(tb["body"][ri]["label"]) if axis == "columns"
               # WITH METHODS DOWN THE SIDE AND METRICS ACROSS THE TOP, THE ROW
               # GROUP IS THE SUBSET and outranks anything a column header
               # offers, because in this layout a column header is a metric and
               # a column "subset" can only be a stray word: LIPNovo's Table 3
               # sits beside Table 5, whose header 'Baseline Impu.' lands over
               # Table 3's last column, and every row's cell in that column
               # claimed the subset 'Baseline'.
               else (row_subset.get(ri) or subsets[k]))
        return m, metrics[j], levels[j], sub

    seen = set()
    for ri, r in enumerate(tb["body"]):
        for k, cell in r["cells"].items():
            m, metric, level, sub = cell_meta(k, ri)
            if not m:
                continue
            j = k if axis == "columns" else ri
            for _v, _p, mt, lv, pb in parts_of(cell, metric, level):
                key = (m[0], m[2] or "", mt, lv, sub,
                       pb or (bases.get(j) or ("unclear",))[0])
                if key in seen:
                    raise Reject(f"G6 duplicate measurement {m[1]} "
                                 f"{mt}/{lv}/{sub or '-'}/{key[5]}")
                seen.add(key)

    vals = [v for ri, r in enumerate(tb["body"])
            if axis == "columns" or methods.get(ri)
            for k, c in r["cells"].items()
            if axis == "rows" or k in methods      # a dropped column is not measured
            for v, _m in (c.get("parts") or [(c["value"], "")])]
    if not vals:
        raise Reject("G3 no cells")
    lo, hi = min(vals), max(vals)
    if hi <= 1.0:
        unit, scale = "0-1", 1.0
    elif lo >= 1.0 and hi <= 100.0:
        unit, scale = "0-100", 100.0
    else:                                                           # G5
        raise Reject(f"G5 mixed units [{lo}, {hi}]")
    base.update({"value_min": f"{lo:g}", "value_max": f"{hi:g}", "unit": unit,
                 "n_cells": len(vals)})

    header_text = " ".join(
        [tb.get("stub") or ""]
        + [t for k in range(len(edges)) for t in (tb["span"].get(k) or [])]
        + [t for k in range(len(edges)) for t in (tb["own"].get(k) or [])])
    did, dname, vid, dprinted = resolve_dataset(
        con, tb["caption"], near, base["publication_id"],
        tb.get("dataset_hint") or table_dataset_label(tb), header_text,
        (tb.get("registry_label") or tb["table_label"] or "").strip())
    # EVERY PRINTED SUBSET WITH ITS CANONICAL NAME, so the record a schema
    # will write holds both: the species as the paper printed it, for
    # checking against the page, and as the catalog names it, with the
    # provenance accession, for lining one paper up against another.
    seen_sub: dict[str, str] = {}
    for ri, r in enumerate(tb["body"]):
        for k in r["cells"]:
            _m, _mt, _lv, sub = cell_meta(k, ri)
            if _m and sub and sub not in seen_sub:
                nm, acc = canonical_subset(sub, con)
                seen_sub[sub] = (f"{nm}" + (f" [{acc}]" if acc else "")) if nm else ""
    base["subsets_canonical"] = "|".join(f"{k}={v}" for k, v in seen_sub.items() if v)
    # PER-RESIDUE PRECISION IS A DIFFERENT GRAIN. A table whose subsets are
    # amino-acid residues ('M(O)', 'Q', 'F', 'K': publication 16's Table 3,
    # "precision for amino acids with similar masses") reports precision on
    # one residue at a time. Stored, each residue would become a subset beside
    # the species of every other table, and 'amino-acid precision on Q' would
    # read as a measurement on a dataset called Q. Refused whole (C4).
    residue = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY](\s*\(.{1,12}\)|\[.{1,12}\]"
                         r"|\+\d+(\.\d+)?)?\*?$")
    subs = [x.strip() for x in seen_sub if x and x.strip()]
    if len(subs) >= 2 and 2 * sum(bool(residue.match(x)) for x in subs) >= len(subs):
        raise Reject(f"C4 per-residue precision, a different grain from a "
                     f"table's amino-acid precision: {subs[:6]}")
    base.update({"dataset_resolved": dname or "", "dataset_printed": dprinted or "",
                 "dataset_version_resolved": vid or ""})

    modal = collections.Counter(b for b, _, _ in bases.values()).most_common(1)[0][0]
    base.update({"basis_assigned": "|".join(
                     f"{methods[k][1]}={bases[k][0]}" for k in sorted(bases)),
                 "basis_cue": (next((c for _, c, _ in bases.values() if c), "") or "")[:200],
                 "basis_conflict": "yes" if any(c for _, _, c in bases.values()) else ""})
    tally[f"accepted ({axis}), modal basis {modal}"] += 1

    results = []
    for ri, r in enumerate(tb["body"]):
        for k, cell in r["cells"].items():
            m, metric, level, sub = cell_meta(k, ri)
            if not m:
                continue
            j = k if axis == "columns" else ri
            for v, _p, mt, lv, bas in parts_of(cell, metric, level):
                bb = bas or (bases.get(j) or ("unclear",))[0]
                results.append(f"{m[1]}{'@' + m[2] if m[2] else ''}:{mt}/"
                               f"{lv}/{sub or '-'}={v / scale:.4f}[{bb}]")
    base.update({"verdict": "accepted", "reason": f"axis={axis}",
                 "subject_resolved": subject["name"] if subject else "",
                 "proposed_results": " ".join(results[:80])})
    audit.append(dict(base))
    def build_grid() -> dict:
        """The table as PRINTED, plus every measurement read from it.

        Two layers, so one record can reproduce the paper and standardise it.
        `columns`, `header`, `rows` and `cells` are the printed grid, stub and
        all, including what the miner does not record (difference rows, count
        rows, year and speed columns), each with the reason. `results` are the
        standardised measurements, each keyed to the printed cell it came from.
        """
        lay = LAYOUT_OVERRIDE.get((base.get("publication_id"),
                                   (tb.get("registry_label") or tb["table_label"] or "").strip()))
        has_group = any(row_subset.values()) or bool(lay and lay.get("row_groups"))
        stub_roles = (["group"] if has_group else []) + ["label"]
        data = [{"role": "data" if (axis == "rows" or k in methods)
                 else "not_recorded",
                 "why": "" if (axis == "rows" or k in methods) else "difference",
                 "x": (edges[k][0] + edges[k][1]) / 2, "k": k} for k in range(len(edges))]
        extra = [{"role": "not_recorded", "why": nc["why"], "x": nc["x"], "nc": nc}
                 for nc in tb.get("not_recorded_cols") or []]
        ordered = sorted(data + extra, key=lambda c: c["x"])
        columns = [{"role": r, "why": ""} for r in stub_roles] + [
            {"role": c["role"], "why": c["why"]} for c in ordered]
        off = len(stub_roles)
        col_of_k = {c["k"]: off + i for i, c in enumerate(ordered) if "k" in c}
        # HEADER: each data column's spanner phrases, top to bottom, then its
        # own header; columns with shorter stacks are aligned at the bottom,
        # next to the body, which is where a header row sits. Equal phrases in
        # adjacent columns of one header row are one spanning cell.
        stacks = {}
        for i, c in enumerate(ordered):
            if "k" in c:
                k = c["k"]
                stacks[off + i] = list(tb["span"].get(k) or []) + [
                    " ".join(tb["own"].get(k) or []).strip()]
            else:
                stacks[off + i] = [c["nc"]["header"]]
        depth = max([len(v) for v in stacks.values()] or [1])
        rowsh: dict[int, dict[int, str]] = collections.defaultdict(dict)
        for gc, st in stacks.items():
            for i, t in enumerate(st):
                if t:
                    rowsh[depth - len(st) + i][gc] = t
        # Not a body row's own label either: LIPNovo's 'Baseline†' sits on a
        # line of its own above the block and was read into the stub header.
        body_labels = {(r["label"] or "").strip() for r in tb["body"]}
        stub_words = [w for w in (tb.get("stub") or "").split()
                      if w.lower().strip(".,;:") not in FUNCTION_WORDS and not prosey(w)
                      and w not in body_labels]
        if stub_words:
            rowsh[depth - 1][0] = " ".join(stub_words)
        header = []
        for hr in sorted(rowsh):
            cells_h = rowsh[hr]
            gcs = sorted(cells_h)
            i = 0
            while i < len(gcs):
                j = i
                while (j + 1 < len(gcs) and gcs[j + 1] == gcs[j] + 1
                       and cells_h[gcs[j + 1]] == cells_h[gcs[i]] and gcs[i] >= off):
                    j += 1
                header.append({"row": hr, "row_end": hr, "c0": gcs[i],
                               "c1": gcs[j] if gcs[i] >= off else off - 1,
                               "text": cells_h[gcs[i]]})
                i = j + 1
        if lay and lay.get("header"):
            def gcol(c):
                if c >= 0:
                    return off + c
                return (off - 1) if c == -1 else 0
            header = [{"row": r0, "row_end": r1, "c0": gcol(c0),
                       "c1": gcol(c1), "text": t}
                      for r0, r1, c0, c1, t in lay["header"]]
        # ROWS, body and not-recorded, in the order they are printed.
        items = [("body", ri, r.get("top") or 0.0) for ri, r in enumerate(tb["body"])] + [
            ("nr", ni, nr["top"]) for ni, nr in enumerate(tb.get("not_recorded_rows") or [])]
        items.sort(key=lambda t: t[2])
        rows_out, cells_out, row_of = [], [], {}
        col_x = [(gc, c["x"]) for gc, c in ((off + i, c) for i, c in enumerate(ordered))]
        for gi, (kind, idx, _t) in enumerate(items):
            if kind == "body":
                r = tb["body"][idx]
                row_of[idx] = gi
                measured = any(cell_meta(k, idx)[0] for k in r["cells"])
                role = ("data" if measured else
                        "difference" if (r["label"] or "").strip() and r["cells"]
                        else "not_recorded")
                lab_i, grp_i = r["label"] or "", row_subset.get(idx, "")
                if lay:
                    if lay.get("row_labels") and idx < len(lay["row_labels"]):
                        lab_i = lay["row_labels"][idx]
                    for g0, g1, gt in lay.get("row_groups") or []:
                        if g0 <= idx <= g1:
                            grp_i = gt
                rows_out.append({"role": role, "label": lab_i,
                                 "group": grp_i,
                                 "why": "" if role == "data" else
                                        ("difference" if role == "difference"
                                         else "no label on the page")})
                for k, c in r["cells"].items():
                    cells_out.append({"r": gi, "c": col_of_k[k], "text": c["printed"],
                                      "bold": bool(c.get("bold")),
                                      "underlined": bool(c.get("underlined")),
                                      "not_run": False})
                for k, t in (r.get("absent") or {}).items():
                    if k in col_of_k:
                        cells_out.append({"r": gi, "c": col_of_k[k], "text": t,
                                          "bold": False, "underlined": False,
                                          "not_run": True})
                for c in ordered:
                    if "nc" in c and r.get("top") in c["nc"]["cells"]:
                        cells_out.append({"r": gi, "c": col_x[ordered.index(c)][0],
                                          "text": c["nc"]["cells"][r["top"]],
                                          "bold": False, "underlined": False,
                                          "not_run": False})
            else:
                nr = tb["not_recorded_rows"][idx]
                rows_out.append({"role": "not_recorded", "label": nr["label"],
                                 "group": "", "why": nr["why"]})
                for x, t in nr["words"]:
                    if col_x:
                        gc = min(col_x, key=lambda q: abs(q[1] - x))[0]
                        cells_out.append({"r": gi, "c": gc, "text": t, "bold": False,
                                          "underlined": False, "not_run": False})
        # RESULTS: the standardised layer, one per measurement.
        results = []
        # THE PAPER'S OWN METHODS, all of them: LIPNovo+'s paper describes both
        # LIPNovo and LIPNovo+, and marking only the first left LIPNovo+'s
        # tables on LIPNovo's page alone.
        own_ids = {r[0] for r in con.execute(
            "SELECT algorithm_id FROM publication_algorithm "
            "WHERE publication_id = ? AND role = 'describes'",
            (base.get("publication_id"),))} | {aid for aid, _n in tb.get("own_methods") or []}
        for ri, r in enumerate(tb["body"]):
            for k, cell in r["cells"].items():
                m, metric, level, sub = cell_meta(k, ri)
                if not m:
                    continue
                j = k if axis == "columns" else ri
                rd = row_dataset(con, base.get("publication_id"), sub) if sub else None
                for pi, (v, _p, mt, lv, bas) in enumerate(parts_of(cell, metric, level)):
                    _pre = SUBSET_PREFIX.get((base.get("publication_id"),
                                              (tb.get("registry_label") or tb["table_label"] or "").strip()))
                    canon, acc = (canonical_subset(f"{_pre} {sub}" if _pre else sub, con)
                                  if sub else (None, None))
                    if rd and rd[2]:
                        canon, acc = rd[2], species_index(con).get(rd[2])
                    sd = cell.get("stddev")
                    results.append({
                        "r": row_of[ri], "c": col_of_k[k], "part": pi,
                        "algorithm_id": m[0], "algorithm": m[1],
                        "printed": printed_of.get(j) or "",
                        "variant": m[2] or "", "is_self": int(m[0] in own_ids),
                        "metric": mt, "level": lv,
                        # A ROW_DATASET row carries its own dataset.
                        "dataset_id": rd[0] if rd else did,
                        "dataset_version_id": rd[1] if rd else vid,
                        # A subset that is not a species (OC, UTI, a pNovo
                        # run) KEEPS ITS PRINTED FORM as its canonical name,
                        # the rule canonical_subset() states; left empty, a
                        # standardised table lost its dataset column.
                        "subset": sub or "", "subset_canonical": canon or sub or "",
                        "subset_accession": acc or "",
                        "is_aggregate": int(bool(AGGREGATE.match(sub or ""))),
                        "value": v / scale,
                        "stddev": (sd / scale) if sd is not None else None,
                        "basis": bas or (bases.get(j) or ("unclear",))[0],
                        # A basis set by a CELL's marker cites that marker; a
                        # method-level basis cites its own sentence.
                        "basis_cue": (
                            f"the table's legend marks this cell "
                            f"'{(cell.get('parts') or [(0, '')])[pi][1]}'"
                            if bas else (bases.get(j) or ("", ""))[1] or ""),
                        "derived": cell.get("derived") or ""})
        return {"columns": columns, "header": header, "rows": rows_out,
                "cells": cells_out, "results": results}

    if collect is not None:
        # The resolved structure, so a review page can show the parse beside a
        # picture of the printed table without reimplementing any of this.
        if tb.get("kind") == "own_results" and not tb.get("design_note"):
            tb["design_note"] = ("A table of the paper's OWN results: it names one "
                                 "method and compares nothing, so it is recorded as own "
                                 "results rather than as a comparison.")
        extra = TABLE_NOTE.get((base.get("publication_id"),
                                (tb.get("registry_label") or tb["table_label"] or "").strip()))
        if extra and extra not in (tb.get("design_note") or ""):
            tb["design_note"] = " ".join(x for x in (tb.get("design_note"), extra) if x)
        collect.append({
            "kind": tb.get("kind") or "comparison",
            "grid": build_grid(),
            "verdict": "accepted", "reason": "",
            "table_label": tb["table_label"], "caption": tb["caption"],
            "footnote": tb.get("footnote") or "",
            "design_note": tb.get("design_note") or "",
            "bbox": tb.get("bbox"), "page": tb.get("page"),
            "axis": axis, "metric_axis": metric_axis,
            "dataset": dname, "dataset_version_id": vid,
            "dataset_printed": dprinted, "unit": unit,
            "col_head": list(col_head),
            # The PRINTED label as well as the resolved name, so the review
            # page can show 'DiffuNovo (Logits)' as the paper wrote it rather
            # than a reconstructed 'DiffuNovo vLogits'.
            "methods": {k: (v[1], v[2], printed_of.get(k) or "")
                        for k, v in methods.items()},
            "metrics": dict(metrics), "levels": dict(levels),
            "subsets": dict(subsets),
            # The subset carried by a ROW GROUP, where the table sets one
            # species above each group of method rows; see row_subset.
            "row_subsets": {i: v for i, v in row_subset.items() if v},
            # The stub's own header ('Species Method'), so the grid can name
            # the row-group column the way the paper does.
            "stub": tb.get("stub") or "",
            "header_raw": tb.get("header_raw") or "",
            "body": [{"label": r["label"],
                      "cells": {k: c["printed"] for k, c in r["cells"].items()},
                      # A cell the paper marks as not run, so the page can show
                      # the row the paper prints rather than silently dropping
                      # it. It carries no value, which is the honest record.
                      "absent": dict(r.get("absent") or {}),
                      # The paper's OWN emphasis, so the review page can show
                      # what the page shows instead of a ranking of its own.
                      "bold": sorted(k for k, c in r["cells"].items() if c.get("bold")),
                      "underlined": sorted(k for k, c in r["cells"].items()
                                           if c.get("underlined"))}
                     for r in tb["body"]],
            "basis": {methods[k][1]: bases[k][0] for k in sorted(bases)},
            # PER ROW, because one method can appear twice under two bases --
            # CausalNovo's 'CasaNovo' (quoted from NovoBench) and '†CasaNovo'
            # (retrained) -- and keyed by name the second overwrote the first,
            # so the page said 'Casanovo: retrained' for both.
            "row_basis": {k: bases[k][0] for k in sorted(bases)},
        })

    if show:
        print(f"\n  p{base['publication_id']} {base['table_label']} "
              f"page {base['pdf_page']}  methods in {axis.upper()}  "
              f"[{dname or 'dataset?'}{' v' + str(vid) if vid else ''}]  unit {unit}")
        print(f"    {tb['caption'][:140]}")
        print(f"    {'':22}" + "".join(f"{col_head[k][:10]:>11}" for k in range(len(edges))))
        if metric_axis == "columns":
            print(f"    {'':22}" + "".join(
                f"{metrics[k][:4] + '/' + levels[k][:3]:>11}"
                for k in range(len(edges))))
        for ri, r in enumerate(tb["body"][:16]):
            tag = (f"{metrics[ri][:4]}/{levels[ri][:3]} {methods[ri][1]}"
                   if metric_axis == "rows" and ri in methods
                   else (methods[ri][1] if axis == "rows" and ri in methods
                         else unsquash_label(r["label"])))[:21]
            print(f"    {tag:22}" + "".join(
                f"{r['cells'][k]['printed'][:10]:>11}" if k in r["cells"] else f"{'':>11}"
                for k in range(len(edges))))
    return 1


# A VERSION OF RECORD INHERITS ITS PREPRINT'S REGISTRY ENTRIES. The ICML and
# NeurIPS proceedings rows (publications 438-440) print the same tables as the
# arXiv preprints that every entry above was written against, so each entry
# keyed on the preprint is copied to the proceedings row unless that row has
# one of its own. The label map is needed where the proceedings renumber:
# AdaNovo's NeurIPS paper drops the preprint's PTM Table 2, so the preprint's
# Tables 3-5 are its Tables 2-4. With a map, a label outside it is NOT copied,
# because the same number then names a different table. None means identical
# numbering.
PROCEEDINGS_OF: dict[int, tuple[int, dict[str, str] | None]] = {
    438: (16, None),   # RefineNovo, ICML 2025
    439: (17, None),   # LIPNovo, ICML 2025
    440: (32, {"Table1": "Table1", "Table3": "Table2",
               "Table4": "Table3", "Table5": "Table4"}),   # AdaNovo, NeurIPS 2024
}


def _inherit_preprint_entries() -> None:
    for reg in [v for v in list(globals().values()) if isinstance(v, dict)]:
        keys = [k for k in reg if isinstance(k, tuple) and len(k) == 2
                and isinstance(k[0], int) and isinstance(k[1], str)]
        for tgt, (src, labels) in PROCEEDINGS_OF.items():
            for k in [k for k in keys if k[0] == src]:
                key = k[1]
                if re.match(r"(?i)table\s*\d", key):
                    if labels is not None:
                        new = labels.get(key.replace(" ", ""))
                        if new is None:
                            continue
                        key = new if " " not in k[1] else new.replace("Table", "Table ")
                reg.setdefault((tgt, key), reg[k])


_inherit_preprint_entries()


if __name__ == "__main__":
    raise SystemExit(main())

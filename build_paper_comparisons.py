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
import csv
import pathlib
import re
import sqlite3
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
COUNT_ROW = re.compile(r"(?i)^\s*#|\b(psms?|spectra|counts?|size|num\.?)\b")
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
    (re.compile(r"(?i)amino[- ]?acid|\bamino\b|\bAA\b|residue[- ]level"), "amino acid"),
    (re.compile(r"(?i)peptide|\bpep\b|\bpept\.|full[- ]sequence"), "peptide"),
    (re.compile(r"(?i)\bPTM\b|modification"), "ptm"),
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
    tok = tok.strip()
    m = PM.match(tok)
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
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def row_kind(row: list[dict]) -> str:
    """'data', 'interstice' or 'other'.

    An INTERSTICE is a short row with no numbers, which is what a row-group
    label looks like when it is set on its own line between data rows:
    CrossNovo's appendix tables put 'AminoAcid', 'Precision', 'Peptide' and
    'Recall' on four such rows. Treated as a block boundary they cut one
    8-row table into fragments, and the fragment below the header then had no
    header at all, which is where 29 of the rejections came from.
    """
    nums = sum(1 for w in row if numeric(w["text"]))
    if nums >= 2:
        return "data"
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
    centres: list[float] = []
    for r in rows[lo:hi + 1]:
        for w in r:
            if numeric(w["text"]):
                centres.append((w["x0"] + w["x1"]) / 2)
    if not centres:
        return []
    centres.sort()
    # Single-linkage on the gaps. 9 pt is wider than the inter-digit spacing
    # inside one number and narrower than any inter-column gutter seen here.
    groups: list[list[float]] = [[centres[0]]]
    for c in centres[1:]:
        if c - groups[-1][-1] <= 9.0:
            groups[-1].append(c)
        else:
            groups.append([c])
    mids = [sum(g) / len(g) for g in groups]
    if len(mids) == 1:
        half = 12.0
        return [(mids[0] - half, mids[0] + half)]
    bounds = [mids[0] - (mids[1] - mids[0]) / 2]
    bounds += [(mids[i - 1] + mids[i]) / 2 for i in range(1, len(mids))]
    bounds.append(mids[-1] + (mids[-1] - mids[-2]) / 2)
    return [(bounds[i], bounds[i + 1]) for i in range(len(mids))]


def phrases(row: list[dict], gap: float = 6.0) -> list[dict]:
    """Group a row's words into phrases, so a multi-word header stays one unit.

    'Amino acid precision' arrives as one token from some producers and three
    from others. Treating the three separately sends each to whichever column
    it happens to sit over, which is how a spanner silently becomes part of a
    method name.
    """
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
               left_bound: float, max_header: int = 5
               ) -> tuple[float, float, float, float]:
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
    first = (cap_end if 0 <= cap_end < lo and lo - cap_end <= max_header
             else max(0, lo - 3))
    last = max(hi, cap_end if cap_end > hi else hi)
    band = [w for i in range(first, min(last + 1, len(rows))) for w in rows[i]]
    if not band:
        return (0.0, 0.0, 0.0, 0.0)
    return (max(0.0, left_bound - 12),
            min(w["top"] for w in band) - 6,
            edges[-1][1] + 14,
            max(w["bottom"] for w in band) + 6)


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
                 hard_floor: int = 0):
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

    def clipped(r):
        return [w for w in r if w["x1"] > lo_x and w["x0"] < hi_x]

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
            if gap > 2.6 * max(local, 3.0):
                break
        r = clipped(rows[i])
        if not r:
            taken += 1
            if taken >= max_rows:
                break
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
              else [q for q in phrases(r) if not prosey(q["text"])])
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
        for q in [q for q in ph if q["x1"] <= edges[0][0]]:
            stub.insert(0, q["text"])
        if not inside:
            taken += 1
            if taken >= max_rows:
                break
            continue
        if is_own_row:
            # A row of column headers: assign the individual WORDS, so two
            # labels that a merge would have joined stay in their own columns.
            for w in r:
                k = assign(w, edges)
                if k is not None:
                    own[k].insert(0, w["text"])
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
            for q in list(inside):
                k = assign(q, edges)
                if k is not None and edges[k][0] - 1 <= q["x0"] and q["x1"] <= edges[k][1] + 1:
                    own[k].insert(0, q["text"])
                else:
                    own_wide.append(q)
            inside = own_wide
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
    out = desquash(text)
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


def _forms(text: str) -> tuple[str, str, str]:
    return text, re.sub(r"\s+", "", text), desquash(text)


PROSE_TOKEN = re.compile(r"^[a-z][a-z.,;:]{9,}$")


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
    return bool(PROSE_TOKEN.match(t)) or len(t) > 34


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


LABEL_ROW = re.compile(r"(?i)^\s*Tab(?:le|\.)\s*(S?\d{1,2}|[IVX]{1,4})\b")


def label_rows(rows: list[list[dict]]) -> list[int]:
    return [i for i, r in enumerate(rows)
            if LABEL_ROW.match(" ".join(w["text"] for w in r))]


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


def caption_text(rows: list[list[dict]], li: int | None,
                 blocks: list[tuple[int, int]]) -> tuple[str, str, int]:
    """The label, the caption prose at row `li`, and the caption's LAST row.

    The last row matters to the header walk. A caption runs over two or three
    lines, and stopping the walk after the caption's FIRST line let its
    continuation be read as a column spanner: every column of CrossNovo's
    Table 2 came back with the spanner
    'set. Theboldfontindicatesthebestperformance.'
    """
    if li is None:
        return "", "", -1
    text = " ".join(w["text"] for w in rows[li])
    m = LABEL_ROW.match(text)
    label = m.group(0).strip() if m else ""
    # RESERVE THE ROW DIRECTLY ABOVE EACH DATA BLOCK for the header. Without
    # that, a caption's continuation lines ran on into the header row and
    # swallowed it, which moved the header walk's floor past the only header
    # there was and rejected the table for having none. A caption line sitting
    # immediately above a table with no header in between would mean the table
    # has no header, which is a rejection either way.
    stop = len(rows)
    for lo, _ in blocks:
        if lo > li:
            stop = min(stop, lo - 1)
    parts = [text]
    for j in range(li + 1, min(li + 4, stop)):
        nxt = rows[j]
        if sum(1 for w in nxt if numeric(w["text"])) >= 2:
            break
        if LABEL_ROW.match(" ".join(w["text"] for w in nxt)):
            break
        parts.append(" ".join(w["text"] for w in nxt))
    last = li + len(parts) - 1
    cap = re.sub(r"\s+", " ", " ".join(parts)).strip()
    cap = re.sub(r"(?i)^" + re.escape(label) + r"\s*[.:|\u2013\u2014]?\s*", "", cap) if label else cap
    return label, cap, last


def extract(page) -> tuple[list[dict], list[dict], int]:
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
    words = strip_line_numbers(
        page.extract_words(use_text_flow=False, keep_blank_chars=False))
    if not words:
        return [], [], 0
    rows = word_rows(words)
    rules = page_rules(page)
    out, vetoed, uncaptioned = [], [], 0
    blocks = [b for b in data_blocks(rows) if len(column_edges(rows, *b)) >= 2]
    paired = pair_captions(rows, blocks)
    for lo, hi in blocks:
        edges = column_edges(rows, lo, hi)
        table_label, caption, cap_end = caption_text(
            rows, paired.get((lo, hi)), blocks)
        if not table_label:
            uncaptioned += 1
            continue
        # Computed BEFORE the vetoes, because the review page shows a refused
        # table beside its picture too, and a veto is exactly the case a human
        # most needs to see.
        crop_lb = min([w["x0"] for r in rows[lo:hi + 1] for w in r
                       if w["x1"] <= edges[0][0] and w["x0"] > edges[0][0] - 220]
                      or [edges[0][0] - 150])
        bbox = block_bbox(rows, lo, hi, cap_end, edges, crop_lb)
        try:
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
        multi = None
        pending: list[str] = []          # interstitial group-label words
        for r in rows[lo:hi + 1]:
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
            label = row_label(r, edges[0][0])
            if cells and COUNT_ROW.search(label):
                dropped += 1
                continue
            missing = [w["text"].strip().lower() for w in r
                       if w["text"].strip().lower() in NOT_RUN]
            if len(cells) == 0:
                # A row with no cells inside the table is either an
                # interstitial group label or a rule. Its words are carried to
                # the next data row rather than discarded, because that is
                # where 'AminoAcid' / 'Precision' live when a stacked table
                # sets its metric on its own line.
                if row_kind(r) == "interstice":
                    pending.extend(w["text"] for w in r)
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
            body.append({"label": label, "cells": cells, "extra": pending})
            pending = []
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

        left_bound = min(
            [w["x0"] for r in rows[lo:hi + 1] for w in r
             if w["x1"] <= edges[0][0] and w["x0"] > edges[0][0] - 220]
            or [edges[0][0] - 150]) - 4
        li = paired.get((lo, hi))
        own, span, stub, span_ambiguous = header_model(
            rows, lo, edges, left_bound=left_bound,
            floor=(cap_end + 1 if 0 <= cap_end < lo else 0), rules=rules,
            hard_floor=(li + 1 if li is not None and li < lo else 0))
        if not own and not span:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": "G3 no header above the block",
                           "bbox": bbox, "page": page.page_number})
            continue
        out.append({"edges": edges, "own": own, "span": span, "stub": stub,
                    "body": body, "dropped_rows": dropped,
                    "caption": caption, "table_label": table_label,
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
    squashed = re.sub(r"[^A-Za-z0-9]", "", text).lower()
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
    raw = re.sub(r"[*+\u2020\u2021\u00a7\u00b6]+\s*$", "", raw).strip()
    if not raw:
        raise Reject("N1 empty header")
    # A curated per-paper label wins over every general rule.
    pa = PAPER_LABEL_ALIASES.get((pub_id, norm(raw))) if pub_id else None
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
    ],
}


def resolve_dataset(con: sqlite3.Connection, caption: str, near: str,
                    pub_id: int, hint: str | None = None
                    ) -> tuple[int | None, str | None, int | None, str | None]:
    """(dataset_id, dataset_name, dataset_version_id, printed) or raise Reject.

    Three sources in order: the caption, then the surrounding prose, then the
    paper's own publication_dataset links as a last resort.
    """
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
        for scope in (caption, near):
            hits = [(m.group(0), v) for rx, v in VERSION_LEXICON.get(name, [])
                    if (m := rx.search(scope))]
            versions = {v for _, v in hits}
            if len(versions) > 1:
                raise Reject(f"D3 {'caption' if scope is caption else 'prose'} "
                             f"names several versions of {name}: {sorted(versions)}")
            if versions:
                printed, version = hits[0]
                got = con.execute(
                    "SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
                    (did, version)).fetchone()
                return did, name, (got[0] if got else None), printed
        return did, name, None, None
    found: list[tuple[re.Pattern, str]] = []
    for scope in (caption, near):
        found = [(rx, nm) for rx, nm in DATASET_CUES if rx.search(scope)]
        if found:
            break
    names = {nm for _, nm in found}
    if len({DATASET_TARGETS.get(n, (n, None)) for n in names}) > 1:   # D2
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
        if len({m.group(0).lower() for m in rx.finditer(t)}) >= min_methods \
           and len(DEC.findall(t)) >= min_decimals:
            out.append(i)
    return out


AUDIT_COLUMNS = [
    "publication_id", "title", "pdf_file", "pdf_page", "table_label", "verdict",
    "reason", "caption", "subject_method", "subject_resolved",
    "n_columns", "n_data_rows", "n_cells", "dropped_rows",
    "methods_printed", "methods_resolved", "methods_unresolved",
    "paper_vocabulary", "metrics_resolved", "levels",
    "dataset_printed", "dataset_resolved", "dataset_version_resolved",
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
        path = paths[0]
        try:
            pdf = pdfplumber.open(path)
        except Exception as exc:
            tally[f"paper: PDF would not open ({type(exc).__name__})"] += 1
            continue
        with pdf:
            pages_text = [(pg.extract_text() or "") for pg in pdf.pages]
            cands = candidate_pages(pages_text, rx)
            if not cands:
                tally["paper: no candidate results page"] += 1
                continue
            seen_papers += 1
            whole = "\n".join(pages_text)
            vocab = paper_vocabulary(whole, con)
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
                    "subject_method": subject["name"] if subject else "",
                    "paper_vocabulary": "|".join(
                        sorted(n for vs in vocab.values() for _, n in vs)),
                })
                tables, vetoed, uncap = extract(page)
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
    # DiffNovo, Tables 1 and 2. The header rows are shredded: 'Cascadia' sits a
    # row above its group and 'DeepNovo-DIAPepNet' arrives as one token, so no
    # reading of the page recovers which column is which.
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
}


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
TABLE_METRIC: dict[tuple[int, str], tuple[str, str] | list[tuple[str, str]]] = {
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
    # RefineNovo, Table 6. Its caption says only "Performance comparison on
    # the NovoBench benchmark (yeast test species)". The metric is NovoBench's
    # peptide-level precision, which the numbers themselves confirm: its
    # Casanovo* reads 0.48 and BENCHMARKS.md records NovoBench's retrained
    # Casanovo at 0.481 on nine-species.
    (16, "Table6"): ("precision", "peptide"),
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
            if norm(printed) in SELF_WORDS and subject:
                got[k] = (subject["id"], subject["name"], None)
            else:
                got[k] = resolve_method(printed, vocab, index, pub_id)
        return "columns", got, {}, {k: v for k, v in enumerate(over)}

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
    for i, r in enumerate(tb["body"]):
        hit, rest, matched = resolve_in_label(
            r["label"], vocab, index, subject, pub_id)
        rows[i] = hit
        printed_rows[i] = matched or r["label"]
        # Words from an interstitial label row count as this row's leftovers:
        # they are the group label, just set on a line of their own.
        leftovers[i] = rest + list(r.get("extra") or [])
    n_col = len({(v[0], v[2] or "") for v in cols.values() if v})
    n_row = len({(v[0], v[2] or "") for v in rows.values() if v})
    if n_col >= 2 and n_col >= n_row:
        return "columns", cols, {}, printed_cols
    if n_row >= 2:
        return "rows", rows, leftovers, printed_rows
    if span_blocked:
        raise Reject(f"N1 the method names are in a spanner whose grouping no "
                     f"rule states, so columns {sorted(amb)} are undecidable; "
                     f"not guessed")
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
    body = []
    for r in tb["body"]:
        cells = {idx[k]: c for k, c in r["cells"].items() if k in idx}
        if cells:
            body.append({"label": r["label"], "cells": cells,
                         "extra": list(r.get("extra") or [])})
    return {"edges": [tb["edges"][k] for k in cols],
            "own": {idx[k]: tb["own"][k] for k in cols if k in tb["own"]},
            "span": {idx[k]: tb["span"][k] for k in cols if k in tb["span"]},
            "stub": tb["stub"], "body": body,
            "dropped_rows": tb["dropped_rows"], "caption": tb["caption"],
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
    if unresolved:
        raise Reject(f"N1 unresolved on the method axis ({axis}): {unresolved}")
    if subject and not any(v[0] == subject["id"] for v in methods.values()):
        raise Reject("N2 no self column")

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
    ctx_extra = " ".join([tb["caption"], tb["stub"]])
    subsets = {}

    def along_columns():
        out = {}
        for k in range(len(edges)):
            ctx = " ".join(span.get(k, [])) + " " + col_head[k]
            metric = metric_of(ctx)
            level = level_of(ctx) or level_of(ctx_extra)
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
            if not (metric_of(ctx) or level_of(ctx)):
                # A segment with no label of its own: the table states one
                # metric for all of them, so fall back to the caption.
                ctx = ctx + " " + ctx_extra
            for i in seg:
                metric = metric_of(ctx) or metric_of(ctx_extra)
                level = level_of(ctx) or level_of(ctx_extra)
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
        if isinstance(pinned, list):
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
        metric = metric_of(ctx_extra)
        if not metric:
            raise Reject(f"M1 no metric on either axis or in the caption; "
                         f"headers {col_head}")
        found = {}
        for k in range(len(edges)):
            ctx = " ".join(span.get(k, [])) + " " + col_head[k]
            level = level_of(ctx) or level_of(ctx_extra)
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
            parts = [x for x in (sp, head)
                     if x and x != "?" and not metric_of(x) and not level_of(x)
                     and not any(rx.search(x) for rx, _ in DATASET_CUES)]
            subsets[k] = unsquash_label(" ".join(parts))
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
            over = mk_legend.get(label_marker(printed))
            if over and over.get("basis"):
                bases[k] = (over["basis"],
                            f"the table's legend marks {printed!r}", False)

    def cell_meta(k, ri):
        """(method, metric, level, subset) for one cell, whichever the layout."""
        m = methods.get(k if axis == "columns" else ri)
        j = k if metric_axis == "columns" else ri
        # When the methods are columns the row label is normally the subset --
        # a species, an enzyme. But if the row label is what NAMES THE METRIC,
        # it is not also a subset, or every cell would claim a subset of
        # 'Peptide precision'.
        sub = (subsets[k] if sub_over
               else "" if (axis == "columns" and metric_axis == "rows")
               else unsquash_label(tb["body"][ri]["label"]) if axis == "columns"
               else subsets[k])
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

    vals = [v for r in tb["body"] for c in r["cells"].values()
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

    did, dname, vid, dprinted = resolve_dataset(
        con, tb["caption"], near, base["publication_id"],
        tb.get("dataset_hint") or table_dataset_label(tb))
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
    if collect is not None:
        # The resolved structure, so a review page can show the parse beside a
        # picture of the printed table without reimplementing any of this.
        collect.append({
            "verdict": "accepted", "reason": "",
            "table_label": tb["table_label"], "caption": tb["caption"],
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
            "body": [{"label": r["label"],
                      "cells": {k: c["printed"] for k, c in r["cells"].items()}}
                     for r in tb["body"]],
            "basis": {methods[k][1]: bases[k][0] for k in sorted(bases)},
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


if __name__ == "__main__":
    raise SystemExit(main())

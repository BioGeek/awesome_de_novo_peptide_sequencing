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
# A cell holding two numbers, which is a different grain and rejects the table.
MULTI = re.compile(r"\d\s*/\s*\d|\d\s*\|\s*\d")
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
                     r"number of parameters|speed[- ]?up|wall[- ]clock")

# M1: metric headers, mapped to the closed vocabulary. Longest first.
METRIC_WORDS = [
    (re.compile(r"(?i)ptm[- ]?prec"), "ptm-precision"),
    (re.compile(r"(?i)ptm[- ]?rec"), "ptm-recall"),
    (re.compile(r"(?i)\bAUC\b|area under"), "auc"),
    (re.compile(r"(?i)\bAP\b|average precision"), "ap"),
    (re.compile(r"(?i)\bF1\b|f1[- ]score"), "f1"),
    (re.compile(r"(?i)precision|\bprec\b|\bprec\."), "precision"),
    (re.compile(r"(?i)recall|\brec\b|\brec\."), "recall"),
    (re.compile(r"(?i)accuracy|\bacc\b|\bacc\."), "accuracy"),
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


def header_model(rows: list[list[dict]], lo: int,
                 edges: list[tuple[float, float]], max_rows: int = 4,
                 left_bound: float | None = None, floor: int = 0):
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
    taken = 0
    for i in range(lo - 1, floor - 1, -1):
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
        ph = phrases(r)
        # A HEADER PHRASE IS SHORT. The word-count test above cannot see prose
        # in a PDF whose producer drops spaces: a line of discussion arrives as
        # ONE 86-character token, so it passes "few words" and, being fewer
        # phrases than there are columns, was distributed across them as a
        # spanner. That is how every column of CrossNovo's Table 2 came back
        # claiming the metric 'precision', from the sentence
        # 'significantlyoutperformsthebaselinemodelsinbothprecisionandrecall...'
        # sitting above it -- which then collapsed its two stacked metric
        # groups into one and tripped G6. The longest real header here is
        # 'Amino acid precision' at 20 characters.
        if any(len(q["text"]) > 34 for q in ph):
            break

        inside = [q for q in ph if q["x1"] > edges[0][0]]
        for q in [q for q in ph if q["x1"] <= edges[0][0]]:
            stub.insert(0, q["text"])
        if not inside:
            taken += 1
            if taken >= max_rows:
                break
            continue
        if len(inside) >= max(2, int(0.8 * len(edges))):
            for q in inside:
                k = assign(q, edges)
                if k is not None:
                    own[k].insert(0, q["text"])
        else:
            for k, c in enumerate(col_c):
                nearest = min(inside, key=lambda q: abs((q["x0"] + q["x1"]) / 2 - c))
                span[k].insert(0, nearest["text"])
        taken += 1
        if taken >= max_rows:
            break
    return own, span, " ".join(stub).strip()


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
        try:
            caption_verdict(caption)
        except Reject as exc:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": str(exc)})
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
                if MULTI.search(w["text"]):                  # G4
                    multi = w["text"]
                    break
                parsed = numeric(w["text"])
                if parsed is None:
                    continue
                k = assign(w, edges)
                if k is None:
                    continue
                if k in cells:
                    ragged = True
                cells[k] = {"value": parsed[0], "stddev": parsed[1],
                            "printed": w["text"].strip()}
            if multi:
                break
            label = row_label(r, edges[0][0])
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
                    dropped += 1
                    continue
                ragged = True
            body.append({"label": label, "cells": cells, "extra": pending})
            pending = []
        if multi:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": f"G4 multi-valued cell {multi!r}"})
            continue
        if ragged:                                           # G1
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": f"G1 ragged grid ({len(edges)} columns)"})
            continue
        if not body:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": "G3 no data rows survived"})
            continue
        # Trailing interstitial words belong to the last group, not to nothing:
        # a stacked table whose final label line sits BELOW its last data row
        # would otherwise lose it.
        if pending and body:
            body[-1]["extra"] = body[-1]["extra"] + pending

        left_bound = min(
            [w["x0"] for r in rows[lo:hi + 1] for w in r
             if w["x1"] <= edges[0][0] and w["x0"] > edges[0][0] - 220]
            or [edges[0][0] - 150]) - 4
        own, span, stub = header_model(
            rows, lo, edges, left_bound=left_bound,
            floor=(cap_end + 1 if 0 <= cap_end < lo else 0))
        if not own and not span:
            vetoed.append({"table_label": table_label, "caption": caption,
                           "reason": "G3 no header above the block"})
            continue
        out.append({"edges": edges, "own": own, "span": span, "stub": stub,
                    "body": body, "dropped_rows": dropped,
                    "caption": caption, "table_label": table_label})
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
                break
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
                break
    return vocab


# A trailing version token, INCLUDING a bare integer: a paper comparing two of
# its own configurations prints 'DiffuNovo 1' and 'DiffuNovo 2', which resolved
# to nothing and took DiffuNovo's whole Table 2 down with it.
VERSION_TAIL = re.compile(
    r"(?i)[\s._-]*(v\s?\d(?:\.\d+)?|\d\.\d+|\d|[+]|-?DIA|-?DDA)$")


def resolve_method(printed: str, vocab: dict[str, list[tuple[int, str]]],
                   index: dict[str, list[tuple[int, str]]]) -> tuple[int, str, str | None]:
    """Resolve a printed column header to (algorithm_id, name, printed version).

    Raises Reject on a miss. A wrong guess here would be invisible in the data;
    a rejection is in the tally, which is the trade this whole script makes.
    """
    raw = printed.strip().strip("|,")
    if not raw:
        raise Reject("N1 empty header")
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
# caption or the neighbouring prose. These cues map printed phrasing to a
# `dataset.name`, and a phrase absent from here leaves the dataset unresolved
# rather than guessed.
DATASET_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)nine[- ]species|9[- ]species|\bnine species\b"), "Nine-species benchmark"),
    (re.compile(r"(?i)ProteomeTools"), "ProteomeTools"),
    (re.compile(r"(?i)MassIVE-?KB"), "MassIVE-KB"),
]

# Dataset names that comparison tables print and the catalog has NO row for.
# Registered so a multi-dataset table can still be SPLIT: the parts naming a
# known dataset are read, and the parts naming these are rejected loudly by
# name. Without them one unattributable column blocks the split and the whole
# table is lost, which hides the gap instead of reporting it.
#
# Both of these are real holes in the catalog, found by this builder. DiffuNovo
# and ReNovo both report on all three benchmarks side by side.
UNCATALOGUED_DATASETS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bHC-?PT\b"), "HC-PT"),
    (re.compile(r"(?i)\b7[- ]?species\b|\bseven[- ]?species\b"), "Seven-species"),
]

ALL_DATASET_CUES = DATASET_CUES + UNCATALOGUED_DATASETS


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
        # A per-column spanner naming the dataset is MORE specific than the
        # caption, which on a split table names all of them at once.
        row = con.execute("SELECT id FROM dataset WHERE name = ?", (hint,)).fetchone()
        if not row:
            raise Reject(f"D1 spanner names {hint!r}, absent from the dataset table")
        did = row[0]
        for rx, version in VERSION_LEXICON.get(hint, []):
            for scope in (caption, near):
                m = rx.search(scope)
                if m:
                    got = con.execute(
                        "SELECT id FROM dataset_version WHERE dataset_id=? AND version=?",
                        (did, version)).fetchone()
                    return did, hint, (got[0] if got else None), m.group(0)
        return did, hint, None, None
    found: list[tuple[re.Pattern, str]] = []
    for scope in (caption, near):
        found = [(rx, nm) for rx, nm in DATASET_CUES if rx.search(scope)]
        if found:
            break
    names = {nm for _, nm in found}
    if len(names) > 1:                                            # D2
        raise Reject(f"D2 multi-dataset table {sorted(names)}")
    if not names:
        rows = con.execute(
            "SELECT DISTINCT d.id, d.name FROM publication_dataset pd "
            "JOIN dataset d ON d.id = pd.dataset_id "
            "WHERE pd.publication_id = ? AND d.kind = 'benchmark'", (pub_id,)).fetchall()
        if len(rows) == 1:
            return rows[0][0], rows[0][1], None, None
        raise Reject("D1 dataset unresolved")
    name = names.pop()
    row = con.execute("SELECT id FROM dataset WHERE name = ?", (name,)).fetchone()
    if not row:                                                   # D1
        raise Reject(f"D1 cue names {name!r}, absent from the dataset table")
    did = row[0]
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


def resolve_in_label(label: str, vocab, index, subject):
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
        return None, []
    for width in range(len(toks), 0, -1):
        for start in range(len(toks) - width, -1, -1):
            cand = " ".join(toks[start:start + width])
            if norm(cand) in SELF_WORDS:
                if subject:
                    return (subject["id"], subject["name"], None), \
                           toks[:start] + toks[start + width:]
                continue
            hit = try_resolve(cand, vocab, index)
            if hit:
                return hit, toks[:start] + toks[start + width:]
    return None, toks


def try_resolve(label, vocab, index):
    """resolve_method() as an Optional, for deciding orientation."""
    try:
        return resolve_method(label, vocab, index)
    except Reject:
        return None


def orientation(tb, vocab, index, subject) -> tuple[str, dict, dict]:
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
    cols = {}
    for k in range(n):
        head = " ".join(tb["own"].get(k, [])).strip()
        if norm(head) in SELF_WORDS and subject:
            cols[k] = (subject["id"], subject["name"], None)
        else:
            cols[k] = try_resolve(head, vocab, index)
    rows, leftovers = {}, {}
    for i, r in enumerate(tb["body"]):
        hit, rest = resolve_in_label(r["label"], vocab, index, subject)
        rows[i] = hit
        # Words from an interstitial label row count as this row's leftovers:
        # they are the group label, just set on a line of their own.
        leftovers[i] = rest + list(r.get("extra") or [])
    n_col = len({(v[0], v[2] or "") for v in cols.values() if v})
    n_row = len({(v[0], v[2] or "") for v in rows.values() if v})
    if n_col >= 2 and n_col >= n_row:
        return "columns", cols, {}
    if n_row >= 2:
        return "rows", rows, leftovers
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
        hits = {nm for rx, nm in ALL_DATASET_CUES if rx.search(sp)}
        groups.setdefault(hits.pop() if len(hits) == 1 else None, []).append(k)
    if None in groups or len(groups) < 2:
        return None
    return {k: v for k, v in groups.items() if k}


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
            "dataset_hint": name}


def emit(con, base, tb, vocab, index, subject, near, whole, audit, tally, show,
         split=True) -> int:
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
                               whole, audit, tally, show, split=False)
                except Reject as exc:
                    tally[f"rejected: {str(exc).split('(')[0].strip()}"] += 1
                    audit.append({**row, "verdict": "rejected", "reason": str(exc)})
            return ok
    edges, own, span = tb["edges"], tb["own"], tb["span"]
    axis, resolved_axis, leftovers = orientation(tb, vocab, index, subject)
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

    def row_groups():
        """Cluster the row-group label words, which arrive scattered.

        A group label is set across several row labels ('Amino' / 'Acid' /
        'Precision' over eleven rows) or on interstitial lines of its own.
        """
        groups: list[dict] = []
        for i in sorted(leftovers):
            text = " ".join(leftovers[i])
            if not (metric_of(text) or level_of(text)):
                continue
            if groups and i - groups[-1]["last"] <= 2:
                groups[-1]["text"] += " " + text
                groups[-1]["rows"].append(i)
                groups[-1]["last"] = i
            else:
                groups.append({"text": text, "rows": [i], "last": i})
        return groups

    def along_rows():
        groups = row_groups()
        # SEGMENT BY WHERE THE METHOD LIST RESTARTS, and pair the segments with
        # the group labels in order. Nearest-centre, which is right for a
        # column spanner, is wrong here: a stacked table lists the same ten
        # methods twice and sets each group label near the TOP of its run, so
        # the midpoint fell inside the first group and the last two of its rows
        # were read as the second metric. A repeat of a method is an
        # unambiguous boundary and needs no typography at all.
        segments: list[list[int]] = []
        current: set = set()
        for i in range(len(tb["body"])):
            key = (methods[i][0], methods[i][2] or "") if i in methods else None
            if key and key in current:
                segments.append([])
                current = set()
            if not segments:
                segments.append([])
            segments[-1].append(i)
            if key:
                current.add(key)
        seg_of = {i: si for si, seg in enumerate(segments) for i in seg}
        paired_groups = (dict(enumerate(groups))
                         if len(segments) == len(groups) else None)
        out = {}
        for i in range(len(tb["body"])):
            ctx = " ".join(leftovers.get(i, []))
            if paired_groups is not None:
                ctx += " " + paired_groups[seg_of[i]]["text"]
            elif groups:
                near_g = min(groups, key=lambda g: abs(
                    sum(g["rows"]) / len(g["rows"]) - i))
                ctx += " " + near_g["text"]
            metric = metric_of(ctx)
            level = level_of(ctx) or level_of(ctx_extra)
            if not metric or not level:
                return None
            out[i] = (metric, level)
        return out

    found = along_columns()
    metric_axis = "columns"
    if found is None and axis == "rows":
        found = along_rows()
        metric_axis = "rows"
    if found is None:
        # Last resort: the caption names one metric for the whole table.
        metric = metric_of(ctx_extra)
        level = level_of(ctx_extra)
        if not metric:
            raise Reject(f"M1 no metric on either axis or in the caption; "
                         f"headers {col_head}")
        if not level:
            # Distinguished from M1 on purpose: the metric was found and only
            # the level is missing, which is a different thing to go and fix.
            raise Reject(f"M2 metric {metric!r} found but no level; "
                         f"headers {col_head}")
        found = {k: (metric, level) for k in range(len(edges))}
        metric_axis = "columns"
    metrics = {j: v[0] for j, v in found.items()}
    levels = {j: v[1] for j, v in found.items()}

    # The SUBSET is whatever the non-method, non-metric axis names. When the
    # methods are rows it is the column, and it must carry the SPANNER as well
    # as the header: CrossNovo's antibody tables print 'AspN' twice, once per
    # chain, distinguished only by the spanner above it, and keying on the
    # header alone made the two columns one measurement.
    for k in range(len(edges)):
        if axis == "columns":
            subsets[k] = ""
        else:
            head = col_head[k]
            sp = " ".join(span.get(k, [])).strip()
            parts = [x for x in (sp, head)
                     if x and x != "?" and not metric_of(x) and not level_of(x)]
            subsets[k] = " ".join(parts)
    base.update({"metrics_resolved": "|".join(metrics[j] for j in sorted(metrics)),
                 "levels": "|".join(levels[j] for j in sorted(levels)),
                 "reason": f"axis={axis} metric_axis={metric_axis}"})

    # G6: no two cells may claim the same measurement. The key carries the
    # metric, the level and the subset, matching paper_comparison_result's own
    # UNIQUE constraint, because one method legitimately appears many times in
    # one table: ContraNovo's Table 1 is six methods crossed with amino-acid
    # and peptide precision, so 'Peaks.' is columns 1 and 7.
    def cell_meta(k, ri):
        """(method, metric, level, subset) for one cell, whichever the layout."""
        m = methods.get(k if axis == "columns" else ri)
        j = k if metric_axis == "columns" else ri
        sub = tb["body"][ri]["label"] if axis == "columns" else subsets[k]
        return m, metrics[j], levels[j], sub

    seen = set()
    for ri, r in enumerate(tb["body"]):
        for k in r["cells"]:
            m, metric, level, sub = cell_meta(k, ri)
            if not m:
                continue
            key = (m[0], m[2] or "", metric, level, sub)
            if key in seen:
                raise Reject(f"G6 duplicate measurement {m[1]} "
                             f"{metric}/{level}/{sub or '-'}")
            seen.add(key)

    vals = [c["value"] for r in tb["body"] for c in r["cells"].values()]
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
        con, tb["caption"], near, base["publication_id"], tb.get("dataset_hint"))
    base.update({"dataset_resolved": dname or "", "dataset_printed": dprinted or "",
                 "dataset_version_resolved": vid or ""})

    bases = {k: find_basis(whole, v[1]) for k, v in methods.items()}
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
            results.append(f"{m[1]}{'@' + m[2] if m[2] else ''}:{metric}/"
                           f"{level}/{sub or '-'}={cell['value'] / scale:.4f}")
    base.update({"verdict": "accepted", "reason": f"axis={axis}",
                 "subject_resolved": subject["name"] if subject else "",
                 "proposed_results": " ".join(results[:80])})
    audit.append(dict(base))

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
                         else r["label"]))[:21]
            print(f"    {tag:22}" + "".join(
                f"{r['cells'][k]['printed'][:10]:>11}" if k in r["cells"] else f"{'':>11}"
                for k in range(len(edges))))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

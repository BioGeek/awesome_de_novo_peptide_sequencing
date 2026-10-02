#!/usr/bin/env python3
"""Build a LOCAL review page: every mined table beside a picture of the printed one.

Spot-checking an extraction by reading a CSV is hopeless, because the thing you
need to check is whether the grid matches the page. This renders each table's
region out of the PDF and puts the crop next to the parse, side by side, so a
wrong column or a mis-assigned metric is visible at a glance.

It also renders the tables the miner REFUSED, with the reason, which is the
half worth looking at: a rejection is either a correct refusal or a gap, and
only a person reading the printed table can say which.

    uv run --with pdfplumber python3 review_comparisons.py              # all
    uv run --with pdfplumber python3 review_comparisons.py --ids 49,58   # some
    uv run --with pdfplumber python3 review_comparisons.py --rejected-only

**IT WRITES OUTSIDE THE REPOSITORY AND IS NEVER PUBLISHED.** The output is a
folder of PNGs and one HTML file under the PDF library, the same place
`build_pdf_library.py` keeps `pdf_status.csv`: it quotes paper figures, so it
is a private reading aid and not something to put on a public site. Nothing it
produces is committed, and it must never run in CI, which has neither a PDF
library nor poppler.

Rendering is `pdftoppm` plus a PIL crop rather than pdfplumber's `to_image()`,
because poppler is already a dependency of this project's PDF handling and
pdfplumber's image path pulls in a separate toolchain.
"""
from __future__ import annotations

import argparse
import collections
import html
import pathlib
import subprocess
import sys
import json
import re
import urllib.parse

import build_pdf_library as bpl
import build_paper_comparisons as B

OUT = bpl.DEFAULT_DIR / "comparison-review"
CACHE = OUT / ".pages"
# Which tables a person has read and signed off. Lives beside the crops,
# outside the repository, because it is this reader's review state and not
# catalog data. Each entry keeps a FINGERPRINT as well as the id, because an id
# encodes the block's order on its page and that order can shift when the
# miner improves; a stale approval is then reported rather than silently
# carried over.
# Two states, both meaning "a person has read this and is done with it":
# APPROVED for a parse confirmed correct, DISMISSED for a refusal confirmed to
# be the right refusal. Both collapse, because the page is a worklist and the
# point is to shrink it.
APPROVED = OUT / "approved.json"
# What each crop actually shows, written for the cross-checker. Without it
# build_table_vlm.py had to guess from the audit CSV and compared a crop
# against whichever table came first for that publication, which made every
# verdict on a multi-table paper meaningless.
MANIFEST = OUT / "crops.json"
ITEMS = OUT / "items.json"


def _restore(it: dict) -> dict:
    """An item read back from JSON, with its integer keys put back.

    JSON turns every dict key into a string, and the page indexes cells,
    metrics and methods by integer column or row; read back unconverted, every
    lookup misses and every table renders empty.
    """
    def intkeys(d):
        return {(int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): v
                for k, v in (d or {}).items()}
    it = dict(it)
    for f in ("metrics", "levels", "subsets", "row_subsets"):
        if isinstance(it.get(f), dict):
            it[f] = intkeys(it[f])
    if isinstance(it.get("methods"), dict):
        it["methods"] = {k: tuple(v) for k, v in intkeys(it["methods"]).items()}
    for r in it.get("body") or []:
        r["cells"] = intkeys(r.get("cells"))
        r["absent"] = intkeys(r.get("absent"))
    if isinstance(it.get("bbox"), list):
        it["bbox"] = tuple(it["bbox"])
    return it
DPI = 150


# One page often carries several tables, so the rendered page is cached: the
# first version re-ran pdftoppm per table and rendered DiffuNovo's page seven
# times.
_PAGE_CACHE: dict[tuple[str, int], pathlib.Path | None] = {}


def page_png(pdf_path: pathlib.Path, page_no: int) -> pathlib.Path | None:
    key = (str(pdf_path), page_no)
    if key in _PAGE_CACHE:
        return _PAGE_CACHE[key]
    tmp = CACHE / f"pg-{abs(hash(key)) % (10 ** 12)}"
    try:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-f", str(page_no),
                        "-l", str(page_no), "-png", str(pdf_path), str(tmp)],
                       check=True, capture_output=True, timeout=180)
        made = sorted(tmp.parent.glob(tmp.name + "-*.png"))
        _PAGE_CACHE[key] = made[0] if made else None
    except Exception:
        _PAGE_CACHE[key] = None
    return _PAGE_CACHE[key]


def render(pdf_path: pathlib.Path, page_no: int, bbox, dest: pathlib.Path) -> bool:
    """Crop one table out of one page. Returns False if nothing could be made."""
    if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return False
    from PIL import Image
    src = page_png(pdf_path, page_no)
    if not src:
        return False
    with Image.open(src) as im:
        k = DPI / 72.0
        box = (max(0, int(bbox[0] * k) - 4), max(0, int(bbox[1] * k) - 4),
               min(im.width, int(bbox[2] * k) + 4),
               min(im.height, int(bbox[3] * k) + 4))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return False
        im.crop(box).save(dest)
    return True


# Every method name and alias the catalog knows, normalised. Filled once in
# main(); empty means the camel split in unsquash_name() stays off, which is
# the safe direction.
KNOWN_NAMES: set[str] = set()


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower()).replace("\u03c0", "pi")


def _near_known(stems: list[str]) -> bool:
    """A glued name within a typo of a catalog name counts as that name.

    Papers misspell methods: TSARseqNovo's table prints 'pi-HelexiNovo' for
    pi-HelixNovo, which no exact lookup knows, so it was split into
    'Helexi Novo'. Measured over all 136 distinct printed labels on the page:
    that misspelling scores 84 against 'helixnovo', and the one glued name
    that SHOULD split, 'PeaksNovo', scores 75 against its nearest
    ('pepnovo'); every species name scores 60 or below. 80 sits in that gap.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return False
    for x in stems:
        core = _norm(x)
        if len(core) >= 6 and any(fuzz.ratio(core, k) >= 80 for k in KNOWN_NAMES):
            return True
    return False


def unsquash_name(text: str) -> str:
    """A printed method label, with the spaces a PDF dropped put back.

    'PeaksNovo(Maetal.,2003)' -> 'Peaks Novo (Ma et al., 2003)'. Display only:
    what the miner RESOLVES is the catalog name, and what a row would store is
    the label exactly as printed. This is so a reviewer can read it.
    """
    t = (text or "").strip()
    t = re.sub(r"(?<=[^\s(])(?=\()", " ", t)                 # space before '('
    # THE CAMEL SPLIT IS ONLY TAKEN WHERE THE GLUED FORM IS NOT A METHOD NAME.
    # Splitting unconditionally gives 'Ada Novo' and 'Diffu Novo' for names
    # the paper really writes glued; never splitting leaves 'PeaksNovo', which
    # the paper writes as two words and the PDF squashed. The catalog decides:
    # 'DeepNovo' and 'AdaNovo' are names in it and stay whole, 'PeaksNovo' is
    # not one and becomes 'Peaks Novo'.
    head = t.split(" (")[0]
    # A LEADING 'vs' COMES OFF TOO, and goes back on with its space. A
    # difference row prints 'vsCasaNovo', and with the prefix glued on the
    # name looked unknown and came out 'vs Casa Novo'.
    lead = ""
    mv = re.match(r"(?i)^(vs\.?|versus)\s*(?=\S)", head)
    if mv:
        lead, head = mv.group(1) + " ", head[mv.end():]
        t = t[mv.end():]
    # A FOOTNOTE MARKER AND A VARIANT SUFFIX COME OFF FIRST. 'PrimeNovo-CV*'
    # is not a catalog name as printed and split into 'Prime Novo-CV*', while
    # 'PrimeNovo' is one; the same for 'Casanovo-pretrained'.
    stems = [head]
    bare = re.sub(r"[*\u2217+\u2020\u2021]+$", "", head)
    # A trailing CITATION and VERSION too: PLMNovo's table prints
    # 'PointNovo[43]' and 'Casanovov4.2[33]', which looked unknown with the
    # bracket on and came out 'Point Novo[43]'.
    bare = re.sub(r"\s*\[\d+(?:\s*[,\u2013-]\s*\d+)*\]\s*$", "", bare)
    stems.append(bare)
    stems.append(re.sub(r"\s*v?\d+(?:\.\d+)*$", "", bare))
    stems.append(re.sub(r"[-_][A-Za-z0-9]{1,12}$", "", bare))
    # With no catalog loaded the split is OFF, which is the safe direction:
    # the condition below is a negation, so an empty set would otherwise make
    # every glued name look unknown and split 'DiffuNovo' into 'Diffu Novo'.
    if head and KNOWN_NAMES and not any(_norm(x) in KNOWN_NAMES for x in stems if x) \
            and not _near_known(stems):
        split = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", head)
        if _norm(split.replace(" ", "")) == _norm(head):
            t = split + t[len(head):]
    t = re.sub(r"(?i)(?<=[a-z])et\s*al\s*\.?", " et al.", t)  # 'Maetal.' -> 'Ma et al.'
    t = re.sub(r",(?=\S)", ", ", t)                           # space after a comma
    t = re.sub(r";(?=\S)", "; ", t)
    return re.sub(r"\s+", " ", lead + t).strip()


def unsquash(text: str) -> str:
    """'ApisMellifera' -> 'Apis Mellifera', for display.

    The same camelCase split the miner applies to a subset before storing it.
    A label squashed with no case change, such as 'Clambacteria' for 'Clam
    bacteria', cannot be recovered and is shown as the page has it.
    """
    text = text or ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)).strip()


def printed_emphasis(rec: dict) -> dict | None:
    """(row, col) -> 'best' or 'second' AS THE PAPER PRINTS IT, or None.

    The paper's own bold and underline is the better answer than any ranking
    computed here, because it is what the picture beside this table shows.
    They disagree whenever a paper counts its own variants as one method:
    DiffuNovo's Table 2 bolds DiffuNovo (MBR) and underlines pi-HelixNovo, the
    best COMPETITOR, while a ranking over the values underlines DiffuNovo
    (Logits) at 0.785 against pi-HelixNovo's 0.765. Reading an emphasis the
    page does not carry invites exactly the doubt this page exists to remove.

    Returns None when the table marks nothing, so ranking() still has a job:
    not every paper emphasises anything, and a reader still wants the best
    value pointed at.
    """
    out = {}
    for i, r in enumerate(rec["body"]):
        for k in r.get("bold") or []:
            out[(i, k)] = "best"
        for k in r.get("underlined") or []:
            out.setdefault((i, k), "second")
    return out or None


def _subset(rec: dict, i: int, k) -> str:
    """The subset one cell belongs to, whichever axis carries it.

    With methods down the side, a species or an enzyme can head the COLUMN
    (CrossNovo) or a ROW GROUP (LIPNovo's leave-one-out Table 3, one species
    above each LIPNovo/Baseline pair). Grouping on the column alone ranked
    LIPNovo's nine species against each other as if they were one measurement.
    """
    if rec["axis"] != "rows":
        return rec["body"][i]["label"] or ""
    rs = rec.get("row_subsets") or {}
    return (rs.get(i) or rs.get(str(i)) or rec["subsets"].get(k) or "")


def ranking(rec: dict) -> dict:
    """(row, col) -> 'best' or 'second', within each measurement.

    A measurement is one (metric, level, subset): the same quantity on the same
    data, which is the only set across which comparing METHODS means anything.
    Every metric recorded here is higher-is-better, so the ranking is a plain
    maximum; if a lower-is-better metric is ever added this has to learn the
    direction.
    """
    groups: dict = {}
    maxis = rec["metric_axis"]
    for i, r in enumerate(rec["body"]):
        for k, printed in r["cells"].items():
            j = k if maxis == "columns" else i
            key = (rec["metrics"].get(j), rec["levels"].get(j),
                   _subset(rec, i, k))
            try:
                v = float(re.sub(r"[^0-9.\-]", "", printed.split("/")[0]) or "nan")
            except ValueError:
                continue
            if v != v:                                  # NaN
                continue
            groups.setdefault(key, []).append((v, i, k))
    # TIES ARE ALL BEST. Marking only the first of several equal values made a
    # tie look like a lone winner, and the clash check then reported every
    # other tied bold as "not the highest value": PLMNovo's Table 1 bolds
    # Casanovo v2 and PLMNovo (ESM-2 650M) at 0.676 for human amino-acid
    # precision, correctly, and the page called it a possible error in the
    # paper. Every cell at the top value is best, every cell at the next
    # distinct value is second.
    out: dict = {}
    for cells in groups.values():
        if len(cells) < 2:
            continue
        distinct = sorted({v for v, _i, _k in cells}, reverse=True)
        for v, i, k in cells:
            if v == distinct[0]:
                out[(i, k)] = "best"
            elif len(distinct) > 1 and v == distinct[1]:
                out[(i, k)] = "second"
    return out


def _measurements(rec: dict) -> dict:
    """(metric, level, subset) -> [(row, col), ...]: the cells one ranking spans."""
    maxis = rec["metric_axis"]
    groups: dict = {}
    for i, r in enumerate(rec["body"]):
        for k in r["cells"]:
            j = k if maxis == "columns" else i
            key = (rec["metrics"].get(j) or "?", rec["levels"].get(j) or "?",
                   _subset(rec, i, k))
            groups.setdefault(key, []).append((i, k))
    return groups


def _absent_peers(rec: dict, key) -> list[str]:
    """Methods printed as NOT RUN ('-') in the measurement `key`."""
    maxis, out = rec["metric_axis"], []
    for i, r in enumerate(rec["body"]):
        for k in (r.get("absent") or {}):
            j = k if maxis == "columns" else i
            kk = (rec["metrics"].get(j) or "?", rec["levels"].get(j) or "?",
                  _subset(rec, i, k))
            if kk == key:
                out.append(_who(rec, (i, k)))
    return out


def _value(rec: dict, c) -> float | None:
    try:
        v = rec["body"][c[0]]["cells"][c[1]]
        return float(re.sub(r"[^0-9.\-]", "", v.split("/")[0]) or "nan")
    except (KeyError, ValueError):
        return None


def _who(rec: dict, c) -> str:
    """The cell's method as the paper prints it, for a footnote."""
    i, k = c
    j = i if rec["axis"] == "rows" else k
    m = rec["methods"].get(j) or rec["methods"].get(str(j))
    if not m:
        return "?"
    printed = m[2] if len(m) > 2 else ""
    return unsquash_name(printed) or m[0]


def _method(rec: dict, c) -> str:
    """The catalog method a cell belongs to, ignoring its printed variant."""
    i, k = c
    j = i if rec["axis"] == "rows" else k
    m = rec["methods"].get(j) or rec["methods"].get(str(j))
    return m[0] if m else f"?{j}"


def emphasis(rec: dict) -> tuple[dict, list[str]]:
    """The marks to draw, and footnotes wherever the PAPER marked otherwise.

    WE ALWAYS RANK. Bold is the best value and underline the next, within each
    measurement, ties included, whether or not the paper marks anything: one
    consistent reading across every table, where the papers' own conventions
    differ.

    THE PAPER'S MARKS ARE CHECKED, NOT DRAWN, and every mark that differs from
    ours is footnoted, saying what the original table did. That includes a
    paper counting its own variants as one method -- DiffuNovo underlines
    pi-HelixNovo, its best competitor, rather than its own other variant --
    which was first left unfootnoted as a legitimate convention; the reviewer
    asked for it to be recorded, because a reader comparing the picture with
    the grid otherwise has no way to tell why they differ. It also covers a
    plain mistake: CrossNovo's Table 1 bolds both pi-PrimeNovo (0.697) and
    InstaNovo (0.732) for peptide recall on Tomato.

    Three things are NOT differences: an unmarked measurement; a measurement
    holding a single value, which has nothing to rank; and the absence of
    underlines in a table that never underlines anything. A tie the paper marks
    IN FULL is not a difference either -- but a tie it marks only IN PART is,
    and the footnote names the tied cell we add.
    """
    rk = ranking(rec)
    pr = printed_emphasis(rec)
    if not pr:
        return rk, []
    uses_underline = any(v == "second" for v in pr.values())
    notes = []
    for key, cells in sorted(_measurements(rec).items()):
        bolds = {c for c in cells if pr.get(c) == "best"}
        unders = {c for c in cells if pr.get(c) == "second"}
        if not bolds and not unders:
            continue                                   # unmarked: not an error
        # ONE VALUE IS NOTHING TO RANK, so bolding it cannot be wrong. LIPNovo's
        # Table 3 'Mean' group has its baseline row cut off at the block's edge
        # and its Table 4 has no GraphNovo AUC ('-'), and both drew a footnote
        # saying the paper "bolded LIPNovo" -- true, and correct.
        if sum(1 for c in cells if _value(rec, c) is not None) < 2:
            # BUT A LONE VALUE THE PAPER BOLDED IS WORTH SAYING, where the
            # others are EXPLICITLY not run: LIPNovo's Table 4 bolds its own
            # AUC (0.707) and prints '-' for GraphNovo's. That is a lone value
            # on the page, not one the parse lost -- which is the case the rule
            # above exists for, and why the not-run marker is required: the
            # Mean group of its Table 3 is one value only because the block
            # ends before its baseline row.
            if bolds and _absent_peers(rec, key):
                notes.append(f"{' / '.join(x for x in (key[2], key[0], key[1]) if x and x != '?')}"
                             f": the original table bolded "
                             f"{', '.join(_who(rec, c) + ' (' + rec['body'][c[0]]['cells'][c[1]] + ')' for c in sorted(bolds))}"
                             f", the only value, since "
                             f"{', '.join(_absent_peers(rec, key))} "
                             f"{'was' if len(_absent_peers(rec, key)) == 1 else 'were'} not run; "
                             f"with nothing to rank it is not marked here.")
            continue
        best = {c for c in cells if rk.get(c) == "best"}
        second = {c for c in cells if rk.get(c) == "second"}
        bold_off = bool(bolds) and not bolds <= best
        under_off = uses_underline and bool(unders) and not unders <= second
        # A TIE MARKED ONLY IN PART is a difference too, of a milder kind: the
        # paper is right about the cells it marked and silent about the one it
        # tied with. LIPNovo's Table 1 underlines pi-HelixNovo at 0.765 for
        # amino-acid precision and not pi-HelixNovo-dagger, also at 0.765; we
        # underline both, and the footnote says which one we added.
        bold_part = bool(bolds) and not bold_off and bolds < best
        under_part = (uses_underline and bool(unders) and not under_off
                      and unders < second)
        if not (bold_off or under_off or bold_part or under_part):
            continue
        cell = lambda c: f"{_who(rec, c)} ({rec['body'][c[0]]['cells'][c[1]]})"
        name = " / ".join(x for x in (key[2], key[0], key[1]) if x and x != "?")
        said, tied = [], []
        if bold_off:
            said.append("bolded " + ", ".join(cell(c) for c in sorted(bolds)))
        if under_off:
            said.append("underlined " + ", ".join(cell(c) for c in sorted(unders)))
        if bold_part:
            tied.append(f"only bolded {', '.join(cell(c) for c in sorted(bolds))}; "
                        f"{', '.join(cell(c) for c in sorted(best - bolds))} ties "
                        f"with it and is bolded here too")
        if under_part:
            tied.append(f"only underlined {', '.join(cell(c) for c in sorted(unders))}; "
                        f"{', '.join(cell(c) for c in sorted(second - unders))} ties "
                        f"with it and is underlined here too")
        notes.append(f"{name}: the original table "
                     + "; and ".join([" and ".join(said)] * bool(said) + tied) + ".")
    return rk, notes


def grid_html(rec: dict) -> str:
    """The parse, as a table laid out the way the paper lays it out."""
    ncol = len(rec["col_head"])
    axis, maxis = rec["axis"], rec["metric_axis"]
    rank, _notes = emphasis(rec)
    out = ["<table class='grid'>"]

    def is_delta(i) -> bool:
        """A row with numbers but no method: a 'vs X' difference row.

        TSARseqNovo's Table 1 prints its own score and then the improvement
        over each baseline in percentage points. Those are not measurements and
        nothing records them, but they are on the page, so they are shown --
        dimmed and labelled -- rather than left as a blank row that reads like
        a parsing failure.
        """
        return (bool(rec["body"][i]["cells"]) and not rec["methods"].get(i)
                and bool(re.match(r"(?i)\s*(vs\.?|versus|\u0394)", rec["body"][i]["label"] or "")))

    def is_bare(i) -> bool:
        """A row with numbers and NO label: dropped by the miner and counted.

        Not a difference row, which is what it used to be captioned as:
        LIPNovo's Table 3 has one row whose label sits on a line of its own
        above the block, so it arrives bare and records nothing.
        """
        return (bool(rec["body"][i]["cells"]) and not rec["methods"].get(i)
                and not (rec["body"][i]["label"] or "").strip())

    def cell(i, k):
        v = rec["body"][i]["cells"].get(k, "")
        if axis == "rows" and (is_delta(i) or is_bare(i)):
            return f"<td class='dim'>{html.escape(v)}</td>" if v else "<td></td>"
        if not v:
            # A CELL THE PAPER MARKS AS NOT RUN. Printing nothing made the row
            # look like a parsing failure, and in a dataset-split part the row
            # used to vanish entirely: RefineNovo's Table 6 lists InstaNovo and
            # PrimeNovo-CV, neither run on seven-species.
            gone = (rec["body"][i].get("absent") or {}).get(k) \
                or (rec["body"][i].get("absent") or {}).get(str(k))
            if gone:
                return f"<td class='dim' title='not run'>{html.escape(gone)}</td>"
        cls = [c for c in (rank.get((i, k)),) if c]
        return (f"<td class='{' '.join(cls)}'>{html.escape(v)}</td>" if cls
                else f"<td>{html.escape(v)}</td>")

    def mcell(j):
        # A row with no metric is a difference row ('vs CasaNovo'), which
        # records nothing and so has no metric to show. Indexing it crashed the
        # page write AFTER every paper had been parsed, which left the page
        # stuck on its progress banner.
        m = rec["metrics"].get(j) if isinstance(rec["metrics"], dict) else None
        lv = rec["levels"].get(j) if isinstance(rec["levels"], dict) else None
        if m is None:
            return ""
        return f"{html.escape(m)}<br><small>{html.escape(lv or '')}</small>"

    def mname(j):
        """The label AS PRINTED, with the resolved catalog name beneath it.

        Reconstructing it from name plus version produced 'DiffuNovo vLogits'
        where the paper writes 'DiffuNovo (Logits)'. The printed form is what a
        reviewer compares against the picture, so it leads; the resolved name
        is what the row would be filed under, so it is shown too when the two
        differ.
        """
        m = rec["methods"].get(j)
        if not m:
            return "&mdash;"
        name, ver, printed = (m + ("",))[:3] if len(m) < 3 else m
        shown = unsquash_name(printed) or (f"{name} {ver}" if ver else name)
        out = html.escape(shown)
        if html.escape(name) != out:
            out += f"<br><small class='dim'>{html.escape(name)}"
            out += f" &middot; {html.escape(ver)}</small>" if ver else "</small>"
        return out

    if axis == "columns":
        out.append("<tr><th></th>" + "".join(
            f"<th>{mname(k)}</th>" for k in range(ncol)) + "</tr>")
        if maxis == "columns":
            # The metric belongs to the column, so it gets a row of its own.
            out.append("<tr><th class='dim'>metric</th>" + "".join(
                f"<td class='dim'>{mcell(k)}</td>" for k in range(ncol)) + "</tr>")
        for i, r in enumerate(rec["body"]):
            # WHEN THE METRIC RUNS DOWN THE ROWS, a single metric row printed
            # row 0's metric under every column, which reads as though the
            # whole table were peptide accuracy. RT-GCTnovo's TABLE I is
            # peptide accuracy on one row and amino-acid accuracy on the next,
            # so the metric belongs beside the ROW.
            label = html.escape(unsquash(r["label"]) or "-")
            if maxis == "rows":
                label += f"<br><small class='dim'>{mcell(i)}</small>"
            out.append(f"<tr><th>{label}</th>"
                       + "".join(cell(i, k) for k in range(ncol)) + "</tr>")
    else:
        # THE ROW GROUP GETS ITS OWN COLUMN, as in the original. LIPNovo's
        # Table 3 prints the species in a first column, once over each
        # LIPNovo/Baseline pair; the parse carried it as the rows' subset, which
        # is what the ranking groups by, and the grid did not show it, so the
        # picture had a column the grid lacked. It is printed once per group,
        # spanning the group's rows.
        rs = {int(k): v for k, v in (rec.get("row_subsets") or {}).items()}
        group_col = bool(rs)
        span: dict[int, int] = {}
        if group_col:
            i = 0
            n = len(rec["body"])
            while i < n:
                j = i
                while j + 1 < n and rs.get(j + 1, "") == rs.get(i, ""):
                    j += 1
                span[i] = j - i + 1
                i = j + 1
        # Named from the stub's own GROUP word, not its first word: LIPNovo's
        # stub picked up a stray 'Baseline-dagger' from the row above the block,
        # and the column was headed with it.
        gw = re.search(r"(?i)\b(species|dataset|datasets|enzyme|organism|group|fold)\b",
                       " ".join([rec.get("stub") or "", rec.get("header_raw") or ""]))
        gname = gw.group(1).capitalize() if gw else "group"
        out.append("<tr>"
                   + (f"<th>{html.escape(gname)}</th>" if group_col else "")
                   + "<th>method</th>"
                   + ("<th class='dim'>metric</th>" if maxis == "rows" else "")
                   + "".join(
                       # An unreadable header ('?', from a header row the text
                       # layer shredded) shows its METRIC instead, which is
                       # what the column holds.
                       # ...and where the ROW GROUP is the subset, a column
                       # "subset" can only be a stray header word -- LIPNovo's
                       # Table 5 puts 'Baseline' over Table 3's last column --
                       # so the metric is shown alone.
                       (f"<th>{mcell(k)}</th>" if maxis == "columns"
                        and (group_col
                             or (rec['subsets'].get(k) or rec['col_head'][k]) in ("?", ""))
                        else f"<th>{html.escape(rec['subsets'].get(k) or rec['col_head'][k])}"
                             + (f"<br><small class='dim'>{mcell(k)}</small>"
                                if maxis == "columns" else "")
                             + "</th>") for k in range(ncol)) + "</tr>")
        for i, r in enumerate(rec["body"]):
            label = (mname(i) if rec["methods"].get(i)
                     else html.escape(unsquash_name(r["label"]) or "-")
                     + ("<br><small class='dim'>difference, not recorded"
                        "</small>" if is_delta(i)
                        else "<br><small class='dim'>no label on the page, not "
                             "recorded</small>" if is_bare(i) else ""))
            grp = ""
            if group_col and i in span:
                grp = (f"<th rowspan='{span[i]}' style='vertical-align:middle'>"
                       f"{html.escape(unsquash(rs.get(i, '')))}</th>")
            out.append(f"<tr>{grp}<th>{label}</th>"
                       + (f"<td class='dim'>{mcell(i)}</td>" if maxis == "rows" else "")
                       + "".join(cell(i, k) for k in range(ncol)) + "</tr>")
    out.append("</table>")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated publication ids")
    ap.add_argument("--rejected-only", action="store_true")
    ap.add_argument("--accepted-only", action="store_true")
    ap.add_argument("--recrop", action="store_true",
                    help="re-render every crop, ignoring the cache")
    ap.add_argument("--rewrite", action="store_true",
                    help="rebuild the page from the stored items, parsing nothing "
                         "(for a change to how the page is drawn, or a sign-off)")
    ap.add_argument("--approve", help="comma-separated ids: the parse is correct")
    ap.add_argument("--dismiss", help="comma-separated ids: the REFUSAL is correct")
    ap.add_argument("--unapprove", help="comma-separated ids to un-mark either way")
    ap.add_argument("--unapprove-all", action="store_true",
                    help="clear every sign-off, to review the set again")
    ap.add_argument("--keep-crops", action="store_true",
                    help="reuse the PNGs already rendered, for an HTML-only change")
    args = ap.parse_args()

    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        import pdfplumber
    except ModuleNotFoundError:
        print("needs pdfplumber: uv run --with pdfplumber python3 "
              "review_comparisons.py", file=sys.stderr)
        return 2

    import sqlite3
    con = sqlite3.connect(B.DB)
    con.row_factory = sqlite3.Row
    pubs = bpl.load_publications(con)
    cov = bpl.coverage(pubs, B.LIBRARY)
    rx = B.locator(con)
    index = B.algorithm_index(con)
    want = {int(i) for i in args.ids.split(",")} if args.ids else None

    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    KNOWN_NAMES.update(
        _norm(n) for row in con.execute(
            "SELECT name, COALESCE(aliases,'') FROM algorithm")
        for n in [row[0]] + [a.strip() for a in row[1].split(",") if a.strip()]
        if n)
    approved: dict = {}
    if APPROVED.exists():
        try:
            approved = json.loads(APPROVED.read_text())
        except Exception:
            print(f"  could not read {APPROVED.name}; starting empty")
    today = __import__("datetime").date.today().isoformat()
    for tid in (args.approve or "").replace(" ", "").split(","):
        if tid:
            approved.setdefault(tid, {}).update({"state": "approved", "on": today})
    for tid in (args.dismiss or "").replace(" ", "").split(","):
        if tid:
            approved.setdefault(tid, {}).update({"state": "dismissed", "on": today})
    for tid in (args.unapprove or "").replace(" ", "").split(","):
        approved.pop(tid, None)
    if args.unapprove_all:
        print(f"  clearing {len(approved)} sign-off(s)")
        approved = {}
    # CROPS ARE REPLACED IN PLACE, NOT DELETED UP FRONT. Deleting them first
    # left the EXISTING page pointing at files that no longer existed for the
    # whole of a re-render, which takes minutes: open it in that window and
    # every screenshot is a broken image. Each crop is overwritten as it is
    # made, and anything no longer referenced is removed at the end.

    rows = con.execute("""
        SELECT DISTINCT p.id, p.title, p.publication_date FROM publication p
          JOIN publication_algorithm pa ON pa.publication_id = p.id AND pa.role='describes'
          JOIN algorithm a ON a.id = pa.algorithm_id
         WHERE a.kind = 'algorithm' ORDER BY p.id""").fetchall()

    # A CROP IS RE-RENDERED ONLY IF IT MOVED. Every pass used to run pdftoppm
    # over every page of every paper, which is almost all of the several
    # minutes a re-render costs, and almost all of it is wasted: a change to
    # one caption or one guard leaves every other table's picture identical.
    # Each crop is keyed by the source file, its mtime, the page and the bbox,
    # the key is recorded in the manifest, and a crop whose key is unchanged
    # and whose file is still on disk is left alone.
    prev_crops: dict = {}
    if MANIFEST.exists():
        try:
            prev_crops = json.loads(MANIFEST.read_text())
        except Exception:
            prev_crops = {}
    reused = rendered = 0

    items: list[dict] = []
    tally: collections.Counter = collections.Counter()
    import time
    # --rewrite PARSES NOTHING. A change to how the page is drawn -- or a
    # sign-off -- needs no PDF opened, and the stored items carry everything
    # the page shows, so the page is rebuilt from them in seconds.
    if args.rewrite:
        if not ITEMS.exists():
            print(f"  --rewrite needs {ITEMS.name}, written by a full run; run one first")
            return 1
        items = [_restore(it) for it in json.loads(ITEMS.read_text())]
        rows = []
    todo = [pub for pub in rows
            if (not want or pub["id"] in want) and (cov.get(pub["id"]) or [])]
    started, done = time.time(), 0
    if not args.rewrite:
        progress_banner(0, len(todo), started, "the library")
    for pub in rows:
        if want and pub["id"] not in want:
            continue
        paths = cov.get(pub["id"]) or []
        if not paths:
            continue
        done += 1
        took = time.time() - started
        eta = took / (done - 1) * (len(todo) - done + 1) if done > 1 else 0
        print(f"  [{done:>3}/{len(todo)}] {int(took)}s elapsed, "
              f"~{int(eta)}s left  p{pub['id']}", flush=True)
        progress_banner(done - 1, len(todo), started, f"p{pub['id']}")
        path = paths[0]
        try:
            pdf = pdfplumber.open(path)
        except Exception:
            continue
        with pdf:
            pages_text = [(pg.extract_text() or "") for pg in pdf.pages]
            cands = B.candidate_pages(pages_text, rx)
            if not cands:
                continue
            whole = "\n".join(pages_text)
            vocab = B.paper_vocabulary(whole, con)
            subject = con.execute("""
                SELECT a.id, a.name FROM algorithm a
                  JOIN publication_algorithm pa ON pa.algorithm_id = a.id
                 WHERE pa.publication_id = ? AND pa.role='describes'
                 ORDER BY a.id LIMIT 1""", (pub["id"],)).fetchone()
            for pno in cands:
                page = pdf.pages[pno]
                near = "\n".join(pages_text[max(0, pno - 1):pno + 2])
                tables, vetoed, _ = B.extract(page)
                found: list[dict] = []
                for v in vetoed:
                    found.append({**v, "verdict": "rejected"})
                for tb in tables:
                    collected: list[dict] = []
                    base = {c: "" for c in B.AUDIT_COLUMNS}
                    base.update({"publication_id": pub["id"], "pdf_page": pno + 1})
                    try:
                        B.emit(con, base, tb, vocab, index, subject, near, whole,
                               [], collections.Counter(), False, collect=collected)
                    except B.Reject as exc:
                        collected.append({"verdict": "rejected", "reason": str(exc),
                                          "table_label": tb["table_label"],
                                          "caption": tb["caption"],
                                          "bbox": tb.get("bbox"),
                                          "page": tb.get("page")})
                    found.extend(collected)
                for n, rec in enumerate(found):
                    if args.rejected_only and rec["verdict"] != "rejected":
                        continue
                    if args.accepted_only and rec["verdict"] != "accepted":
                        continue
                    # A STABLE IDENTIFIER PER TABLE, so a person can say which
                    # one they mean. It is the crop's own stem, which already
                    # encodes publication, page and the block's order on that
                    # page, and it is reproduced as the HTML anchor so the chip
                    # links to itself and the URL can be copied.
                    tid = f"p{pub['id']}-pg{pno + 1}-{n}"
                    name = f"{tid}.png"
                    dest = OUT / name
                    bb = rec.get("bbox") or []
                    crop_key = "|".join([path.name, str(rec.get("page") or (pno + 1)),
                                         f"{path.stat().st_mtime_ns}",
                                         ",".join(f"{x:.1f}" for x in bb)])
                    was = prev_crops.get(tid) or {}
                    # A manifest written before this cache existed has no key.
                    # Its crops were made by the run that wrote it, so they are
                    # current and are trusted once; from then on the key
                    # decides. --recrop overrides either way.
                    fresh = dest.exists() and not args.recrop and (
                        was.get("crop_key") == crop_key
                        or ("crop_key" not in was and tid in prev_crops))
                    if fresh:
                        ok, reused = True, reused + 1
                    elif args.keep_crops and dest.exists():
                        ok, reused = True, reused + 1
                    else:
                        ok = render(path, rec.get("page") or (pno + 1),
                                    rec.get("bbox"), dest) or dest.exists()
                        rendered += 1
                    rec["crop_key"] = crop_key
                    # A link straight to the PDF this crop came from, at the
                    # page it came from. Browsers' built-in viewers honour
                    # '#page=N', and the path needs quoting because the library
                    # folder has spaces in its name.
                    rec.update({"pdf_url": "file://" + urllib.parse.quote(str(path)),
                                "pdf_name": path.name,
                                "tid": tid, "pub": pub["id"], "title": pub["title"],
                                "year": str(pub["publication_date"] or "")[:4],
                                "img": name if ok else None,
                                "pdf_page": rec.get("page") or (pno + 1)})
                    rec["base_verdict"] = rec["verdict"]
                    seen_before = approved.get(tid)
                    if seen_before:
                        # An entry with no state predates the dismissed state
                        # and meant approved.
                        # NOT named `want`: that is the publication-id filter
                        # in this same function, and shadowing it made every
                        # paper fail an `int in str` test.
                        state = seen_before.get("state", "approved")
                        if state == "approved" and rec["verdict"] == "accepted":
                            rec["verdict"] = "approved"
                        elif state == "dismissed" and rec["verdict"] == "rejected":
                            rec["verdict"] = "dismissed"
                    items.append(rec)
                    tally[rec["verdict"]] += 1
        print(f"  p{pub['id']:<4} {len([i for i in items if i['pub']==pub['id']]):>3} "
              f"table(s)  {pub['title'][:52]}", flush=True)

    for f in CACHE.glob("*.png"):
        f.unlink()
    # A RESTRICTED RUN TOUCHES ONLY ITS OWN PAPERS. `--ids 18` used to delete
    # every crop it had not just made and rewrite the manifest with its own
    # entries alone, so a quick look at one paper threw away the pictures and
    # the cache keys of all the others, and the next full run had to redraw
    # the lot. Unreferenced crops are pruned only by a run over everything.
    if not args.keep_crops and not want:
        keep = {it["img"] for it in items if it.get("img")}
        for old in OUT.glob("*.png"):
            if old.name not in keep:
                old.unlink()
    # Record the fingerprint of everything approved, so a later run can tell
    # whether the id still points at the same table.
    for it in items:
        if it["tid"] in approved:
            approved[it["tid"]].update(
                {"label": it.get("table_label") or "", "pub": it["pub"],
                 "cells": it.get("n_cells") or len(it.get("body") or [])})
    APPROVED.write_text(json.dumps(approved, indent=1, sort_keys=True))
    fresh = {it["tid"]: {"publication_id": it["pub"], "pdf_page": it["pdf_page"],
                         "table_label": it.get("table_label") or "",
                         "verdict": it["verdict"],
                         "crop_key": it.get("crop_key") or "",
                         "values": [c for r in (it.get("body") or [])
                                    for c in r["cells"].values()]}
             for it in items}
    if want:
        # Merge: keep every other paper's entry exactly as it was.
        kept = {t: v for t, v in prev_crops.items()
                if v.get("publication_id") not in want}
        fresh = {**kept, **fresh}
    MANIFEST.write_text(json.dumps(fresh, indent=1, sort_keys=True))
    # A RESTRICTED RUN MERGES INTO THE WHOLE PAGE. Parsing every PDF is
    # almost all of a render's minutes, and almost all of it is wasted when one
    # paper changed: so every run stores its items, and an `--ids` run
    # re-parses only those papers, replaces their items and writes the full
    # page from the rest as stored. Sign-offs are re-applied to every item from
    # approved.json at write time, so an approval given for a paper outside
    # --ids still takes effect, and a revoked one reverts.
    if want and ITEMS.exists():
        try:
            stored = [_restore(it) for it in json.loads(ITEMS.read_text())]
        except Exception as exc:
            print(f"  could not read {ITEMS.name} ({exc}); writing a partial page")
            stored = []
        if stored:
            items = sorted([it for it in stored if it["pub"] not in want] + items,
                           key=lambda it: (it["pub"], it["pdf_page"],
                                           int(it["tid"].rsplit("-", 1)[1])))
    for it in items:
        base = it.get("base_verdict") or it["verdict"]
        # An entry with no state predates the dismissed state and meant
        # approved, exactly as in the build loop above.
        state = (approved[it["tid"]].get("state", "approved")
                 if it["tid"] in approved else None)
        it["verdict"] = ("approved" if state == "approved" and base == "accepted"
                         else "dismissed" if state == "dismissed" and base == "rejected"
                         else base)
    tally = collections.Counter(it["verdict"] for it in items)
    try:
        ITEMS.write_text(json.dumps(items, default=list))
    except (OSError, TypeError) as exc:
        print(f"  could not store items ({exc}); the next --ids run will be partial")
    write_html(items, tally, approved)
    print(f"\n  crops: {reused} reused, {rendered} rendered")
    print(f"  {tally['accepted']} accepted, {tally['approved']} approved, "
          f"{tally['rejected']} rejected, {tally['dismissed']} dismissed")
    stale = [t for t in approved if t not in {i['tid'] for i in items}]
    if stale:
        print(f"  {len(stale)} approved id(s) no longer present: {', '.join(sorted(stale))}")
        print("  the block order on a page can shift as the miner changes; "
              "re-approve under the new id")
    print(f"  {OUT / 'index.html'}")
    return 0


# A RENDER TAKES MINUTES AND THE PAGE GAVE NO SIGN OF IT. Opening the page
# mid-render showed the previous version with nothing to say it was stale, so
# a reviewer could sign off a table the run was about to change. While a run
# is in progress the existing page carries a banner with the count, the
# elapsed time and an estimate, and reloads itself every 15 s; the finished
# page is written without either, so the reloading stops by itself the moment
# the new page is ready.
_PREVIOUS_PAGE: str | None = None


def progress_banner(done: int, total: int, started: float, current: str) -> None:
    global _PREVIOUS_PAGE
    import time
    index = OUT / "index.html"
    if _PREVIOUS_PAGE is None:
        try:
            _PREVIOUS_PAGE = index.read_text(encoding="utf-8")
        except OSError:
            _PREVIOUS_PAGE = "<!doctype html><html><head><meta charset='utf-8'>" \
                             "</head><body><p>No previous render.</p></body></html>"
    took = time.time() - started
    eta = (took / done * (total - done)) if done else None
    left = (f"about {int(eta // 60)} min {int(eta % 60):02d} s left" if eta is not None
            else "estimating time left")
    pct = int(100 * done / total) if total else 0
    banner = (
        "<div style='position:sticky;top:0;z-index:99;background:#1d4ed8;color:#fff;"
        "padding:10px 16px;font:14px/1.4 system-ui,sans-serif'>"
        f"<b>Re-rendering</b> &mdash; {done} of {total} papers ({pct}%), "
        f"{int(took // 60)} min {int(took % 60):02d} s elapsed, {left}. "
        f"Now reading {html.escape(current)}. "
        "This is the PREVIOUS version of the page; it reloads every 15 s and "
        "switches to the new one when the render finishes."
        f"<div style='margin-top:6px;height:6px;background:#93c5fd;border-radius:3px'>"
        f"<div style='width:{pct}%;height:6px;background:#fff;border-radius:3px'></div></div>"
        "</div>")
    page = _PREVIOUS_PAGE
    refresh = "<meta http-equiv='refresh' content='15'>"
    page = page.replace("<head>", "<head>" + refresh, 1) if "<head>" in page else refresh + page
    i = page.find("<body")
    if i >= 0:
        j = page.find(">", i) + 1
        page = page[:j] + banner + page[j:]
    else:
        page = banner + page
    try:
        index.write_text(page, encoding="utf-8")
    except OSError:
        pass


def write_html(items: list[dict], tally, approved: dict | None = None) -> None:
    by_pub: dict = {}
    for it in items:
        by_pub.setdefault((it["pub"], it["title"], it["year"]), []).append(it)
    H = ["""<!doctype html><html><head><meta charset="utf-8">
<title>Comparison-table review</title><style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--line:#d8d8d2;--ok:#1a7f4b;--no:#a8321e;--dim:#6b6b63}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
 padding:14px 24px;z-index:5}
h1{margin:0;font-size:17px}
.sub{color:var(--dim);font-size:13px;margin-top:3px}
main{padding:8px 24px 60px}
h2{font-size:15px;margin:34px 0 4px;padding-top:10px;border-top:1px solid var(--line)}
h2 small{color:var(--dim);font-weight:400}
.item{margin:18px 0 26px}
.hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}
.tag{font:600 11px/1.6 ui-monospace,monospace;padding:1px 7px;border-radius:9px;color:#fff}
.tag.accepted{background:var(--ok)} .tag.rejected{background:var(--no)}
.tag.approved{background:#1d4ed8} .tag.dismissed{background:#6b6b63}
details.appr.dismissed{border-color:#cfcfc6;background:#f6f6f3}
details.appr.dismissed>summary{color:#5d5d55}
details.appr{border:1px dashed #bcd0a8;background:#f4f9f0;border-radius:6px;
 padding:8px 12px;margin:14px 0}
details.appr>summary{cursor:pointer;font-size:13px;color:#3f6b2c}
.tid{font:600 11.5px/1.6 ui-monospace,monospace;color:#2a4d8f;background:#eaf0fb;
 border:1px solid #cfdcf2;padding:1px 7px;border-radius:9px;cursor:copy;
 -webkit-user-select:all;user-select:all}
.tid:hover{background:#dde8fa}
.tid.copied{background:#d8f0dd;border-color:#9fd3ad;color:#14612f}
.anch{color:#9db4da;text-decoration:none;font:600 12px/1 ui-monospace,monospace}
.anch:hover{color:#2a4d8f}
.pdf{font-size:12px;color:#2a4d8f;text-decoration:none;border-bottom:1px dotted #9db4da}
.pdf:hover{border-bottom-style:solid}
.item:target{outline:3px solid #f0c000;outline-offset:6px;border-radius:4px}
.toc{columns:3;font:12.5px/1.8 ui-monospace,monospace;margin:10px 0 0}
.toc a{color:#2a4d8f;text-decoration:none} .toc a:hover{text-decoration:underline}
.toc .r{color:var(--no)} .toc .a{color:var(--ok)} .toc .ok{color:#1d4ed8;font-weight:700} .toc .dm{color:#8a8a80}
.lbl{font-weight:600}
.meta{color:var(--dim);font-size:12.5px}
.cap{color:var(--dim);font-size:12.5px;margin:2px 0 8px;max-width:150ch}
.caplabel{font:600 10px/1.6 ui-monospace,monospace;letter-spacing:.06em;
 text-transform:uppercase;color:#8a8a80;border:1px solid var(--line);
 border-radius:4px;padding:0 4px;margin-right:5px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
@media(max-width:1100px){.pair{grid-template-columns:1fr}}
.panel{border:1px solid var(--line);background:#fff;border-radius:6px;padding:10px;overflow:auto}
.panel h3{margin:0 0 8px;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--dim)}
.panel img{max-width:100%;display:block}
table.grid{border-collapse:collapse;font:12px/1.4 ui-monospace,monospace}
table.grid th,table.grid td{border:1px solid var(--line);padding:3px 6px;text-align:right;white-space:nowrap}
table.grid th{background:#f4f4f0;text-align:left;font-weight:600}
table.grid .dim{color:var(--dim);font-weight:400;background:#fafaf7}
table.grid td.best{font-weight:700;background:#eef7ee}
table.grid td.second{text-decoration:underline;background:#f7f7ef}
.why{color:var(--no);font:12.5px/1.5 ui-monospace,monospace;white-space:pre-wrap}
.nope{color:var(--dim);font-style:italic}
</style></head><body>
<script>
// Open a PDF in a NEW TAB. target="_blank" alone is not enough: a browser
// handing a file:// PDF to its built-in viewer may ignore it and navigate the
// current tab, losing the reviewer's place on a long page. window.open is
// explicit about it, and the default link is left to do the work if the call
// is blocked, so the link never stops working.
document.addEventListener('click', function (e) {
  var a = e.target.closest ? e.target.closest('a.pdf') : null;
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
  var w = window.open(a.href, '_blank', 'noopener');
  if (w) { e.preventDefault(); try { w.opener = null; } catch (err) {} }
});

// Copy on click where the browser allows it. A file:// page is not always a
// secure context, so navigator.clipboard can be absent or refuse; the
// execCommand path and, failing both, the CSS selection, cover that.
document.addEventListener('click', function (e) {
  var el = e.target.closest ? e.target.closest('.tid') : null;
  if (!el) return;
  var id = el.dataset.id || el.textContent;
  var done = function () {
    el.classList.add('copied');
    setTimeout(function () { el.classList.remove('copied'); }, 900);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(id).then(done, function () { legacy(); });
  } else { legacy(); }
  function legacy() {
    try {
      var t = document.createElement('textarea');
      t.value = id; t.setAttribute('readonly', '');
      t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.appendChild(t); t.select();
      document.execCommand('copy'); document.body.removeChild(t); done();
    } catch (err) { /* the CSS selection is the fallback */ }
  }
});
</script>"""]
    H.append(f"<header><h1>Comparison-table review</h1><div class='sub'>"
             f"{tally['accepted']} accepted and {tally['rejected']} refused, each beside the "
             f"printed table it came from. Local file, not published.</div></header><main>")
    H.append("<details class='panel' style='margin-bottom:18px'>"
             "<summary><strong>Index of every table id</strong> "
             "&mdash; green accepted, red refused</summary><div class='toc'>")
    for it in sorted(items, key=lambda i: (i["pub"], i["pdf_page"], i["tid"])):
        cls = {"accepted": "a", "approved": "ok", "dismissed": "dm"}.get(
            it["verdict"], "r")
        H.append(f"<a class='{cls}' href='#{it['tid']}'>{it['tid']}</a> "
                 f"<span class='meta'>{html.escape((it.get('table_label') or '?')[:12])}</span><br>")
    H.append("</div></details>")
    for (pid, title, year), group in sorted(by_pub.items()):
        H.append(f"<h2>p{pid} &middot; {html.escape(title)} <small>({year})</small></h2>")
        group.sort(key=lambda i: (i["pdf_page"], i["verdict"] != "accepted"))
        # APPROVED TABLES COLLAPSE. They have been read and signed off, so they
        # are kept for reference and folded away rather than occupying the page
        # a reviewer is working down.
        for state, word in (("approved", "approved"),
                            ("dismissed", "confirmed as correctly refused")):
            done = [i for i in group if i["verdict"] == state]
            group = [i for i in group if i["verdict"] != state]
            if done:
                H.append(f"<details class='appr {state}'><summary>{len(done)} "
                         f"{word} on this paper &mdash; click to show</summary>")
                for it in done:
                    H.append(item_html(it))
                H.append("</details>")
        for it in group:
            H.append(item_html(it))
    H.append("</main></body></html>")
    (OUT / "index.html").write_text("".join(H), encoding="utf-8")


def item_html(it: dict) -> str:
    """One table: its id, its badge, the crop, and the parse or the reason."""
    v = it["verdict"]
    H = [f"<div class='item' id='{it['tid']}'><div class='hd'>"
         # `user-select:all` means ONE CLICK selects the whole id, so Ctrl+C
         # works with no scripting at all. The click handler also puts it on
         # the clipboard where the browser allows it, which a file:// page
         # sometimes does not, hence the selection as the real mechanism and
         # the copy as a convenience.
         f"<span class='tid' data-id='{it['tid']}' "
         f"title='click to copy'>{it['tid']}</span>"
         f"<a class='anch' href='#{it['tid']}' title='link to this table'>#</a>"
         f"<span class='tag {v}'>{v}</span>"
         f"<span class='lbl'>{html.escape(it.get('table_label') or '?')}</span>"
         f"<span class='meta'>page {it['pdf_page']}</span>"
         + (f"<a class='pdf' href=\"{it['pdf_url']}#page={it['pdf_page']}\""
            f" target='_blank' rel='noopener noreferrer'"
            f" title='{html.escape(it['pdf_name'])}'>"
            f"open the PDF &#8599;</a>" if it.get("pdf_url") else "")]
    if v in ("accepted", "approved"):
        H.append(f"<span class='meta'>{html.escape(it.get('dataset') or '?')}"
                 + (f" &middot; version {it['dataset_version_id']}"
                    if it.get("dataset_version_id") else " &middot; version not stated")
                 + f" &middot; methods in {it['axis']}, metric in {it['metric_axis']}"
                 + f" &middot; unit {it.get('unit')}</span>")
    H.append("</div>")
    if it.get("caption"):
        # IN FULL, and labelled as the paper's own words. It was clipped at 400
        # characters, which cut the legend off exactly the captions that need
        # one -- the footnote explaining a marker is always at the end.
        H.append("<div class='cap'><span class='caplabel'>caption</span> "
                 + html.escape(it["caption"]) + "</div>")
    H.append("<div class='pair'><div class='panel'><h3>Printed in the paper</h3>")
    H.append(f"<img src='{it['img']}' alt='table crop'>" if it.get("img")
             else "<div class='nope'>no crop could be rendered</div>")
    H.append("</div><div class='panel'>")
    if v in ("accepted", "approved"):
        # SAY WHICH EMPHASIS IS ON SCREEN. Bold and underline mean two
        # different things depending on the table, and a reader comparing
        # against the picture needs to know which, or a legitimate difference
        # of convention reads as a wrong number.
        _m, wrong = emphasis(it)
        H.append("<h3>What the miner read</h3>" + grid_html(it))
        if it.get("footnote"):
            H.append("<div class='cap' style='margin-top:8px'>footnote &mdash; "
                     + html.escape(it["footnote"]) + "</div>")
        if it.get("design_note"):
            H.append("<div class='cap' style='margin-top:8px'><b>about this table"
                     "</b> &mdash; " + html.escape(it["design_note"]) + "</div>")
        for n in wrong:
            H.append("<div class='cap' style='margin-top:6px'>"
                     "<b>original emphasis</b> &mdash; " + html.escape(n) + "</div>")
        if it.get("basis"):
            b = ", ".join(f"{html.escape(k)}: {html.escape(x)}"
                          for k, x in it["basis"].items())
            H.append(f"<div class='cap' style='margin-top:8px'>basis &mdash; {b}</div>")
    else:
        H.append("<h3>Why it was refused</h3>"
                 f"<div class='why'>{html.escape(it.get('reason') or '')}</div>")
    H.append("</div></div></div>")
    return "".join(H)


if __name__ == "__main__":
    raise SystemExit(main())

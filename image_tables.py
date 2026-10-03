"""Tables that exist only as images: find them, and turn two agreeing readings
into the structure the miner already knows how to resolve.

`read_table_images.py` runs the two vision models; this module is everything
around them that needs no GPU:

  find_image_tables()  a table caption with a large text-free gap beside it,
                       where the gap holds images or vector drawings -- the
                       shape of InstaNovo's published Extended Data tables
  otsl_grid()          PaddleOCR-VL's OTSL output to a dense grid
  html_grid()          GLM-OCR's HTML to a dense grid (spans expanded)
  compare()            the two grids, cell by cell
  grid_table()         an AGREED grid to the miner's table dict, so emit()
                       applies every guard it applies to a text-layer table

A table read from an image is accepted only when the two models agree on every
cell, and it is marked as such all the way into the database.
"""
from __future__ import annotations

import html
import re

import build_paper_comparisons as B
from build_table_vlm import Grid

# How much empty height beside a caption makes a picture of a table. A caption
# and its first table row are a line or two apart; InstaNovo's Extended Data
# Table 2 leaves 170 pt between caption and footnote.
MIN_GAP = 60.0


def _objects_in(page, box) -> int:
    x0, top, x1, bottom = box
    n = 0
    for kind in ("images", "rects", "lines", "curves"):
        for o in getattr(page, kind, []) or []:
            if o["x1"] > x0 and o["x0"] < x1 and o["bottom"] > top and o["top"] < bottom:
                n += 10 if kind == "images" else 1
    return n


def find_image_tables(page, seen_labels: set[str]) -> list[dict]:
    """Captioned tables on this page whose body is not text.

    `seen_labels` are the labels the text-layer miner already produced for this
    page, so a table it parsed is never read a second time from its picture.
    """
    words = B.strip_line_numbers(page.extract_words(use_text_flow=False,
                                                    keep_blank_chars=False))
    if not words:
        return []
    rows = B.word_rows(words)
    out = []
    for li, x0, x1 in B.label_runs(rows):
        label, caption, cap_end = B.caption_text(rows, (li, x0, x1), [], None,
                                                 float(page.width))
        if not label or label.strip() in seen_labels:
            continue
        cap_end = max(cap_end, li)
        bottom_of = lambda i: max(w["bottom"] for w in rows[i])
        top_of = lambda i: min(w["top"] for w in rows[i])
        margin = max(20.0, x0 - 6)
        boxes = []
        if cap_end + 1 < len(rows):                     # table BELOW its caption
            boxes.append((margin, bottom_of(cap_end) + 2,
                          float(page.width) - margin, top_of(cap_end + 1) - 2))
        if li > 0:                                       # table ABOVE its caption
            boxes.append((margin, bottom_of(li - 1) + 2,
                          float(page.width) - margin, top_of(li) - 2))
        for box in boxes:
            if box[3] - box[1] >= MIN_GAP and _objects_in(page, box) >= 10:
                out.append({"table_label": label.strip(), "caption": caption,
                            "bbox": list(box), "page": page.page_number})
                break
    return out


def otsl_grid(text: str) -> list[list[str]]:
    """PaddleOCR-VL's table tokens to a dense grid.

    <fcel> opens a cell with text, <ecel> an empty one; <lcel>, <ucel> and
    <xcel> are a cell merged left, up, or both, and take that cell's text --
    the same expansion html_grid() gives a colspan or rowspan.
    """
    text = text.replace("</s>", "")
    rows: list[list[str]] = [[]]
    for tok, body in re.findall(r"<(fcel|ecel|lcel|ucel|xcel|nl)>([^<]*)", text):
        cur = rows[-1]
        if tok == "nl":
            rows.append([])
            continue
        if tok == "fcel":
            cur.append(body.strip())
        elif tok == "ecel":
            cur.append("")
        elif tok == "lcel":
            cur.append(cur[-1] if cur else "")
        else:                                          # ucel / xcel: from above
            above = rows[-2] if len(rows) >= 2 else []
            cur.append(above[len(cur)] if len(cur) < len(above) else "")
    return [r for r in rows if r]


def html_grid(text: str) -> list[list[str]]:
    g = Grid()
    g.feed(text)
    return [[html.unescape(c).strip() for c in r] for r in g.dense()]


def _norm(cell: str) -> str:
    return re.sub(r"\s+", "", cell.replace("−", "-").replace("±", "+-")).lower()


def compare(a: list[list[str]], b: list[list[str]]) -> list[tuple[int, int, str, str]]:
    """Every cell where the two readings differ, as (row, col, a, b).

    A different SHAPE is reported as one difference at (-1, -1): there is no
    sensible cell-by-cell comparison of two grids that do not line up.
    """
    if len(a) != len(b) or any(len(x) != len(y) for x, y in zip(a, b)):
        return [(-1, -1, f"{len(a)}x{max(map(len, a), default=0)}",
                 f"{len(b)}x{max(map(len, b), default=0)}")]
    return [(r, c, a[r][c], b[r][c]) for r in range(len(a)) for c in range(len(a[r]))
            if _norm(a[r][c]) != _norm(b[r][c])]


def grid_table(grid: list[list[str]], label: str, caption: str, bbox, page) -> dict:
    """An agreed grid as the dict extract() would have produced.

    Header rows are the leading rows with no number past the first column; the
    first column is the stub when no body row has a number there. Upper header
    rows become spanners, the last becomes each column's own header, which is
    exactly what the text-layer header reader produces.
    """
    def is_num(s: str) -> bool:
        return B.numeric(s) is not None
    h = 0
    while h < len(grid) and not any(is_num(c) for c in grid[h][1:]):
        h += 1
    head, body_rows = grid[:h], grid[h:]
    stub = 1 if body_rows and not any(is_num(r[0]) for r in body_rows if r) else 0
    ncol = max((len(r) for r in grid), default=0) - stub
    edges = [(100.0 + 60 * k, 160.0 + 60 * k) for k in range(ncol)]
    own, span = {}, {}
    for k in range(ncol):
        col = [r[stub + k] if stub + k < len(r) else "" for r in head]
        if col and col[-1]:
            own[k] = [col[-1]]
        ups = [t for t in col[:-1] if t]
        if ups:
            span[k] = ups
    # The same NOT-RECORDED rules extract() applies to a text table, which an
    # image table never passes through: a year, speed, error-rate or loss
    # column, and a count, BLEU or spread ('std') row. They stay in the printed
    # layer with their reason, exactly as for a text table.
    drop_col = re.compile(r"(?i)^years?$|\bspeed\b|spectra/s|throughput|latency"
                          r"|\btime\b|\(ms\)|\(s\)|error\s*rate|\bloss\b")
    nr_cols = []
    for k in range(ncol):
        if drop_col.search(" ".join(own.get(k, []))):
            nr_cols.append(k)
    keep = [k for k in range(ncol) if k not in nr_cols]
    body, nr_rows = [], []
    for ri, r in enumerate(body_rows):
        lab = r[0] if stub else ""
        if B.COUNT_ROW.search(lab) or B.UNRECORDED_METRIC_ROW.search(lab):
            nr_rows.append({"label": lab, "top": 10.0 * ri,
                            "why": "count" if B.COUNT_ROW.search(lab)
                                   else "metric outside the vocabulary",
                            "words": [(130.0 + 60 * k, r[stub + k])
                                      for k in range(ncol) if stub + k < len(r)]})
            body_rows[ri] = None
    not_recorded_cols = [{"x": 130.0 + 60 * k, "header": " ".join(own.get(k, [])),
                          "why": "error rate" if "error" in " ".join(own.get(k, [])).lower()
                                 else "not recorded",
                          "cells": {10.0 * ri: (r[stub + k] if stub + k < len(r) else "")
                                    for ri, r in enumerate(body_rows) if r is not None}}
                         for k in nr_cols]
    remap = {old: new for new, old in enumerate(keep)}
    own = {remap[k]: v for k, v in own.items() if k in remap}
    span = {remap[k]: v for k, v in span.items() if k in remap}
    edges = [edges[k] for k in keep]
    for ri, r in enumerate(body_rows):
        if r is None:
            continue
        cells, absent = {}, {}
        for k in keep:
            t = r[stub + k] if stub + k < len(r) else ""
            v = B.numeric(t)
            if v is not None:
                cells[remap[k]] = {"value": v[0], "stddev": v[1], "printed": t,
                                   "parts": [(v[0], "")], "bold": False,
                                   "underlined": False}
            elif t.strip().lower() in B.NOT_RUN:
                absent[remap[k]] = t.strip()
        body.append({"label": r[0] if stub else "", "cells": cells, "absent": absent,
                     "extra": [], "top": 10.0 * ri, "extra_top": None})
    return {"edges": edges, "own": own, "span": span,
            "stub": (head[-1][0] if head and stub else ""),
            "body": body, "dropped_rows": len(nr_rows), "not_recorded_rows": nr_rows,
            "not_recorded_cols": not_recorded_cols, "caption": caption,
            "table_label": label,
            "footnote": "", "header_raw": " ".join(c for r in head for c in r if c),
            "span_ambiguous": set(), "registry_label": label,
            "bbox": bbox, "page": page, "extraction": "image",
            "design_note": ("Read from an IMAGE: the page has no text layer for this "
                            "table, and GLM-OCR and PaddleOCR-VL, read independently, "
                            "agree on every cell.")}

#!/usr/bin/env python3
"""Check every mined text table against two independent vision-model readings.

The text-layer parse is the source of every stored number. This compares it,
ROW BY ROW and in column order, with what GLM-OCR and PaddleOCR-VL read off
the same crop (`read_table_images.py --crosscheck` fills the cache). A
position-free test -- "does this number appear anywhere in the model's
output" -- passes a value that landed in the wrong cell, so each parsed row is
paired with the model row of the same LABEL (or, failing that, the one sharing
the most numbers in order), and the two are ALIGNED as ordered sequences.

Aligned, not indexed. A parsed row is legitimately a SUBSEQUENCE of the
printed one: a not-recorded column (error rate, speed) is dropped, and a table
split by dataset keeps only its own columns while the crop shows all of them.
Comparing number i with number i reported every such table as wrong, with both
models "agreeing" on a different value -- a false CHECK. A longest common
subsequence keeps the column order, so a value in the wrong cell still fails,
and a parsed value with no partner is reported beside whatever the model read
in the gap it should have filled.

    python3 crosscheck_tables.py            # report, writes table_crosscheck.csv

Verdicts per table:

    AGREE       both models read every parsed number exactly
    ONE-READER  one model agrees everywhere and the other differs somewhere:
                a model's misreading, almost always
    CHECK       BOTH models read the same different value in some cell: the
                text-layer parse is the likely culprit, so a person should look
    DIFFER      the models disagree with the parse and with each other
    UNREAD      a model's output is missing or has no table

Report only: it never edits the parse or the database. A CHECK is a pointer to
a cell for a human, not a correction.
"""
from __future__ import annotations

import csv
import json
import pathlib
import re

import build_pdf_library as bpl
import image_tables as IT

CROPS = bpl.DEFAULT_DIR / "comparison-review"
CACHE = CROPS / "vlm-cache"
OUT = pathlib.Path(__file__).with_name("table_crosscheck.csv")
NUM = re.compile(r"\d*\.\d+|\d+")


def nums(text: str) -> list[str]:
    # Compared BY VALUE: GLM writes '0.7540' for a printed '0.754', and a
    # string comparison called that a misreading. Normalised, so equal values
    # compare equal while the order of the sequence still matters.
    out = []
    for n in NUM.findall((text or "").replace(",", "").replace("−", "-")):
        try:
            out.append(format(float(n), "g"))
        except ValueError:
            out.append(n)
    return out


def model_rows(reader: str, tid: str) -> list[tuple[str, list[str]]] | None:
    f = CACHE / f"{tid}.{reader}.txt"
    if not f.exists():
        return None
    text = f.read_text()
    grid = IT.html_grid(text) if reader == "glm" else IT.otsl_grid(text)
    # A row whose label cell is itself a number lost its label to a reading
    # slip; its first cell is data, not a label.
    return [("", [n for c in r for n in nums(c)]) if IT.B.numeric(r[0]) is not None
            else (r[0], [n for c in r[1:] for n in nums(c)]) for r in grid if r] or None


def label_key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def lcs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence of a and b, in order."""
    n, m = len(a), len(b)
    t = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            t[i][j] = t[i + 1][j + 1] + 1 if a[i] == b[j] else max(t[i + 1][j], t[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif t[i + 1][j] >= t[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def align(label: str, parsed: list[str], rows: list[tuple[str, list[str]]]) -> dict[int, tuple[bool, str]]:
    """For each parsed index: (matched, what the model read there).

    The model row is the one with the same label, else the one sharing the
    longest ordered run of numbers. An unmatched value is paired with the model
    number in its gap when the gap holds exactly as many numbers as the parse
    is missing there, which is the "same cell, different reading" case.
    """
    same = [r for r in rows if label_key(r[0]) and label_key(r[0]) == label_key(label)]
    pool = same or rows
    if not pool:
        return {i: (False, "") for i in range(len(parsed))}
    nums_ = max(pool, key=lambda r: len(lcs(parsed, r[1])))[1]
    # A label the model put on the wrong row (GLM shifts a whole label column
    # down by one on InstaNovo's supplementary tables) must not sink a row whose
    # numbers it read correctly, so a weak label match yields to the numbers.
    if same and 2 * len(lcs(parsed, nums_)) < len(parsed):
        nums_ = max(rows, key=lambda r: len(lcs(parsed, r[1])))[1]
    pairs = lcs(parsed, nums_)
    out = {i: (True, nums_[j]) for i, j in pairs}
    anchors = [(-1, -1)] + pairs + [(len(parsed), len(nums_))]
    for (i0, j0), (i1, j1) in zip(anchors, anchors[1:]):
        gap_p, gap_m = list(range(i0 + 1, i1)), nums_[j0 + 1:j1]
        for k, i in enumerate(gap_p):
            out[i] = (False, gap_m[k] if len(gap_m) == len(gap_p) else "")
    return out


def main() -> int:
    items = json.loads((CROPS / "items.json").read_text())
    rows, tally = [], {}
    for it in items:
        if it["verdict"] not in ("accepted", "approved") or \
                it.get("extraction", "text") != "text" or not it.get("body"):
            continue
        reads = {r: model_rows(r, it["tid"]) for r in ("glm", "paddle")}
        if any(v is None for v in reads.values()):
            verdict, cells = "UNREAD", []
        else:
            cells = []
            # Grid rows of role 'data' are the body rows, in order; a body row
            # whose results carry 'derived' was computed, not printed.
            grid = it.get("grid") or {}
            data_rows = [gi for gi, g in enumerate(grid.get("rows") or [])
                         if g.get("role") == "data"]
            derived_gi = {x["r"] for x in grid.get("results") or [] if x.get("derived")}
            derived_rows = {k for k, gi in enumerate(data_rows) if gi in derived_gi}
            for ri, r in enumerate(it["body"]):
                # A DERIVED cell (TSARseqNovo's Casanovo rows: its score
                # minus the printed improvement) is not on the page, so no
                # reader can confirm it; it is checked through the printed
                # cells it was computed from.
                if ri in derived_rows:
                    continue
                parsed = [n for c in r["cells"].values() for n in nums(c)]
                if not parsed:
                    continue
                g = align(r["label"], parsed, reads["glm"])
                p = align(r["label"], parsed, reads["paddle"])
                for i, val in enumerate(parsed):
                    (g_ok, gv), (p_ok, pv) = g[i], p[i]
                    if not (g_ok and p_ok):
                        cells.append((r["label"], i, val, gv, pv, g_ok, p_ok))
            if not cells:
                verdict = "AGREE"
            elif any(not g_ok and not p_ok and gv == pv != ""
                     for *_x, gv, pv, g_ok, p_ok in cells):
                verdict = "CHECK"
            elif all(g_ok or p_ok for *_x, g_ok, p_ok in cells):
                verdict = "ONE-READER"
            else:
                verdict = "DIFFER"
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append({"tid": it["tid"], "verdict": verdict, "n_differing": len(cells),
                     "cells": "; ".join(f"{l!s:.24} #{i}: parse {v} / glm {g} / paddle {p}"
                                        for l, i, v, g, p, *_ok in cells[:8])})
        if verdict not in ("AGREE", "UNREAD"):
            print(f"  {verdict:10} {it['tid']}: {rows[-1]['cells'][:160]}")
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["tid", "verdict", "n_differing", "cells"])
        w.writeheader()
        w.writerows(rows)
    print()
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>4}  {k}")
    print(f"\n  {len(rows)} table(s) -> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

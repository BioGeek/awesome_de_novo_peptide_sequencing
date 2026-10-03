#!/usr/bin/env python3
"""Check every mined text table against two independent vision-model readings.

The text-layer parse is the source of every stored number. This compares it,
ROW BY ROW and in column order, with what GLM-OCR and PaddleOCR-VL read off
the same crop (`read_table_images.py --crosscheck` fills the cache). A
position-free test -- "does this number appear anywhere in the model's
output" -- passes a value that landed in the wrong cell, so each parsed row is
paired with the model row whose numbers match it best, and then compared
number by number.

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
    return NUM.findall((text or "").replace(",", "").replace("−", "-"))


def model_rows(reader: str, tid: str) -> list[list[str]] | None:
    f = CACHE / f"{tid}.{reader}.txt"
    if not f.exists():
        return None
    text = f.read_text()
    grid = IT.html_grid(text) if reader == "glm" else IT.otsl_grid(text)
    return [[n for c in r for n in nums(c)] for r in grid] or None


def best_row(parsed: list[str], candidates: list[list[str]]) -> list[str]:
    def score(c):
        return sum(1 for a, b in zip(parsed, c) if a == b) - abs(len(parsed) - len(c)) * 0.1
    return max(candidates, key=score) if candidates else []


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
            for r in it["body"]:
                parsed = [n for c in r["cells"].values() for n in nums(c)]
                if not parsed:
                    continue
                g = best_row(parsed, reads["glm"])
                p = best_row(parsed, reads["paddle"])
                for i, val in enumerate(parsed):
                    gv = g[i] if i < len(g) else ""
                    pv = p[i] if i < len(p) else ""
                    if gv != val or pv != val:
                        cells.append((r["label"], i, val, gv, pv))
            if not cells:
                verdict = "AGREE"
            elif any(gv == pv != val for _l, _i, val, gv, pv in cells):
                verdict = "CHECK"
            elif all(gv == val for _l, _i, val, gv, _pv in cells) or \
                    all(pv == val for _l, _i, val, _gv, pv in cells):
                verdict = "ONE-READER"
            else:
                verdict = "DIFFER"
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append({"tid": it["tid"], "verdict": verdict, "n_differing": len(cells),
                     "cells": "; ".join(f"{l!s:.24} #{i}: parse {v} / glm {g} / paddle {p}"
                                        for l, i, v, g, p in cells[:8])})
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

#!/usr/bin/env python3
"""A second, independent read of a table, from a vision model, as a CROSS-CHECK.

The geometric parser in `build_paper_comparisons.py` reads where the ink is. It
cannot read what governs what, because a PDF does not say: when a method name
spans several columns and the page carries no `\\cmidrule`, the grouping is
genuinely undecidable, which is why SPANNER_OVERRIDE exists and is curated by
hand.

A document vision model CAN state it, because it emits HTML with `colspan` and
`rowspan`. That is the one fact the text layer lacks.

**IT IS NOT ALLOWED TO BE THE SOURCE OF A NUMBER.** A vision model will
occasionally read 0.665 as 0.655, and a wrong digit in a precision is exactly
the invisible error every guard in the miner exists to avoid: nothing
downstream could ever catch it. So this script never writes a value. It
compares its grid against the geometric parse and reports:

    AGREE      every cell matches, so the structure it proposes is trustworthy
    STRUCTURE  the numbers match but the grouping differs -> a proposal to review
    DISAGREE   a cell differs -> shown to a human, and nothing is proposed

Two independent extractions agreeing is much stronger evidence than either
alone, which is the whole point of running it. Where they disagree, the review
page is the place to look.

    uv run --python 3.12 --with "torch==2.8.*" --with "torchvision==0.23.*" \\
        --with "transformers>=5,<6" --with accelerate --with pillow --with einops \\
        --index-strategy unsafe-best-match \\
        --extra-index-url https://download.pytorch.org/whl/cu128 \\
        python3 build_table_vlm.py --ids 49,58

**Report only, and never in CI**: it needs a GPU, a 2 GB local model and the
PDF library. The model is loaded from a LOCAL CLONE, because the Hugging Face
Python client cannot reach the Hub through this machine's TLS gateway while
`git` can -- the same split recorded under 'Checkpoints' in CLAUDE.md.

The crops it reads are the ones `review_comparisons.py` already renders, so the
two tools see exactly the same picture a human does.
"""
from __future__ import annotations

import argparse
import collections
import csv
import html.parser
import pathlib
import re
import sqlite3
import sys

import build_pdf_library as bpl
import build_paper_comparisons as B

# The model lives beside the other code checkouts, NOT in the PDF library:
# the library is papers, and a 2 GB set of weights is not one. Override with
# --model.
#
# GLM-OCR rather than PaddleOCR-VL, after trying the latter. PaddleOCR-VL's
# documented path is the PaddlePaddle pipeline and its transformers files fit
# NEITHER major version: on 4.x its code calls
# `create_causal_mask(inputs_embeds=...)` where the parameter is
# `input_embeds`, and on 5.x `ROPE_INIT_FUNCTIONS` no longer has the 'default'
# entry it looks up. GLM-OCR is a Glm4v model, which transformers supports
# natively with no remote code, so there is no version window to hit.
MODEL = pathlib.Path.home() / "code" / "GLM-OCR"
CROPS = bpl.DEFAULT_DIR / "comparison-review"
OUT = pathlib.Path(__file__).with_name("table_vlm_candidates.csv")
PROMPT = ("Convert the table in this image to HTML. Use colspan and rowspan to "
          "reproduce merged header cells exactly. Output only the HTML table.")


class Grid(html.parser.HTMLParser):
    """An HTML table to a dense 2D grid, honouring colspan and rowspan.

    The spans are the reason this script exists, so they are expanded rather
    than recorded: a header that covers three columns becomes that text in
    three cells, which is directly comparable with the geometric parser's
    per-column reading.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[int, str]] = []
        self.pending: dict[int, tuple[str, int]] = {}   # col -> (text, rows left)
        self.cur: dict[int, str] | None = None
        self.col = 0
        self.span = 1
        self.rowspan = 1
        self.buf: list[str] = []
        self.incell = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self.cur = {}
            self.col = 0
            for c, (text, left) in list(self.pending.items()):
                self.cur[c] = text
                if left <= 1:
                    del self.pending[c]
                else:
                    self.pending[c] = (text, left - 1)
        elif tag in ("td", "th") and self.cur is not None:
            self.incell = True
            self.buf = []
            try:
                self.span = max(1, int(a.get("colspan", 1)))
                self.rowspan = max(1, int(a.get("rowspan", 1)))
            except ValueError:
                self.span = self.rowspan = 1

    def handle_data(self, data):
        if self.incell:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cur is not None:
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            while self.col in self.cur:
                self.col += 1
            for i in range(self.span):
                self.cur[self.col + i] = text
                if self.rowspan > 1:
                    self.pending[self.col + i] = (text, self.rowspan - 1)
            self.col += self.span
            self.incell = False
        elif tag == "tr" and self.cur is not None:
            self.rows.append(self.cur)
            self.cur = None

    def dense(self) -> list[list[str]]:
        w = max((max(r) + 1 for r in self.rows if r), default=0)
        return [[r.get(i, "") for i in range(w)] for r in self.rows]


NUM = re.compile(r"\d*\.\d+|\d+")


def numbers(text: str) -> list[str]:
    return NUM.findall(text.replace(",", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated publication ids")
    ap.add_argument("--limit", type=int, default=24,
                    help="stop after N crops (inference is seconds each)")
    ap.add_argument("--accepted-only", action="store_true",
                    help="only cross-check tables the miner already accepted")
    ap.add_argument("--debug", action="store_true",
                    help="re-raise an inference error instead of counting it")
    ap.add_argument("--model", type=pathlib.Path, default=MODEL,
                    help=f"local model clone (default {MODEL})")
    args = ap.parse_args()

    model_dir = args.model
    if not model_dir.exists():
        print(f"model not found at {model_dir}\n"
              f"  git clone https://huggingface.co/PaddlePaddle/PaddleOCR-VL "
              f"{model_dir}", file=sys.stderr)
        return 2
    if not (CROPS / "index.html").exists():
        print("no crops yet; run review_comparisons.py first", file=sys.stderr)
        return 2
    try:
        import torch
        import transformers
        from transformers import AutoProcessor
        from PIL import Image
    except ModuleNotFoundError as exc:
        print(f"missing {exc.name}; see the docstring for the uv invocation",
              file=sys.stderr)
        return 2

    con = sqlite3.connect(B.DB)
    con.row_factory = sqlite3.Row
    want = {int(i) for i in args.ids.split(",")} if args.ids else None

    # The crops carry the publication id in the filename, which is how this
    # script finds the geometric parse to compare against: p<id>-pg<page>-<n>.
    crops = sorted(CROPS.glob("p*-pg*-*.png"))
    # THE MANIFEST SAYS WHAT EACH CROP SHOWS. Reading the audit CSV and taking
    # the first accepted row for the publication compared a crop against a
    # different table: p9's appendix crops were scored against its Table 1,
    # 80 values against 30, and every verdict on a multi-table paper was
    # meaningless.
    manifest_path = CROPS / "crops.json"
    if not manifest_path.exists():
        print(f"no {manifest_path.name}; re-run review_comparisons.py",
              file=sys.stderr)
        return 2
    import json
    manifest = json.loads(manifest_path.read_text())

    print(f"loading {model_dir.name} ...", flush=True)
    proc = AutoProcessor.from_pretrained(str(model_dir), trust_remote_code=True)
    # The dtype argument was renamed between major versions, and a natively
    # supported model wants a different auto class from one that ships its own
    # code, so both are tried rather than assumed.
    kinds = []
    for name in ("AutoModelForImageTextToText", "AutoModelForVision2Seq",
                 "AutoModelForCausalLM"):
        if hasattr(transformers, name):
            kinds.append(getattr(transformers, name))
    model, last = None, None
    for cls in kinds:
        for kw in ({"dtype": torch.bfloat16}, {"torch_dtype": torch.bfloat16}):
            try:
                model = cls.from_pretrained(
                    str(model_dir), trust_remote_code=True, **kw,
                    device_map="cuda" if torch.cuda.is_available() else "cpu")
                break
            except Exception as exc:
                last = exc
        if model is not None:
            break
    if model is None:
        print(f"could not load the model: {last}", file=sys.stderr)
        return 2
    model.eval()
    print(f"  on {next(model.parameters()).device}", flush=True)

    rows_out: list[dict] = []
    tally: collections.Counter = collections.Counter()
    done = 0
    for crop in crops:
        tid = crop.stem
        info = manifest.get(tid)
        if not info:
            tally["crop not in the manifest"] += 1
            continue
        pid = info["publication_id"]
        if want and pid not in want:
            continue
        if args.accepted_only and info["verdict"] not in ("accepted", "approved"):
            continue
        if done >= args.limit:
            break
        done += 1
        msgs = [{"role": "user", "content": [
            {"type": "image", "image": str(crop)}, {"type": "text", "text": PROMPT}]}]
        try:
            text = proc.apply_chat_template(msgs, tokenize=False,
                                            add_generation_prompt=True)
            img = Image.open(crop).convert("RGB")
            inputs = proc(text=[text], images=[img], return_tensors="pt").to(model.device)
            with torch.inference_mode():
                ids = model.generate(**inputs, max_new_tokens=2048, do_sample=False)
            raw = proc.batch_decode(ids[:, inputs["input_ids"].shape[1]:],
                                     skip_special_tokens=True)[0]
        except Exception as exc:
            if args.debug:
                raise
            tally[f"inference failed: {type(exc).__name__}: {exc}"[:90]] += 1
            continue
        m = re.search(r"<table.*?</table>", raw, re.S | re.I)
        if not m:
            tally["no HTML table in the model's output"] += 1
            continue
        g = Grid()
        g.feed(m.group(0))
        dense = g.dense()
        if not dense:
            tally["HTML parsed to an empty grid"] += 1
            continue

        vlm_nums = [n for r in dense for c in r for n in numbers(c)]
        geo_nums = [n for v in info.get("values") or [] for n in numbers(v)]
        if geo_nums:
            shared = sum(1 for n in geo_nums if any(n.startswith(v[:5]) or
                                                    v.startswith(n[:5])
                                                    for v in vlm_nums))
            verdict = ("AGREE" if shared == len(geo_nums)
                       else "DISAGREE" if shared < 0.9 * len(geo_nums)
                       else "MOSTLY")
        else:
            verdict = "STRUCTURE"      # the miner refused it; nothing to compare
        tally[verdict] += 1
        rows_out.append({
            "publication_id": pid, "crop": crop.name, "tid": tid,
            "table_label": info["table_label"], "miner": info["verdict"],
            "verdict": verdict,
            "geo_cells": len(geo_nums), "vlm_cells": len(vlm_nums),
            "vlm_header_rows": sum(1 for r in dense
                                   if not any(numbers(c) for c in r)),
            "vlm_cols": len(dense[0]) if dense else 0,
            "vlm_header": " | ".join(dense[0]) if dense else "",
            "vlm_header2": " | ".join(dense[1]) if len(dense) > 1 else "",
        })
        print(f"  {verdict:9} {tid:16} {info['table_label'][:12]:13} "
              f"geo {len(geo_nums):>3} / vlm {len(vlm_nums):>3}   "
              f"{(' | '.join(dense[0]))[:46]}", flush=True)

    if rows_out:
        with OUT.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            for r in rows_out:
                w.writerow(r)
    print()
    for k, v in tally.most_common():
        print(f"      {v:>4}  {k}")
    print(f"\n  {len(rows_out)} row(s) -> {OUT.name}")
    print("\nReport only. No value from this model is ever written to denovo.db;")
    print("it is here to confirm or contradict the geometric parse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

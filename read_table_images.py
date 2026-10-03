#!/usr/bin/env python3
"""Read the tables that exist only as IMAGES, with one vision model per run.

Some papers typeset a table as a picture: InstaNovo's published Extended Data
tables carry a caption and a footnote in the text layer and nothing between
them, and DeepNovo-DIA's supplementary tables have almost no text layer at all.
The geometric miner cannot see those, by construction.

This script is HALF of the answer, and deliberately only half. It runs ONE
model over the crops `review_comparisons.py` lists in `image_tables.json` and
caches each model's raw output. Nothing here decides anything:
`review_comparisons.py` compares the two models' grids and accepts a table only
when they agree on EVERY cell, and even then the table goes to a human for
sign-off like any other. A vision model will occasionally read 0.665 as 0.655;
two independent models making the same misreading is far less likely, and a
person checking the picture is the last line.

The two models need incompatible transformers versions, so each runs in its own
environment:

    # GLM-OCR: a Glm4v model, native in transformers 5
    uv run --python 3.12 --with "torch==2.8.*" --with "torchvision==0.23.*" \\
        --with "transformers>=5,<6" --with accelerate --with pillow --with einops \\
        --index-strategy unsafe-best-match \\
        --extra-index-url https://download.pytorch.org/whl/cu128 \\
        python3 read_table_images.py --reader glm

    # PaddleOCR-VL: ships its own code, written for transformers 4.x
    uv run --python 3.12 --with "torch==2.8.*" --with "torchvision==0.23.*" \\
        --with "transformers>=4.57,<4.58" --with accelerate --with pillow \\
        --with einops --with protobuf --with sentencepiece \\
        --index-strategy unsafe-best-match \\
        --extra-index-url https://download.pytorch.org/whl/cu128 \\
        python3 read_table_images.py --reader paddle

**PaddleOCR-VL needs a one-line patch, applied to an overlay, never to the
clone.** Its modeling file calls `create_causal_mask(inputs_embeds=...)`, the
transformers-5 name; every 4.x release, 4.55 and 4.57 included, calls the
parameter `input_embeds`. And transformers 5 removed `ROPE_INIT_FUNCTIONS
['default']`, which the same file needs. So no released version runs it as
shipped. `paddle_overlay()` builds `~/.cache/paddleocr-vl-tf4`: symlinks to
every file of the local clone, weights included, plus a copy of the modeling
file with that one argument renamed.

Never in CI: it needs a GPU, two local model clones and the PDF library.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import build_pdf_library as bpl

CROPS = bpl.DEFAULT_DIR / "comparison-review"
MANIFEST = CROPS / "image_tables.json"
CACHE = CROPS / "vlm-cache"
MODELS = {
    "glm": pathlib.Path.home() / "code" / "GLM-OCR",
    "paddle": pathlib.Path.home() / "code" / "PaddleOCR-VL",
}
GLM_PROMPT = ("Convert the table in this image to HTML. Use colspan and rowspan to "
              "reproduce merged header cells exactly. Output only the HTML table.")


def paddle_overlay(clone: pathlib.Path) -> pathlib.Path:
    """The PaddleOCR-VL clone, with its one transformers-4 incompatibility fixed.

    Symlinks, so the 4 GB of weights are not copied, and the clone itself is
    never edited.
    """
    out = pathlib.Path.home() / ".cache" / "paddleocr-vl-tf4"
    out.mkdir(parents=True, exist_ok=True)
    for f in clone.iterdir():
        if f.name == "modeling_paddleocr_vl.py":
            continue
        link = out / f.name
        if not link.exists():
            link.symlink_to(f)
    src = (clone / "modeling_paddleocr_vl.py").read_text()
    old = ("        causal_mask = create_causal_mask(\n"
           "            config=self.config,\n"
           "            inputs_embeds=inputs_embeds,")
    if src.count(old) != 1:
        sys.exit("PaddleOCR-VL's modeling file changed; the overlay patch no longer applies")
    (out / "modeling_paddleocr_vl.py").write_text(
        src.replace(old, old.replace("inputs_embeds=inputs_embeds",
                                     "input_embeds=inputs_embeds")))
    return out


def load(reader: str):
    import torch
    import transformers
    from transformers import AutoProcessor
    path = MODELS[reader]
    if reader == "paddle":
        path = paddle_overlay(path)
        model = transformers.AutoModelForCausalLM.from_pretrained(
            str(path), trust_remote_code=True, dtype=torch.bfloat16).to("cuda").eval()
    else:
        model = transformers.AutoModelForImageTextToText.from_pretrained(
            str(path), trust_remote_code=True, dtype=torch.bfloat16,
            device_map="cuda").eval()
    proc = AutoProcessor.from_pretrained(str(path), trust_remote_code=True)
    return model, proc


def read(reader: str, model, proc, image) -> str:
    import torch
    prompt = "Table Recognition:" if reader == "paddle" else GLM_PROMPT
    msgs = [{"role": "user", "content": [{"type": "image", "image": image},
                                         {"type": "text", "text": prompt}]}]
    inputs = proc.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                      return_dict=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        ids = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
    return proc.batch_decode(ids[:, inputs["input_ids"].shape[1]:],
                             skip_special_tokens=False)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reader", choices=sorted(MODELS), required=True)
    ap.add_argument("--force", action="store_true", help="re-read cached crops")
    ap.add_argument("--crosscheck", action="store_true",
                    help="read the crops of every accepted or signed-off TEXT table "
                         "instead, so crosscheck_tables.py can compare all three "
                         "readings")
    args = ap.parse_args()
    if args.crosscheck:
        items = json.loads((CROPS / "items.json").read_text())
        todo = [{"tid": i["tid"], "img": i["img"]} for i in items
                if i.get("img") and i["verdict"] in ("accepted", "approved")
                and i.get("extraction", "text") == "text"]
    else:
        if not MANIFEST.exists():
            sys.exit(f"no {MANIFEST.name}; run review_comparisons.py first")
        todo = json.loads(MANIFEST.read_text())
    CACHE.mkdir(exist_ok=True)
    pending = [t for t in todo if args.force
               or not (CACHE / f"{t['tid']}.{args.reader}.txt").exists()]
    print(f"{len(todo)} image table(s), {len(pending)} to read with {args.reader}")
    if not pending:
        return 0
    from PIL import Image
    model, proc = load(args.reader)
    for t in pending:
        img = Image.open(CROPS / t["img"]).convert("RGB")
        text = read(args.reader, model, proc, img)
        (CACHE / f"{t['tid']}.{args.reader}.txt").write_text(text)
        print(f"  {t['tid']}: {len(text)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

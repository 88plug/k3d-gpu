#!/usr/bin/env python3
"""H27 quality proxy: teacher-forced mean NLL (nats/token) of each precision.

Runs inside the pinned python container with optimum-intel and openvino from
--prepare. Text is tokenized once with the fp16 model's tokenizer, cut into
non-overlapping 512-token windows, and scored on CPU. Writes
{"heldout": {prec: nll}, "wikitext2": {prec: nll}, "tokens": {...}}.

  nll.py --models /models --heldout heldout.txt --wikitext wikitext2.txt --out nll.json
"""

import argparse
import json
import math

import torch
from optimum.intel import OVModelForCausalLM
from transformers import AutoTokenizer

WINDOW = 512
PRECS = ("fp16", "int8", "int4")


def windows(ids, budget):
    ids = ids[:budget]
    return [ids[i:i + WINDOW] for i in range(0, len(ids) - 1, WINDOW) if len(ids[i:i + WINDOW]) > 1]


def nll(model, wins):
    tot, n = 0.0, 0
    for w in wins:
        x = torch.tensor([w])
        with torch.no_grad():
            logits = model(input_ids=x, attention_mask=torch.ones_like(x)).logits[0, :-1].float()
        lp = torch.log_softmax(logits, -1).gather(1, x[0, 1:].unsqueeze(1)).squeeze(1)
        tot -= lp.sum().item()
        n += lp.numel()
    return tot / n, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--wikitext", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tok = AutoTokenizer.from_pretrained("%s/OpenVINO/Qwen3-4B-fp16-ov" % a.models)
    sets = {}
    for name, path, budget in (("heldout", a.heldout, 4096), ("wikitext2", a.wikitext, 1024)):
        with open(path, encoding="utf-8") as f:
            sets[name] = windows(tok(f.read())["input_ids"], budget)
    out = {"heldout": {}, "wikitext2": {}, "tokens": {}, "window": WINDOW}
    for p in PRECS:
        m = OVModelForCausalLM.from_pretrained("%s/OpenVINO/Qwen3-4B-%s-ov" % (a.models, p), device="CPU",
                                               use_cache=False, compile=True)
        for name, wins in sets.items():
            v, n = nll(m, wins)
            out[name][p] = v if math.isfinite(v) else None
            out["tokens"][name] = n
        del m
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()

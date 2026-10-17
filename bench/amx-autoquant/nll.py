#!/usr/bin/env python3
"""H27 quality proxy: teacher-forced mean NLL (nats/token) of each precision.

Runs inside the pinned python container with optimum-intel and openvino from
--prepare. Text is tokenized once with the fp16 model's tokenizer, cut into
non-overlapping 512-token windows, and scored on CPU. The OpenVINO/Qwen3-4B-*-ov
IRs are stateful: a forward call with no past_key_values resets the KV state
(optimum-intel 2.2.0 OVModelForCausalLM.prepare_inputs), so each window is scored
from an empty cache. Every arm runs with INFERENCE_PRECISION_HINT=f32, so the fp16
reference is not silently executed in bf16 on AMX/AVX512_BF16 hosts and the deltas
measure the weights alone. Writes
{"heldout": {prec: nll}, "wikitext2": {prec: nll}, "tokens": {...}}.

  nll.py --models /models --heldout heldout.txt --wikitext wikitext2.txt --out nll.json
         [--precs int4] [--max-windows 1]     (smoke test: one arm, one window)
"""

import argparse
import json
import math

import torch
from optimum.intel import OVModelForCausalLM
from transformers import AutoTokenizer

WINDOW = 512
PRECS = ("fp16", "int8", "int4")
OV_CONFIG = {"INFERENCE_PRECISION_HINT": "f32"}


def windows(ids, budget, limit=None):
    ids = ids[:budget]
    wins = [ids[i:i + WINDOW] for i in range(0, len(ids) - 1, WINDOW) if len(ids[i:i + WINDOW]) > 1]
    return wins[:limit] if limit else wins


def nll(model, wins):
    tot, n = 0.0, 0
    for w in wins:
        x = torch.tensor([w])
        with torch.no_grad():
            logits = model(input_ids=x, attention_mask=torch.ones_like(x), past_key_values=None).logits
        if logits.shape[1] != x.shape[1]:
            raise SystemExit("model returned logits for %d of %d positions; teacher forcing needs all"
                             % (logits.shape[1], x.shape[1]))
        logits = logits[0, :-1].float()
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
    ap.add_argument("--precs", default=",".join(PRECS))
    ap.add_argument("--max-windows", type=int)
    a = ap.parse_args()
    tok = AutoTokenizer.from_pretrained("%s/OpenVINO/Qwen3-4B-fp16-ov" % a.models)
    sets = {}
    for name, path, budget in (("heldout", a.heldout, 4096), ("wikitext2", a.wikitext, 1024)):
        with open(path, encoding="utf-8") as f:
            sets[name] = windows(tok(f.read())["input_ids"], budget, a.max_windows)
    out = {"heldout": {}, "wikitext2": {}, "tokens": {}, "window": WINDOW}
    for p in a.precs.split(","):
        m = OVModelForCausalLM.from_pretrained("%s/OpenVINO/Qwen3-4B-%s-ov" % (a.models, p), device="CPU",
                                               ov_config=OV_CONFIG, compile=True)
        for name, wins in sets.items():
            v, n = nll(m, wins)
            out[name][p] = v if math.isfinite(v) else None
            out["tokens"][name] = n
        del m
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()

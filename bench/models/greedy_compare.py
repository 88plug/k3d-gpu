#!/usr/bin/env python3
"""greedy_compare.py IR_DIR SWAP_DIR OUT_JSON [--max-new N] [--device CPU]: greedy outputs of two
OpenVINO IRs that differ only in their model .xml/.bin files (crescent amendment M1: the QAT IR
with the grid snap against the same export before it).

IR_DIR is a complete optimum-intel export (A: the snapped IR). SWAP_DIR holds the files that
replace IR_DIR's model files (B: the unsnapped openvino*model.xml/.bin that
export_qat_ov.sh keeps with QAT_KEEP_UNSNAPPED). B is a temporary directory of symlinks: IR_DIR's
files, with SWAP_DIR's files in place of the same names. Each prompt is generated greedily
(do_sample=False, thinking off through the chat template) on A and on B; the report gives, per
prompt, whether the token ids are identical, the first index where they differ, and both texts.
Runs in the export venv (optimum-intel, transformers).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

PROMPTS = [
    "What is the capital of Australia? Answer in one sentence.",
    "List three prime numbers greater than 50, separated by commas.",
    "A customer wants to move $250 from checking to savings. Write the one-line confirmation a bank "
    "assistant would send.",
    "Translate to French: The meeting is moved to Thursday at noon.",
    "Write a Python function that returns the n-th Fibonacci number iteratively.",
    "Explain in two sentences why the sky is blue.",
    "Sort these words alphabetically: pear, apple, mango, banana.",
    "What is 17 multiplied by 23? Give only the number.",
]


def farm(ir_dir, swap_dir):
    """A temp dir of symlinks to ir_dir's files, swap_dir's files taking precedence."""
    d = tempfile.mkdtemp(prefix="greedy-b-")
    swap = set(os.listdir(swap_dir))
    for name in os.listdir(ir_dir):
        src = os.path.join(swap_dir if name in swap else ir_dir, name)
        os.symlink(os.path.abspath(src), os.path.join(d, name))
    missing = swap - set(os.listdir(ir_dir))
    if missing:
        raise SystemExit(f"{swap_dir} has files {sorted(missing)} that {ir_dir} does not")
    return d


def generate(model_dir, prompts, max_new, device):
    from optimum.intel import OVModelForCausalLM, OVModelForVisualCausalLM
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    t0 = time.time()
    # An image-text-to-text export (Gemma 4) splits the LM into openvino_language_model.xml;
    # text-only generate goes through the VLM class with input_ids alone.
    cls = OVModelForVisualCausalLM if os.path.exists(os.path.join(model_dir, "openvino_language_model.xml")) \
        else OVModelForCausalLM
    m = cls.from_pretrained(model_dir, device=device)
    load_s = time.time() - t0
    out = []
    for p in prompts:
        ids = tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True,
                                      enable_thinking=False, return_tensors="pt", return_dict=True)
        n = ids["input_ids"].shape[1]
        t0 = time.time()
        g = m.generate(**ids, do_sample=False, max_new_tokens=max_new)
        new = g[0, n:].tolist()
        out.append({"ids": new, "text": tok.decode(new, skip_special_tokens=True), "seconds": round(time.time() - t0, 2)})
    return out, round(load_s, 1)


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ir_dir")
    ap.add_argument("swap_dir")
    ap.add_argument("out")
    ap.add_argument("--max-new", type=int, default=64)
    ap.add_argument("--device", default="CPU")
    a = ap.parse_args(argv)
    b_dir = farm(a.ir_dir, a.swap_dir)
    try:
        ra, la = generate(a.ir_dir, PROMPTS, a.max_new, a.device)
        rb, lb = generate(b_dir, PROMPTS, a.max_new, a.device)
    finally:
        shutil.rmtree(b_dir)
    rows = []
    for p, x, y in zip(PROMPTS, ra, rb):
        d = first_diff(x["ids"], y["ids"])
        rows.append({"prompt": p, "identical": d is None, "first_diff": d, "tokens": [len(x["ids"]), len(y["ids"])],
                     "snapped": x["text"], "unsnapped": y["text"], "seconds": [x["seconds"], y["seconds"]]})
    res = {"a": os.path.abspath(a.ir_dir), "b_swap": os.path.abspath(a.swap_dir), "max_new_tokens": a.max_new,
           "device": a.device, "load_seconds": [la, lb], "identical": sum(r["identical"] for r in rows),
           "prompts": len(rows), "rows": rows}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: res[k] for k in ("identical", "prompts", "load_seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

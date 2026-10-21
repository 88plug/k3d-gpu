#!/usr/bin/env python3
"""gguf_bytes.py GGUF [--experts N --routed K]: H54c byte split from a GGUF tensor table.

Reads only the header (stdlib, no gguf package). B_exp = routed/experts of the bytes of every
`ffn_(gate|up|down|gate_up)_exps` tensor, weights and per-expert scales (the bytes one decode step reads from the routed experts);
B_rest = every other per-layer tensor (`blk.*`) plus the output matrix (`output.weight`, or the
tied `token_embd.weight` when no output tensor exists). Prints JSON.
"""
import argparse
import json
import re
import struct
import sys

# ggml type -> (block elements, block bytes); ggml.h / gguf-py constants.py
TYPES = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34),
    9: (32, 36), 10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210),
    15: (256, 292), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8), 30: (1, 2),
}
SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q",
          11: "<q", 12: "<d"}
EXPS = re.compile(r"^blk\.\d+\.ffn_(gate|up|down|gate_up)_exps\.(weight|scale)$")


class Reader:
    def __init__(self, f):
        self.f = f

    def unpack(self, fmt):
        n = struct.calcsize(fmt)
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("truncated GGUF header")
        return struct.unpack(fmt, b)[0]

    def string(self):
        n = self.unpack("<Q")
        return self.f.read(n).decode("utf-8", "replace")

    def value(self, t):
        if t == 8:
            return self.string()
        if t == 9:
            it, n = self.unpack("<I"), self.unpack("<Q")
            return [self.value(it) for _ in range(n)]
        if t not in SCALAR:
            raise ValueError(f"unknown GGUF value type {t}")
        return self.unpack(SCALAR[t])


def tensors(path):
    with open(path, "rb") as f:
        r = Reader(f)
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        r.unpack("<I")
        nt, nkv = r.unpack("<Q"), r.unpack("<Q")
        kv = {}
        for _ in range(nkv):
            k = r.string()
            kv[k] = r.value(r.unpack("<I"))
        out = []
        for _ in range(nt):
            name = r.string()
            dims = [r.unpack("<Q") for _ in range(r.unpack("<I"))]
            t = r.unpack("<I")
            r.unpack("<Q")
            if t not in TYPES:
                raise ValueError(f"tensor {name}: unknown ggml type {t}")
            n = 1
            for d in dims:
                n *= d
            be, bb = TYPES[t]
            if n % be:
                raise ValueError(f"tensor {name}: {n} elements not a multiple of block {be}")
            out.append((name, t, n // be * bb))
        return kv, out


def split(tens, experts, routed):
    exp_all = sum(b for n, _, b in tens if EXPS.match(n))
    rest = sum(b for n, _, b in tens if n.startswith("blk.") and not EXPS.match(n))
    names = {n: b for n, _, b in tens}
    out_name = "output.weight" if "output.weight" in names else "token_embd.weight"
    rest += names.get(out_name, 0)
    b_exp = exp_all * routed / experts
    return {"B_exp": b_exp, "B_rest": rest, "B_tot": b_exp + rest, "exps_all": exp_all,
            "output_tensor": out_name, "n_tensors": len(tens)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("gguf")
    p.add_argument("--experts", type=int)
    p.add_argument("--routed", type=int)
    a = p.parse_args(argv)
    kv, tens = tensors(a.gguf)
    arch = kv.get("general.architecture", "")
    experts = a.experts or kv.get(f"{arch}.expert_count")
    routed = a.routed or kv.get(f"{arch}.expert_used_count")
    if not experts or not routed:
        sys.exit("expert_count/expert_used_count not in GGUF metadata; pass --experts/--routed")
    res = split(tens, experts, routed)
    res.update({"arch": arch, "experts": experts, "routed": routed,
                "block_count": kv.get(f"{arch}.block_count")})
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()

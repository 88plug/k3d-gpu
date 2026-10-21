#!/usr/bin/env python3
"""h60.py: EXPERIMENTS.md H60 (Qwen3.8-Flash-Next in host DRAM through llama.cpp) helpers.

  h60.py active-bytes GGUF...        bytes read per decoded token, by tensor class, from the
                                     GGUF headers (all shards of one model)
  h60.py score OUT [--stream GBS]    the H60 verdict from h60.sh's records in OUT

Bytes per token: every tensor is read once per token, except
  - lookup tables, of which a token reads a few rows (token_embd, per_layer_token_embd: the
    n-gram table; counted 0),
  - routed experts, of which a token reads expert_used_count / expert_count.
The shared expert, attention, Gated DeltaNet, norms and the output head (output.weight) count in
full. Bandwidth ceiling = STREAM triad / bytes per token. Efficiency = decode tok/s / ceiling.

Decision (pre-registered in H60): e = median N=1 decode tok/s / ceiling (STREAM 305 GB/s).
H54's llama.cpp arm Lg read 38% of the 305 GB/s ceiling on this host; the band is 0.38 x [0.7, 1.3] =
[0.266, 0.494]. 95% bootstrap CI of e's median over the N=1 requests (10,000 resamples, seed 60).
  SUPPORTED     CI inside the band
  REFUTED       CI wholly outside the band
  INCONCLUSIVE  otherwise, or the N=1 phase void after its re-run, the PSI guard stopped the hold,
                fewer than 8 ok N=1 requests, or GSM8K 0-49 below 25/50 (the model is not serving
                sensibly, so its speed is not read)
Python 3 standard library only."""
import glob
import json
import os
import random
import re
import statistics
import struct
import sys

STREAM_GBS = 305.0      # H54's pre-registered STREAM triad (48 threads, 2026-10-20)
EFF_H54 = 0.38          # H54a Lg: 48.74 tok/s x 2.380 GB / 305 GB/s (EXPERIMENTS.md H54a run: 38%)
BAND = (round(EFF_H54 * 0.7, 3), round(EFF_H54 * 1.3, 3))
EXP = re.compile(r"ffn_(gate|up|down|gate_up)_exps")
LOOKUP = re.compile(r"^(token_embd|per_layer_token_embd)\.")


def gguf(path):
    """(kv, [(name, nbytes)]) from one GGUF v2+ file's header; sizes from the offset table."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        f.read(4)
        n_t, n_kv = struct.unpack("<QQ", f.read(16))
        sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

        def rstr():
            (n,) = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")

        def rval(t):
            if t == 8:
                return rstr()
            if t == 9:
                et, n = struct.unpack("<IQ", f.read(12))
                return [rval(et) for _ in range(n)] if et in (8, 9) else f.read(sizes[et] * n)
            fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                   10: "<Q", 11: "<q", 12: "<d"}[t]
            return struct.unpack(fmt, f.read(sizes[t]))[0]
        kv, align = {}, 32
        for _ in range(n_kv):
            k = rstr()
            (t,) = struct.unpack("<I", f.read(4))
            v = rval(t)
            if isinstance(v, (int, float, str)):
                kv[k] = v
        align = kv.get("general.alignment", align)
        infos = []
        for _ in range(n_t):
            name = rstr()
            (nd,) = struct.unpack("<I", f.read(4))
            f.read(8 * nd)
            _typ, off = struct.unpack("<IQ", f.read(12))
            infos.append((name, off))
        start = (f.tell() + align - 1) // align * align
    end = os.path.getsize(path) - start
    infos.sort(key=lambda x: x[1])
    return kv, [(n, (infos[i + 1][1] if i + 1 < len(infos) else end) - o) for i, (n, o) in enumerate(infos)]


def active_bytes(paths):
    kv, rows = {}, []
    for p in paths:
        k, r = gguf(p)
        kv.update(k)
        rows += r
    arch = kv.get("general.architecture", "")
    used, total = kv.get(f"{arch}.expert_used_count"), kv.get(f"{arch}.expert_count")
    if not (used and total):
        raise ValueError("no expert_used_count / expert_count in the GGUF metadata")
    cls = {"routed_experts": 0, "lookup_tables": 0, "rest": 0}
    for n, b in rows:
        cls["routed_experts" if EXP.search(n) else "lookup_tables" if LOOKUP.match(n) else "rest"] += b
    active = cls["rest"] + cls["routed_experts"] * used / total
    return {"arch": arch, "expert_used_count": used, "expert_count": total, "bytes": cls,
            "file_bytes": sum(cls.values()), "active_bytes": int(active)}


def boot_ci(xs, n=10000, seed=60):
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return meds[int(0.025 * n)], meds[int(0.975 * n) - 1]


def score(out, stream=STREAM_GBS):
    ab = json.load(open(os.path.join(out, "active-bytes.json")))
    ceiling = stream * 1e9 / ab["active_bytes"]
    res = {"active_bytes": ab["active_bytes"], "stream_gbs": stream, "ceiling_tok_s": round(ceiling, 2),
           "band": BAND, "why": []}
    if os.path.exists(os.path.join(out, "psi-abort")):
        res["why"].append("PSI guard stopped the hold: " + open(os.path.join(out, "psi-abort")).read().strip())
    phases = {}
    if os.path.exists(os.path.join(out, "phases.jsonl")):
        for line in open(os.path.join(out, "phases.jsonl")):
            r = json.loads(line)
            phases[r["phase"]] = r   # the last try wins
    n1 = phases.get("n1")
    if not n1:
        res["why"].append("no N=1 phase")
    elif n1["check"]["void"]:
        res["why"].append("N=1 phase void after its re-run: " + n1["check"]["reason"])
    tps = []
    f = os.path.join(out, "load-n1.json")
    if os.path.exists(f):
        s = json.load(open(f))["phases"][0]["samples"]
        tps = [x["decode_tokens_per_s"] for x in s if x.get("ok") and x.get("decode_tokens_per_s")]
    if len(tps) < 8:
        res["why"].append(f"{len(tps)} ok N=1 requests with a decode rate (< 8)")
    q = os.path.join(out, "quality.json")
    if os.path.exists(q):
        res["gsm8k_correct"] = json.load(open(q))["correct"]
        if res["gsm8k_correct"] < 25:
            res["why"].append(f"GSM8K 0-49 {res['gsm8k_correct']}/50 (< 25)")
    else:
        res["why"].append("no GSM8K record")
    if tps:
        eff = [t / ceiling for t in tps]
        lo, hi = boot_ci(eff)
        res.update(n1_decode_median=round(statistics.median(tps), 3), efficiency_median=round(statistics.median(eff), 4),
                   efficiency_ci95=[round(lo, 4), round(hi, 4)])
    if res["why"] or not tps:
        return dict(res, verdict="INCONCLUSIVE")
    lo, hi = res["efficiency_ci95"]
    if BAND[0] <= lo and hi <= BAND[1]:
        v = "SUPPORTED"
    elif hi < BAND[0] or lo > BAND[1]:
        v = "REFUTED"
    else:
        v = "INCONCLUSIVE"
    return dict(res, verdict=v)


def main(argv):
    if len(argv) >= 2 and argv[0] == "active-bytes":
        print(json.dumps(active_bytes(argv[1:]), indent=1))
    elif len(argv) >= 2 and argv[0] == "score":
        stream = float(argv[3]) if len(argv) >= 4 and argv[2] == "--stream" else STREAM_GBS
        print(json.dumps(score(argv[1], stream), indent=1))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

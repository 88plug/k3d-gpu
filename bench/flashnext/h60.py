#!/usr/bin/env python3
"""h60.py: EXPERIMENTS.md H60 (Qwen3.8-Flash-Next in host DRAM through llama.cpp) helpers.

  h60.py active-bytes GGUF...        bytes read per decoded token, by tensor class, from the
                                     GGUF headers (all shards of one model)
  h60.py score OUT [--stream GBS]    the H60 verdict from h60.sh's records in OUT

Bytes per token: every tensor is read once per token, except
  - lookup tables, of which a token reads a few rows (token_embd, per_layer_token_embd: the
    n-gram table; counted 0),
  - routed experts, of which a token reads expert_used_count / expert_count.
The shared expert, attention, Gated DeltaNet, norms and the output head count in full (with tied
embeddings, no output.weight, token_embd is the output head and counts in full). Bandwidth ceiling = STREAM triad / bytes per token. Efficiency = decode tok/s / ceiling.

Decision (pre-registered in H60): e = median N=1 decode tok/s / ceiling (STREAM 305 GB/s), for
Flash-Next (fn) and for the same-hold control Lg (Google's Gemma 4 QAT Q4_0 GGUF, H54's Lg, on the
same llama.cpp build and arguments). r = e_fn / e_lg, 95% bootstrap CI (both arms' requests
resampled, 10,000 resamples, seed 60).
  SUPPORTED     CI inside [0.7, 1.3]
  REFUTED       CI wholly outside [0.7, 1.3]
  INCONCLUSIVE  otherwise, or fn's N=1 phase void after its re-run, the PSI guard stopped the
                hold, fewer than 8 ok fn N=1 requests, or GSM8K 0-49 below 25/50
Without a valid Lg control (not delivered, void, or < 8 ok requests), e_fn's CI is read against
H54's fixed efficiency instead: 0.38 x [0.7, 1.3] = [0.266, 0.494] (H54a Lg, 48.74 tok/s x 2.380
GB / 305 GB/s).
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
RATIO_BAND = (0.7, 1.3)
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
    # With tied embeddings (no output.weight) token_embd is also the output head, read in full.
    tied = not any(n.startswith("output.weight") for n, _ in rows)
    cls = {"routed_experts": 0, "lookup_tables": 0, "rest": 0}
    for n, b in rows:
        lookup = LOOKUP.match(n) and not (tied and n.startswith("token_embd."))
        cls["routed_experts" if EXP.search(n) else "lookup_tables" if lookup else "rest"] += b
    active = cls["rest"] + cls["routed_experts"] * used / total
    return {"arch": arch, "expert_used_count": used, "expert_count": total, "tied_output": tied, "bytes": cls,
            "file_bytes": sum(cls.values()), "active_bytes": int(active)}


def boot_ci(xs, n=10000, seed=60, ys=None):
    """95% bootstrap CI of median(xs), or of median(xs) / median(ys) with both resampled."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        m = statistics.median(rng.choices(xs, k=len(xs)))
        vals.append(m / statistics.median(rng.choices(ys, k=len(ys))) if ys else m)
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def arm(out, name):
    """(N=1 decode rates, why-not list) for one arm directory OUT/NAME."""
    d, why = os.path.join(out, name), []
    phases = {}
    if os.path.exists(os.path.join(d, "phases.jsonl")):
        for line in open(os.path.join(d, "phases.jsonl")):
            try:
                r = json.loads(line)
            except ValueError:
                why.append(f"{name}: unreadable phases.jsonl line")
                continue
            phases[r["phase"]] = r   # the last try wins
    n1 = phases.get("n1")
    if not n1:
        why.append(f"{name}: no N=1 phase")
    elif n1["check"].get("void", True):
        why.append(f"{name}: N=1 phase void after its re-run: " + str(n1["check"].get("reason")))
    tps = []
    f = os.path.join(d, "load-n1.json")
    if os.path.exists(f):
        smp = json.load(open(f))["phases"][0]["samples"]
        tps = [x["decode_tokens_per_s"] for x in smp if x.get("ok") and x.get("decode_tokens_per_s")]
    if len(tps) < 8:
        why.append(f"{name}: {len(tps)} ok N=1 requests with a decode rate (< 8)")
    return tps, why, phases


def score(out, stream=STREAM_GBS):
    """Primary: r = e_fn / e_lg, Flash-Next's bandwidth efficiency over the same-hold Lg
    control's, CI inside [0.7, 1.3] SUPPORTED, wholly outside REFUTED. Without a valid Lg control,
    e_fn against H54's fixed 0.38 x [0.7, 1.3] (BAND) instead."""
    ab = json.load(open(os.path.join(out, "active-bytes.json")))
    c_fn = stream * 1e9 / ab["active_bytes"]
    res = {"active_bytes": ab["active_bytes"], "stream_gbs": stream, "ceiling_tok_s": round(c_fn, 2), "why": []}
    if os.path.exists(os.path.join(out, "psi-abort")):
        res["why"].append("PSI guard stopped the hold: " + open(os.path.join(out, "psi-abort")).read().strip())
    fn, why_fn, ph = arm(out, "fn")
    res["why"] += why_fn
    q = os.path.join(out, "fn", "quality.json")
    if os.path.exists(q):
        res["gsm8k_correct"] = json.load(open(q))["correct"]
        if res["gsm8k_correct"] < 25:
            res["why"].append(f"GSM8K 0-49 {res['gsm8k_correct']}/50 (< 25)")
    else:
        res["why"].append("no GSM8K record")
    lg, why_lg, _ = arm(out, "lg")
    ab_lg = os.path.join(out, "active-bytes-lg.json")
    control = not why_lg and os.path.exists(ab_lg)
    res["control"] = "same-hold Lg" if control else "H54 fixed 0.38 (Lg control not valid: " + "; ".join(why_lg or ["no bytes"]) + ")"
    if fn:
        e_fn = [t / c_fn for t in fn]
        res.update(fn_n1_decode_median=round(statistics.median(fn), 3), e_fn_median=round(statistics.median(e_fn), 4))
    if control:
        c_lg = stream * 1e9 / json.load(open(ab_lg))["active_bytes"]
        e_lg = [t / c_lg for t in lg]
        res.update(lg_n1_decode_median=round(statistics.median(lg), 3), e_lg_median=round(statistics.median(e_lg), 4))
    if res["why"] or not fn:
        return dict(res, verdict="INCONCLUSIVE")
    if control:
        lo, hi = boot_ci(e_fn, ys=e_lg)
        band = RATIO_BAND
        res.update(stat="e_fn / e_lg", ratio_median=round(res["e_fn_median"] / res["e_lg_median"], 4))
    else:
        lo, hi = boot_ci(e_fn)
        band = BAND
        res.update(stat="e_fn")
    res.update(band=band, ci95=[round(lo, 4), round(hi, 4)])
    if band[0] <= lo and hi <= band[1]:
        v = "SUPPORTED"
    elif hi < band[0] or lo > band[1]:
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

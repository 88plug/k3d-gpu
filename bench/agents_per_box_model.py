"""Agents-per-box step model for a continuous-batching CPU engine (stdlib only).

Calibrated to H2 measurements on i9-13900T P-cores, Qwen3-4B int4, OVMS 2026.4.1:
  c=1 decode 15.7 tok/s -> step 63.7 ms ; c=4 14.4 tok/s/stream -> step 69.4 ms
  prefill ~172 tok/s.
Step time = T0 + A*prefill_tokens + sum_over_decoding_seqs(B + kv_bytes_read/BW)
            + SPEC verification extra tokens * A_verify.
Each agent loops: LLM turn (prefill new tokens, decode out tokens) -> tool wait.
Turn latency = submit -> last token.  Output: max N with p95 turn <= SLO.
"""
import random, statistics, sys

KV_ELEMS_PER_TOKEN = 2 * 36 * 8 * 128          # Qwen3-4B: 2*L*kv_heads*head_dim
T0 = 0.0618                                      # s, fitted intercept
B_SEQ = 0.0019                                   # s per decoding seq at short ctx (fit)
A_PREFILL = (256 / 172 - T0) / 256               # s per prefill token (chunk 256)
BW = 70e9                                        # B/s achievable DDR5-5600 2ch (assumed)
CHUNK = 256                                      # max_num_batched_tokens default

def run(n_agents, kv_bytes=1.0, shared_prefix_attn=False, prefix_cache=True,
        spec_accept=1.0, spec_k=0, prefix=4000, new_tok=500, out_tok=150,
        tool_mean=6.0, turns=8, seed=1, horizon=1800.0):
    rnd = random.Random(seed)
    # agent state
    ag = []
    for i in range(n_agents):
        ag.append(dict(ctx=prefix, turn=0, state="wait", t_ready=rnd.uniform(0, 20),
                       pre_left=0, dec_left=0, t_sub=0.0))
    t = 0.0; lat = []
    while t < horizon:
        for a in ag:
            if a["state"] == "wait" and a["t_ready"] <= t:
                if a["turn"] >= turns:          # new session: reset context
                    a["turn"] = 0; a["ctx"] = prefix
                a["state"] = "pre"
                a["pre_left"] = new_tok if prefix_cache else a["ctx"] + new_tok
                if a["turn"] == 0 and not prefix_cache:
                    a["pre_left"] = prefix + new_tok
                a["dec_left"] = out_tok; a["t_sub"] = t
        pre = [a for a in ag if a["state"] == "pre"]
        dec = [a for a in ag if a["state"] == "dec"]
        if not pre and not dec:
            nxt = min(a["t_ready"] for a in ag); t = max(t, nxt); continue
        budget = CHUNK; pre_tok = 0
        for a in pre:                            # FCFS chunked prefill
            take = min(budget, a["pre_left"]); a["pre_left"] -= take
            pre_tok += take; budget -= take
            if a["pre_left"] == 0:
                a["state"] = "dec"; a["ctx"] += new_tok
            if budget == 0: break
        kvb = KV_ELEMS_PER_TOKEN * kv_bytes
        if shared_prefix_attn and dec:
            kv_read = kvb * (prefix + sum(a["ctx"] - prefix for a in dec))
        else:
            kv_read = kvb * sum(a["ctx"] for a in dec)
        verify = len(dec) * spec_k
        step = T0 + A_PREFILL * (pre_tok + verify) + B_SEQ * len(dec) + kv_read / BW
        t += step
        for a in dec:
            g = min(a["dec_left"], spec_accept)
            a["dec_left"] -= g; a["ctx"] += g
            if a["dec_left"] <= 0:
                lat.append(t - a["t_sub"]); a["turn"] += 1
                a["state"] = "wait"; a["t_ready"] = t + rnd.expovariate(1 / tool_mean)
    lat.sort()
    if len(lat) < 20: return float("inf"), len(lat)
    return lat[int(0.95 * len(lat))], len(lat)

def max_agents(slo=10.0, **kw):
    best = 0
    for n in range(1, 129):
        p95, _ = run(n, **kw)
        if p95 <= slo: best = n
        elif n > best + 4: break
    return best

CONFIGS = {
    "baseline u8 KV, prefix cache ON": {},
    "prefix cache OFF": dict(prefix_cache=False),
    "u4 KV (half bytes)": dict(kv_bytes=0.5),
    "TURBO ~3.5-bit KV": dict(kv_bytes=0.44),
    "shared-prefix attention (Hydragen-style)": dict(shared_prefix_attn=True),
    "u4 KV + shared-prefix attn": dict(kv_bytes=0.5, shared_prefix_attn=True),
    "prompt-lookup k=5 accept 2.0 tok/step": dict(spec_k=5, spec_accept=2.0),
    "prompt-lookup k=3 accept 1.8": dict(spec_k=3, spec_accept=1.8),
    "tool output 250 tok (compress)": dict(new_tok=250),
    "all: u4+shared+tool250+PLD k3": dict(kv_bytes=0.5, shared_prefix_attn=True, new_tok=250,
                                       spec_k=3, spec_accept=1.8),
}

if __name__ == "__main__":
    slo = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    for name, kw in CONFIGS.items():
        n = max_agents(slo, **kw)
        p, cnt = run(max(n, 1), **kw)
        print(f"{name:45s} N@p95<={slo:.0f}s = {n:3d}  (p95 {p:5.2f}s, {cnt} turns)")


def moe_expert_fraction(batch, experts=128, top_k=8):
    """Expected fraction of routed-expert weights read per decode step at a batch.

    Uniform-routing approximation: 1 - (1 - k/E)^B.  Qwen3-30B-A3B: E=128, k=8.
    """
    return 1 - (1 - top_k / experts) ** batch

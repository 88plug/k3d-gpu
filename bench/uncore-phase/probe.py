#!/usr/bin/env python3
"""bench/uncore-phase/probe.py -- H29 probe (pre-registered; see EXPERIMENTS.md H29 row).

H29: for B=1 decode on the P-cores (OVMS, Qwen3-4B int4, the `agent up` CPU path),
a STATIC config (knobs held for the whole request, prefill included; not phase-aware)
that caps P-core frequency AND pins the uncore (ring/LLC on client parts; not the
IMC/DRAM clock) high cuts package J/token by >= 10% at tok/s within 3% of stock, on
BOTH gross J/token and marginal J/token against the round's stock-arm idle, and the
uncore pin is what keeps tok/s (UC beats core-cap-only C).

Arms (all on the same warm server, interleaved per round, one cell = one request):
  A   stock: uncore min/max and P-core scaling_max_freq as read at start
  U   uncore floor pinned: min = max = initial_max (the only way to "raise" it: max is
      already the hardware max on this host, 4.7 GHz)
  C   P-core cap: scaling_max_freq = --core-cap-khz on the pod's CPUs, uncore stock
  UC  H29 treatment: C + U
  L   regime control (rounds 0,2,4): uncore min = max = --uncore-low-khz. If decode
      tok/s does not drop here, the ring is not on decode's critical path and the
      uncore half of H29 cannot work.

Controls: per-cell idle window under the same arm (recorded; the verdict subtracts
the STOCK arm's idle of the same round from every arm, so a pin's standing idle cost
is charged to the arm that pays it, and also checks gross J/token); Williams-square arm order per round;
settle after every write; per-cell confounds (pkg temp, package throttle count
delta, pswpin/pswpout delta, load1, busy fraction of CPUs outside the pod);
manipulation checks (ring MHz from current_freq_khz for L and U, median pod-CPU MHz
for C, and A's pod-CPU MHz above the cap so the cap binds); hard
deadline; every sysfs write logged and restored in `finally` and on SIGTERM/SIGINT.

Run (root: RAPL energy_uj, current_freq_khz and all writes need it), server reachable:
  sudo python3 bench/uncore-phase/probe.py --cpus 0-15 --url http://127.0.0.1:18029/v3 \
      --model Qwen3-4B-int4-ov --out OUT.jsonl --write-log OUT.writes.log
  python3 bench/uncore-phase/probe.py --analyze OUT.jsonl
  --dry-run prints the cell order and the writes it would make, writes nothing.
K3D_GPU_SYSROOT=<dir> prefixes every sysfs/procfs path (the unit tests use a fake tree).
Stdlib only.
"""
import argparse, http.client, json, os, signal, statistics, sys, threading, time, urllib.parse
from math import comb

ROOT = os.environ.get("K3D_GPU_SYSROOT", "")
CPU = ROOT + "/sys/devices/system/cpu"
UNC = CPU + "/intel_uncore_frequency/package_00_die_00"
RAPL_PKG = ROOT + "/sys/class/powercap/intel-rapl:0/energy_uj"
RAPL_CORE = ROOT + "/sys/class/powercap/intel-rapl:0:0/energy_uj"
RAPL_WRAP = ROOT + "/sys/class/powercap/intel-rapl:0/max_energy_range_uj"
THROTTLE = CPU + "/cpu0/thermal_throttle/package_throttle_count"
PRIMARY = ["A", "U", "C", "UC"]
SETTLE_S, IDLE_S, SAMPLE_S = 6.0, 5.0, 0.5
PROMPT = "Write a long, detailed technical essay about memory hierarchies in modern CPUs."


def rd(p, cast=int):
    try:
        with open(p) as f:
            return cast(f.read().strip())
    except (OSError, ValueError):
        return None


def cpulist(s):
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def pkg_temp_c():
    try:
        zones = sorted(os.listdir(ROOT + "/sys/class/thermal"))
    except OSError:
        return None
    for z in zones:
        if rd(f"{ROOT}/sys/class/thermal/{z}/type", str) == "x86_pkg_temp":
            v = rd(f"{ROOT}/sys/class/thermal/{z}/temp")
            return v / 1000 if v is not None else None
    return None


def vmstat():
    d = {}
    with open(ROOT + "/proc/vmstat") as f:
        for line in f:
            k, v = line.split()
            if k in ("pswpin", "pswpout"):
                d[k] = int(v)
    return d


def stat_busy():
    out = {}
    with open(ROOT + "/proc/stat") as f:
        for line in f:
            if line.startswith("cpu") and line[3].isdigit():
                p = line.split()
                v = [int(x) for x in p[1:]]
                out[int(p[0][3:])] = (sum(v) - v[3] - v[4], sum(v))
    return out


def busy_frac(a, b, cpus):
    db = sum(b[c][0] - a[c][0] for c in cpus if c in a and c in b)
    dt = sum(b[c][1] - a[c][1] for c in cpus if c in a and c in b)
    return round(db / dt, 3) if dt else None


class Knobs:
    """Every write is logged with a timestamp; restore() puts back what was read at start."""

    def __init__(self, cpus, log_path, dry):
        self.cpus, self.dry = cpus, dry
        self.log = open(log_path, "a") if log_path else None
        self.u_min0, self.u_max0 = rd(f"{UNC}/initial_min_freq_khz"), rd(f"{UNC}/initial_max_freq_khz")
        self.u_min_saved, self.u_max_saved = rd(f"{UNC}/min_freq_khz"), rd(f"{UNC}/max_freq_khz")
        self.core_saved = {c: rd(f"{CPU}/cpu{c}/cpufreq/scaling_max_freq") for c in cpus}
        if None in (self.u_min0, self.u_max0, self.u_min_saved, self.u_max_saved) \
                or None in self.core_saved.values():
            sys.exit("uncore or cpufreq sysfs unreadable")

    def w(self, path, val):
        line = f"{time.time():.3f} write {path} {val}"
        if self.log:
            self.log.write(line + "\n"); self.log.flush()
        if self.dry:
            print("DRY", line); return
        with open(path, "w") as f:
            f.write(str(val))

    def uncore(self, lo, hi):
        # Floor min first, so any lo <= hi is reached by a valid sequence of writes.
        self.w(f"{UNC}/min_freq_khz", self.u_min0)
        self.w(f"{UNC}/max_freq_khz", hi)
        self.w(f"{UNC}/min_freq_khz", lo)

    def core(self, khz):
        for c in self.cpus:
            self.w(f"{CPU}/cpu{c}/cpufreq/scaling_max_freq",
                   khz if khz is not None else self.core_saved[c])

    def apply(self, arm, a):
        lo, hi = self.u_min_saved, self.u_max_saved
        if arm in ("U", "UC"):
            lo = hi = self.u_max0
        elif arm == "L":
            lo = hi = a.uncore_low_khz
        self.uncore(lo, hi)
        self.core(a.core_cap_khz if arm in ("C", "UC") else None)

    def restore(self):
        self.uncore(self.u_min_saved, self.u_max_saved)
        self.core(None)
        if self.log:
            self.log.write(f"{time.time():.3f} restored\n"); self.log.flush()


class Sampler(threading.Thread):
    def __init__(self, cpus):
        super().__init__(daemon=True)
        self.cpus, self.stop, self.ring, self.mhz, self.temp = cpus, threading.Event(), [], [], []

    def run(self):
        while not self.stop.is_set():
            r = rd(f"{UNC}/current_freq_khz")
            if r:
                self.ring.append(r / 1000)
            v = [rd(f"{CPU}/cpu{c}/cpufreq/scaling_cur_freq") for c in self.cpus]
            v = [x / 1000 for x in v if x]
            if v:
                self.mhz.append(statistics.mean(v))
            t = pkg_temp_c()
            if t is not None:
                self.temp.append(t)
            self.stop.wait(SAMPLE_S)


def energy_delta(e0, e1):
    if e0 is None or e1 is None:
        return None
    return (e1 - e0 if e1 >= e0 else e1 + rd(RAPL_WRAP) - e0) / 1e6


def stream(url, model, max_tokens):
    """Streamed chat completion; returns (ttft_s, decode_tok_s, chunks, wall_s, usage)."""
    u = urllib.parse.urlparse(url)
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": PROMPT}],
                       "max_tokens": max_tokens, "ignore_eos": True, "temperature": 0,
                       "stream": True, "stream_options": {"include_usage": True}})
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=300)
    t0 = time.monotonic()
    conn.request("POST", u.path.rstrip("/") + "/chat/completions", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read()[:300]!r}")
    first = last = None
    chunks, usage = 0, None
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        ev = json.loads(line[5:])
        if ev.get("usage"):
            usage = ev["usage"]
        if ev.get("choices"):
            now = time.monotonic(); chunks += 1
            first = first or now; last = now
    conn.close()
    # Same denominator as J/token: usage.completion_tokens when the server reports it.
    toks = (usage or {}).get("completion_tokens") or chunks
    dec = (toks - 1) / (last - first) if toks > 1 and first and last > first else None
    return (first - t0 if first else None), dec, chunks, time.monotonic() - t0, usage


# Williams square for 4 arms: every arm takes every position once and follows every
# other arm once per 4 rounds, so neither position nor carry-over favours an arm.
WILLIAMS = [[0, 1, 3, 2], [1, 2, 0, 3], [2, 3, 1, 0], [3, 0, 2, 1]]


def order(rounds):
    """Primary arms in Williams-square order by round; L third on even rounds."""
    cells = []
    for r in range(rounds):
        o = [PRIMARY[i] for i in WILLIAMS[r % 4]]
        if r % 2 == 0:
            o = o[:2] + ["L"] + o[2:]
        cells += [(r, arm) for arm in o]
    return cells


def measure(a):
    cpus = cpulist(a.cpus)
    others = [c for c in sorted(stat_busy()) if c not in cpus]
    k = Knobs(cpus, a.write_log, a.dry_run)
    cells = order(a.rounds)
    print(f"{len(cells)} cells: {' '.join(f'{r}{x}' for r, x in cells)}", file=sys.stderr)
    if a.dry_run:
        for _, arm in cells[:5]:
            k.apply(arm, a)
        k.restore()
        return

    def bail(signum, _):
        k.restore(); sys.exit(128 + signum)
    signal.signal(signal.SIGTERM, bail); signal.signal(signal.SIGINT, bail)
    start = time.monotonic()
    try:
        stream(a.url, a.model, 32)  # warm-up at stock config, not recorded
        with open(a.out, "a") as out:
            for r, arm in cells:
                if time.monotonic() - start > a.deadline_s:
                    print("deadline hit: stopping (partial run)", file=sys.stderr); break
                k.apply(arm, a); time.sleep(SETTLE_S)
                s = Sampler(cpus); s.start()
                p0 = rd(RAPL_PKG); time.sleep(IDLE_S); p1 = rd(RAPL_PKG)
                idle_w = energy_delta(p0, p1) / IDLE_S
                ring_idle = statistics.median(s.ring) if s.ring else None
                s.ring.clear(); s.mhz.clear()
                th0, vm0, b0, l1 = rd(THROTTLE), vmstat(), stat_busy(), os.getloadavg()[0]
                e0, c0, t0 = rd(RAPL_PKG), rd(RAPL_CORE), time.monotonic()
                ttft, dec, chunks, _, usage = stream(a.url, a.model, a.max_tokens)
                e1, c1, t1 = rd(RAPL_PKG), rd(RAPL_CORE), time.monotonic()
                s.stop.set(); s.join()
                vm1, b1, th1 = vmstat(), stat_busy(), rd(THROTTLE)
                dt, ej = t1 - t0, energy_delta(e0, e1)
                toks = (usage or {}).get("completion_tokens") or chunks
                rec = {"round": r, "arm": arm, "ts": time.time(), "tokens": toks, "chunks": chunks,
                       "ttft_s": ttft, "decode_tok_s": dec, "wall_s": dt, "pkg_j": ej,
                       "core_j": energy_delta(c0, c1), "idle_w": idle_w,
                       "gross_j_tok": ej / toks, "marg_j_tok": (ej - idle_w * dt) / toks,
                       "ring_mhz": statistics.median(s.ring) if s.ring else None,
                       "ring_target_mhz": (a.uncore_low_khz if arm == "L" else
                                           k.u_max0 if arm in ("U", "UC") else k.u_max_saved) / 1000,
                       "ring_idle_mhz": ring_idle,
                       "pod_cpu_mhz": statistics.median(s.mhz) if s.mhz else None,
                       "temp_max_c": max(s.temp) if s.temp else None,
                       "throttle_delta": (th1 - th0) if None not in (th0, th1) else None,
                       "pswpin": vm1["pswpin"] - vm0["pswpin"], "pswpout": vm1["pswpout"] - vm0["pswpout"],
                       "load1": l1, "others_busy": busy_frac(b0, b1, others), "pod_busy": busy_frac(b0, b1, cpus)}
                out.write(json.dumps(rec) + "\n"); out.flush()
                print(f"r{r} {arm:2} {dec or 0:6.2f} tok/s marg {rec['marg_j_tok']:.3f} J/tok "
                      f"ring {rec['ring_mhz']} core {rec['pod_cpu_mhz']} T {rec['temp_max_c']}", file=sys.stderr)
                if rec["temp_max_c"] and rec["temp_max_c"] >= a.temp_abort_c:
                    print("temperature guardrail: stopping", file=sys.stderr); break
    finally:
        k.restore()


def sign_conf(diffs):
    """1 - one-sided sign-test p, success = diff < 0 (callers orient the diffs)."""
    n = len(diffs)
    if n == 0:
        return None
    wins = sum(d < 0 for d in diffs)
    p = sum(comb(n, i) for i in range(wins, n + 1)) / 2 ** n
    return round(1 - p, 3)


def analyze(path, a):
    with open(path) as f:
        rows = [json.loads(line) for line in f]
    def bad(x):
        return (x["throttle_delta"] or 0) > 0 or x["pswpin"] > a.swap_flag_pages or not x["decode_tok_s"]
    ok = [x for x in rows if not bad(x)]
    print(f"cells {len(rows)}, valid {len(ok)}, flagged {len(rows) - len(ok)}")
    by = {arm: [x for x in ok if x["arm"] == arm] for arm in PRIMARY + ["L"]}
    def med(arm, k):
        v = [x[k] for x in by[arm] if x[k] is not None]
        return statistics.median(v) if v else None
    def spr(arm, k):
        v = [x[k] for x in by[arm] if x[k] is not None]
        return max(v) - min(v) if v else None
    for arm in PRIMARY + ["L"]:
        if by[arm]:
            print(f"{arm:2} n={len(by[arm])} tok/s {med(arm, 'decode_tok_s'):.2f} (spread {spr(arm, 'decode_tok_s'):.2f}) "
                  f"marg J/tok {med(arm, 'marg_j_tok'):.3f} (spread {spr(arm, 'marg_j_tok'):.3f}) "
                  f"gross J/tok {med(arm, 'gross_j_tok'):.3f} ring {med(arm, 'ring_mhz')} "
                  f"core {med(arm, 'pod_cpu_mhz')} load1 {med(arm, 'load1')} T {med(arm, 'temp_max_c')}")

    # Common baseline: every arm's marginal energy subtracts the stock arm's idle W of
    # the same round (fallback: median stock idle), never its own arm's idle.
    a_idle = {x["round"]: x["idle_w"] for x in by["A"] if x["idle_w"] is not None}
    a_idle_med = statistics.median(a_idle.values()) if a_idle else None
    for x in ok:
        base = a_idle.get(x["round"], a_idle_med)
        x["margc_j_tok"] = (x["pkg_j"] - base * x["wall_s"]) / x["tokens"] if base is not None else None
    for arm in PRIMARY + ["L"]:
        if by[arm]:
            print(f"{arm:2} margc J/tok {med(arm, 'margc_j_tok'):.3f} (spread {spr(arm, 'margc_j_tok'):.3f}) "
                  f"gross spread {spr(arm, 'gross_j_tok'):.3f} idle W {med(arm, 'idle_w'):.2f} "
                  f"ring idle {med(arm, 'ring_idle_mhz')}")

    def paired(x, y, k):
        rx = {r["round"]: r[k] for r in by[x]}
        ry = {r["round"]: r[k] for r in by[y]}
        return [rx[i] / ry[i] for i in sorted(rx) if i in ry]

    if any(len(by[arm]) < 4 for arm in PRIMARY):
        print("VERDICT INCONCLUSIVE: < 4 valid cells in a primary arm"); return
    a_noise = spr("A", "decode_tok_s") / med("A", "decode_tok_s")
    if a_noise > 0.10:
        print(f"VERDICT INCONCLUSIVE: baseline tok/s spread {a_noise:.1%} > 10%"); return
    if by["L"] and (med("L", "ring_mhz") or 1e9) > a.uncore_low_khz / 1000 + 100:
        print("VERDICT INCONCLUSIVE: uncore writes not honoured (L ring above target)"); return
    if (med("C", "pod_cpu_mhz") or 1e9) > a.core_cap_khz / 1000 + 100:
        print("VERDICT INCONCLUSIVE: core cap not honoured"); return
    if (med("A", "pod_cpu_mhz") or 0) < a.core_cap_khz / 1000 + 200:
        print("VERDICT INCONCLUSIVE: stock cores not above the cap (cap does not bind)"); return
    u_t = med("U", "ring_target_mhz")
    if u_t is not None and (med("U", "ring_mhz") or 0) < u_t - 100:
        print("VERDICT INCONCLUSIVE: uncore pin not honoured (U ring below target)"); return
    m = statistics.median
    uc_a_tok, uc_a_j = paired("UC", "A", "decode_tok_s"), paired("UC", "A", "margc_j_tok")
    uc_a_g = paired("UC", "A", "gross_j_tok")
    uc_c_tok, uc_c_j = paired("UC", "C", "decode_tok_s"), paired("UC", "C", "margc_j_tok")
    uc_c_g = paired("UC", "C", "gross_j_tok")
    c_a_tok, c_a_j = paired("C", "A", "decode_tok_s"), paired("C", "A", "margc_j_tok")
    c_a_g = paired("C", "A", "gross_j_tok")
    u_a_g = paired("U", "A", "gross_j_tok")
    u_a_tok = paired("U", "A", "decode_tok_s")
    l_a_tok = paired("L", "A", "decode_tok_s") if by["L"] else []
    print(f"UC/A tok/s {m(uc_a_tok):.3f}  margc J/tok {m(uc_a_j):.3f}  gross J/tok {m(uc_a_g):.3f}  "
          f"conf(gross lower) {sign_conf([r - 1 for r in uc_a_g])}  conf(margc lower) {sign_conf([r - 1 for r in uc_a_j])}")
    print(f"C/A  tok/s {m(c_a_tok):.3f}  margc J/tok {m(c_a_j):.3f}  gross J/tok {m(c_a_g):.3f}  "
          f"conf(gross lower) {sign_conf([r - 1 for r in c_a_g])}")
    print(f"UC/C tok/s {m(uc_c_tok):.3f}  margc J/tok {m(uc_c_j):.3f}  gross J/tok {m(uc_c_g):.3f}  "
          f"conf(UC faster) {sign_conf([1 - r for r in uc_c_tok])}  conf(UC gross lower) {sign_conf([r - 1 for r in uc_c_g])}")
    print(f"U/A  tok/s {m(u_a_tok):.3f}  gross J/tok {m(u_a_g):.3f}")
    if l_a_tok:
        print(f"L/A  tok/s {m(l_a_tok):.3f}  (ring on decode's critical path if <= 0.95)")
    def lower(ratios, cut):
        return m(ratios) <= cut and all(r < 1 for r in ratios)
    part1 = m(uc_a_tok) >= 0.97 and lower(uc_a_j, 0.90) and lower(uc_a_g, 0.90)
    part2 = (m(uc_c_tok) >= 1.03 and all(r > 1 for r in uc_c_tok)) \
        or (lower(uc_c_j, 0.95) and lower(uc_c_g, 0.95))
    c_alone = m(c_a_tok) >= 0.97 and lower(c_a_j, 0.90) and lower(c_a_g, 0.90)
    if part1 and part2:
        print("VERDICT SUPPORTED (H29 as stated: core cap + uncore pin)")
    elif part1:
        print("VERDICT REFUTED (split): core cap alone carries the saving; the uncore pin adds nothing")
    else:
        print("VERDICT REFUTED: UC does not hold tok/s within 3% at >= 10% lower J/token (gross and common-idle marginal)"
              + ("; core cap alone (C) does" if c_alone else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpus", default="0-15")
    ap.add_argument("--url", default="http://127.0.0.1:18029/v3")
    ap.add_argument("--model", default="Qwen3-4B-int4-ov")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=192)
    ap.add_argument("--core-cap-khz", type=int, default=2800000)
    ap.add_argument("--uncore-low-khz", type=int, default=2000000)
    ap.add_argument("--deadline-s", type=float, default=600)
    ap.add_argument("--temp-abort-c", type=float, default=95)
    ap.add_argument("--swap-flag-pages", type=int, default=25600)  # 100 MiB of 4 KiB pages
    ap.add_argument("--out")
    ap.add_argument("--write-log")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze")
    a = ap.parse_args()
    if a.analyze:
        analyze(a.analyze, a); return
    if not a.dry_run and (os.geteuid() != 0 or not a.out):
        sys.exit("measure needs root and --out")
    measure(a)


if __name__ == "__main__":
    main()

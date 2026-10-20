"""Within-hold interleaved pairs (EXPERIMENTS.md, "H47 and H49 pre-registration").

Both arms are live in one hold. A pair is one task id run once on each arm, one attempt in
flight at a time; pair j runs A then B when j is even and B then A when j is odd, over the
task set's fixed order. Each attempt is sampled over its own window (send to answer): host
busy CPU-s/s over every CPU, each arm pod's cgroup CPU-s/s, package energy, load1, throttle,
swap and E-core busy. Background CPU (bg_cpu) is the host's busy CPU-s/s minus the arm pods'.

Load rule L1 (H47, H49): a pair is matched when its two attempts' bg_cpu differ by at most
0.8 CPU-s/s; over the matched pairs, the arms' mean bg_cpu and mean swap used must be within
0.8 CPU-s/s and 256 MiB.

Load rule L2 (H47b, H49b, EXPERIMENTS.md "H47b and H49b pre-registration"): before each
attempt, with both arms idle, `load.py settle` then a pre-window. A pair is matched when its
two pre-windows agree on every amendment-27 pre covariate within load.TOLERANCE and both show
the level's full load. The arm-level check is amendment 27 (load.matched_a27) over the matched
pairs' pre-windows. bg_cpu, package W and throttle during the attempt are reported, never
gated.

Load rule L3 (H50, H50o, H53; EXPERIMENTS.md "Crescent amendment A3"): the same
pre-windows, no match filter. Every complete pair enters the statistic; a balance gate (a
sign-flip test of arm on each pre covariate, p < 0.01 gates) replaces it; a two-regressor
adjustment and L2's matched subset are reported, never gated. The arm order of pair j is drawn
from the registered seed (order), and each pairs directory carries one registration.json
(register). Python 3 standard library only.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "load"))
import energy  # noqa: E402
import load as LOAD  # noqa: E402
import proxy  # noqa: E402
import stats  # noqa: E402
import tasks as T  # noqa: E402

PAIR_GUARD_S = 600.0        # a pair starts only with this much of the hold left
TASK_CAP_S = 240.0          # per attempt, both arms
TURN_S = proxy.TIMEOUT_S    # the proxy's turn timeout (120 s)
L1_BG_CPU = 0.8             # CPU-s/s: 5 pp of the 16 load CPUs
ARM_SWAP_MIB = 256.0
IDLE_CPU = 0.5              # median idle-arm pod CPU above this: arms not independent
MIN_MATCHED = 16
MIN_MATCHED_FRAC = 0.75
MAX_VOID = 2                # H49
PRE_S = 10.0                # rule L2 pre-window: one throttle event reads 6/min, under TOLERANCE abs 10
PRE_COVARIATES = LOAD.COVARIATES["pre"]   # busy_pct, throttle_per_min, swap_used_mib, swap_delta_mib
OUTCOMES = ("pass_budget", "fail_graded", "over_turn", "over_task", "void")


def order(j, a, b, seed=None):
    """Arms of pair j in run order. No seed: A B for even j, B A for odd j (A B B A ...), as
    H47-H49b ran. A seed (rule L3): A first or B first by a coin drawn from Random(f"{seed}:{j}"),
    a pure function of (seed, j), so a resumed hold draws the same order."""
    if seed is None:
        return (a, b) if j % 2 == 0 else (b, a)
    import random
    return (a, b) if random.Random(f"{seed}:{j}").random() < 0.5 else (b, a)


def can_start(now, deadline, guard=PAIR_GUARD_S):
    return deadline - now >= guard


# ---- per-attempt sample -----------------------------------------------------------

def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def busy_jiffies(stat_text, cpus=None):
    """Busy jiffies summed over every cpuN line (or only `cpus`): user..steal minus idle+iowait."""
    busy = 0
    for line in stat_text.splitlines():
        f = line.split()
        if not f or not f[0].startswith("cpu") or f[0] == "cpu":
            continue
        if cpus is not None and int(f[0][3:]) not in cpus:
            continue
        v = [int(x) for x in f[1:9]]
        busy += sum(v) - v[3] - v[4]
    return busy


def total_jiffies(stat_text, cpus):
    tot = 0
    for line in stat_text.splitlines():
        f = line.split()
        if f and f[0].startswith("cpu") and f[0] != "cpu" and int(f[0][3:]) in cpus:
            tot += sum(int(x) for x in f[1:9])
    return tot


def cgroup_usage_usec(path):
    """cpu.stat usage_usec of a cgroup v2 directory (None when unreadable)."""
    for line in _read(os.path.join(path, "cpu.stat")).splitlines():
        k, _, v = line.partition(" ")
        if k == "usage_usec" and v.strip().isdigit():
            return int(v)
    return None


def find_pod_cgroup(uid, root="/sys/fs/cgroup", max_depth=8):
    """The cgroup v2 directory of a pod: pod<uid> (cgroupfs driver) or
    ...-pod<uid_with_underscores>.slice (systemd driver), searched under root. None if absent."""
    names = (f"pod{uid}", f"pod{uid.replace('-', '_')}.slice")
    base = root.rstrip("/").count("/")
    for d, subs, _ in os.walk(root):
        if d.rstrip("/").count("/") - base >= max_depth:
            subs[:] = []
            continue
        for s in subs:
            if s in names or any(s.endswith("-" + n) for n in names):
                return os.path.join(d, s)
    return None


class Probe:
    """Snapshots of the host and the arm pods; window() turns two snapshots into metrics.

    pods: {arm: cgroup dir}; two arms may share one dir (one pod serving both arms)."""

    def __init__(self, pods, ecpus, proc="/proc", sysfs="/sys", rapl=True, clock=time.time):
        self.pods, self.ecpus, self.proc, self.sysfs, self.clock = pods, set(ecpus), proc, sysfs, clock
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        zones = energy.rapl_domains(sysfs)["package"] if rapl else []
        self.zones = zones
        self.ranges = [int(_read(f"{z}/max_energy_range_uj").strip() or 0) for z in zones]

    def snap(self):
        st = _read(f"{self.proc}/stat")
        mem = _read(f"{self.proc}/meminfo")
        kv = {}
        for line in mem.splitlines():
            k, _, v = line.partition(":")
            kv[k] = int(v.split()[0]) if v.split() else 0
        thr = sum(int(_read(p).strip() or 0) for p in
                  glob.glob(f"{self.sysfs}/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count"))
        return {"t": self.clock(), "busy": busy_jiffies(st),
                "ebusy": busy_jiffies(st, self.ecpus), "etot": total_jiffies(st, self.ecpus),
                "pods": {p: cgroup_usage_usec(p) for p in set(self.pods.values())},
                "uj": [energy.read_uj(z) for z in self.zones],
                "swap": (kv.get("SwapTotal", 0) - kv.get("SwapFree", 0)) / 1024, "thr": thr,
                "load1": float((_read(f"{self.proc}/loadavg").split() or ["0"])[0])}

    def window(self, s0, s1):
        return window(s0, s1, self.pods, self.hz, self.ranges)


def window(s0, s1, pods, hz, ranges):
    """Metrics of one attempt window. pod_cpu is per arm; bg_cpu counts a shared pod once."""
    dt = s1["t"] - s0["t"]
    if dt <= 0:
        return {"seconds": dt, "bg_cpu": None}
    host = (s1["busy"] - s0["busy"]) / hz / dt
    per_dir = {}
    for p in set(pods.values()):
        a, b = s0["pods"].get(p), s1["pods"].get(p)
        per_dir[p] = None if a is None or b is None else (b - a) / 1e6 / dt
    pod_cpu = {arm: per_dir[p] for arm, p in pods.items()}
    bg = None if any(v is None for v in per_dir.values()) else host - sum(per_dir.values())
    de = [energy.delta_uj(a, b, r) for a, b, r in zip(s0["uj"], s1["uj"], ranges)]
    pkg_j = None if not de or any(d is None for d in de) else sum(de) / 1e6
    etot = s1["etot"] - s0["etot"]
    return {"seconds": round(dt, 3), "host_cpu": round(host, 4),
            "pod_cpu": {k: (round(v, 4) if v is not None else None) for k, v in pod_cpu.items()},
            "bg_cpu": round(bg, 4) if bg is not None else None,
            "ecore_busy_pct": round(100 * (s1["ebusy"] - s0["ebusy"]) / etot, 2) if etot > 0 else None,
            "pkg_j": round(pkg_j, 3) if pkg_j is not None else None,
            "pkg_w": round(pkg_j / dt, 2) if pkg_j is not None else None,
            "load1": s1["load1"], "throttle_per_min": round((s1["thr"] - s0["thr"]) * 60 / dt, 2),
            "swap_used_mib": round(s1["swap"], 1),
            "swap_growth_mib_per_min": round((s1["swap"] - s0["swap"]) * 60 / dt, 2)}


# ---- outcome of one attempt -------------------------------------------------------

def turn_over(rec, turn_s=TURN_S):
    err = (rec.get("error") or "").lower()
    return "timed out" in err or "timeout" in err or (rec.get("e2e_s") or 0) > turn_s


def outcome(graded_pass, ask_err, duration_s, recs, cap_s=TASK_CAP_S, turn_s=TURN_S, void_reason=None):
    """One of OUTCOMES. A turn over the proxy timeout wins over the cap: the task then
    failed to deliver within the turn budget, whatever happened after. Any other agent
    error inside both budgets is a graded failure of the arm, not missing data."""
    if void_reason:
        return "void"
    if any(turn_over(r, turn_s) for r in recs):
        return "over_turn"
    if duration_s >= cap_s or (ask_err and "timed out" in ask_err.lower()):
        return "over_task"
    return "pass_budget" if graded_pass else "fail_graded"


# ---- one hold -----------------------------------------------------------------------

class HoldAborted(Exception):
    pass


def run_cmd(cmd, timeout=300):
    """Run a shell-free command; (ok, message)."""
    try:
        p = subprocess.run(shlex.split(cmd), capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"did not finish in {timeout} s"
    except OSError as e:
        return False, str(e)
    tail = (p.stderr or b"").decode(errors="replace").strip()[-300:]
    return p.returncode == 0, f"exit {p.returncode}: {tail}" if p.returncode else ""


def read_jsonl(path):
    """The records of a JSON-lines file; a last line without its newline (a record still being
    appended by a live hold) is not read."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        text = f.read()
    return [json.loads(x) for x in text.split("\n")[:-1] if x.strip()]


def append(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def next_pair(out):
    """First pair index not yet recorded in pairs.jsonl (complete or void): pairs resume
    across holds and never repeat."""
    done = [p["j"] for p in read_jsonl(os.path.join(out, "pairs.jsonl"))]
    return max(done) + 1 if done else 0


def attempt(ab, arms, arm, task, j, hold, probe, out, mcp_url, cap_s=TASK_CAP_S, settle_s=30.0,
            idle_s=0.0, clock=time.time, opener=urllib.request.urlopen, quiesce_s=0.0, pre=None):
    """One scored attempt of `task` on `arm`. arms[arm] = {"agent", "proxy_log", "restart", "entity"}
    (entity: a fixed DevUI entity id, or None to resolve it from /v1/entities).
    idle_s > 0: before the attempt, sample the (shared) pod with no request in flight; that
    reading is the attempt's idle_cpu. Else idle_cpu is the other arm's pod CPU in-window.
    pre(arm) (rule L2): called first, with both arms idle; returns the settle gap's pre-window,
    stored with both pods' CPU over it."""
    me = arms[arm]
    pre_w = None
    if pre is not None:
        p0, pt0 = probe.snap(), clock()
        pre_w = dict(pre(arm), t_start=pt0)
        pre_w.update(t_end=clock(), pod_cpu=probe.window(p0, probe.snap()).get("pod_cpu"))
    idle_cpu = None
    if idle_s > 0:
        i0 = probe.snap()
        time.sleep(idle_s)
        w = probe.window(i0, probe.snap())
        idle_cpu = w["pod_cpu"].get(arm)
    prompt = task["prompt"]
    s0 = probe.snap()
    t0 = clock()
    try:
        answer, status, err = me["agent"].ask(prompt, timeout=cap_s)
    except (OSError, ValueError, urllib.error.URLError) as e:   # entity lookup before the send
        answer, status, err = None, None, f"{type(e).__name__}: {e}"
    t1 = clock()
    s1 = probe.snap()
    win = probe.window(s0, s1)
    if idle_s <= 0:
        other = [a for a in arms if a != arm]
        idle_cpu = win["pod_cpu"].get(other[0]) if other else None
    dur = t1 - t0
    recs = ab.wait_records(me["proxy_log"], prompt, t0, t1, err is None, settle_s, clock=clock)
    users = ab.memory_users(recs)
    if users is not None and users > 1:
        raise HoldAborted(f"agent memory is on: {task['id']} on {arm} carried {users} user messages")
    calls = ab.flat_calls(recs)
    end, void_reason = None, None
    if err and ("connection refused" in err.lower() or "http error 404" in err.lower()):
        # The agent's endpoint or entity is gone: a harness fault, not the arm's answer.
        void_reason = f"agent not reachable: {err}"
    if mcp_url and task.get("end_state"):
        try:
            end = ab.mcp_state(mcp_url, task, opener)
        except (OSError, ValueError, urllib.error.URLError) as e:
            void_reason = void_reason or f"end-state read failed: {e}"
    graded, source = ab.graded_answer(answer, recs)
    ok, reason = False, err
    if err is None and void_reason is None:
        ok, reason = T.grader_for(task)(task, graded, calls, end, evidence=ab.evidence_text(recs))
    kind = outcome(ok, err, dur, recs, cap_s, void_reason=void_reason)
    row = {"hold": hold, "j": j, "task": task["id"], "arm": arm, "outcome": kind, "passed": bool(ok),
           "reason": void_reason or reason, "t_start": t0, "t_end": t1, "duration_s": round(dur, 3),
           "error": err, "response_status": status, "answer": (answer or "")[:2000], "answer_source": source,
           "llm_requests": len(recs), "user_msgs_max": users,
           "turns_s": [None if r.get("error") else r.get("e2e_s") for r in recs], "turn_over": sum(turn_over(r) for r in recs),
           "end_state": end, "calls": [{k: c.get(k) for k in ("name", "valid", "reason")} for c in calls],
           "idle_cpu": idle_cpu, "window": win}
    if pre is not None:
        row["pre"] = pre_w
    with open(os.path.join(out, "turns.jsonl"), "a") as f:
        for r in recs:
            f.write(json.dumps(dict(r, task=task["id"], arm=arm, j=j, hold=hold)) + "\n")
    append(os.path.join(out, "attempts.jsonl"), row)
    if kind == "over_task" and me.get("restart"):
        # The agent may still be working on the abandoned task: restart it so nothing of
        # this attempt runs into the next one.
        ok_r, msg = run_cmd(me["restart"])
        if not ok_r:
            raise HoldAborted(f"agent restart of {arm} after the cap failed: {msg}")
        # A restarted DevUI registers its entity under a new id: resolve it again.
        me["agent"].entity = me.get("entity")
    if kind in ("over_task", "over_turn") and quiesce_s > 0:
        waited, settled = quiesce(probe, arm, quiesce_s)
        append(os.path.join(out, "quiesce.jsonl"), {"hold": hold, "j": j, "arm": arm, "after": kind,
                                                     "waited_s": waited, "settled": settled})
    moved = end is not None and end != ab.seed_state(task)
    return row, (task.get("mutating") or moved or kind == "over_task")


def quiesce(probe, arm, limit_s, step_s=1.0, below=IDLE_CPU):
    """After an abandoned attempt (over_turn, over_task) the arm's server may still be
    decoding it: wait, up to limit_s, until its pod reads <= `below` CPU over a step_s
    window. Returns (seconds waited, settled)."""
    t0 = time.monotonic()
    while True:
        s0 = probe.snap()
        time.sleep(step_s)
        cpu = probe.window(s0, probe.snap())["pod_cpu"].get(arm)
        waited = time.monotonic() - t0
        if cpu is not None and cpu <= below:
            return round(waited, 1), True
        if waited >= limit_s:
            return round(waited, 1), False


def run_hold(ab, arms, a, b, task_list, out, deadline, probe, mcp_url, reset, hold, cap_s=TASK_CAP_S,
             guard_s=PAIR_GUARD_S, settle_s=30.0, idle_s=0.0, warmup=None, clock=time.time,
             opener=urllib.request.urlopen, log=lambda m: None, quiesce_s=0.0, warmup_timeout_s=600.0, pre=None,
             seed=None):
    """Pairs until the task list ends or the hold leaves less than guard_s. Returns the
    number of complete pairs written. Every pair, complete or void, goes to pairs.jsonl."""
    os.makedirs(out, exist_ok=True)
    if warmup:
        for arm in (a, b):
            w0 = clock()
            try:
                ans, _, err = arms[arm]["agent"].ask(warmup, timeout=warmup_timeout_s)
            except (OSError, ValueError, urllib.error.URLError) as e:
                ans, err = None, f"{type(e).__name__}: {e}"
            append(os.path.join(out, "warmups.jsonl"), {"hold": hold, "arm": arm, "seconds": round(clock() - w0, 3),
                                                        "error": err, "answer": (ans or "")[:300]})
            if err:
                raise HoldAborted(f"warm-up of {arm} failed: {err}")
    done = 0
    j = next_pair(out)
    while j < len(task_list):
        if not can_start(clock(), deadline, guard_s):
            log(f"pair {j}: {deadline - clock():.0f} s left < {guard_s:.0f} s, hold ends")
            break
        t = task_list[j]
        rows, void = [], None
        try:
            for arm in order(j, a, b, seed):
                row, needs_reset = attempt(ab, arms, arm, t, j, hold, probe, out, mcp_url, cap_s, settle_s,
                                           idle_s, clock, opener, quiesce_s, pre)
                rows.append(row)
                if row["outcome"] == "void":
                    void = row["reason"]
                if needs_reset and reset:
                    try:
                        ab.reset_sidecar(reset, mcp_url, t, opener)
                    except ab.ResetFailed as e:
                        void = f"reset after {arm} failed: {e}"
                        raise HoldAborted(void)
                if void:
                    break
        except HoldAborted as e:
            append(os.path.join(out, "pairs.jsonl"), {"j": j, "task": t["id"], "hold": hold, "status": "void",
                                                       "void_reason": void or str(e), "order": list(order(j, a, b, seed))})
            raise
        if clock() > deadline:
            void = void or "pair ran past the hold deadline"
        append(os.path.join(out, "pairs.jsonl"), {"j": j, "task": t["id"], "hold": hold,
                                                   "status": "void" if void else "complete", "void_reason": void,
                                                   "order": list(order(j, a, b, seed))})
        log(f"pair {j} {t['id']}: " + ", ".join(f"{r['arm']} {r['outcome']} {r['duration_s']:.1f}s" for r in rows)
            + (f" VOID ({void})" if void else ""))
        done += 0 if void else 1
        j += 1
    return done


# ---- analysis -----------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def pair_table(attempts, pairs, a, b):
    """{j: {"task", a: row, b: row}} for complete pairs with both attempts present, and the
    void count (void pairs plus pairs whose second attempt is missing, e.g. a killed hold)."""
    by = {}
    for r in attempts:
        by.setdefault(r["j"], {})[r["arm"]] = r
    status = {p["j"]: p for p in pairs}
    table, void = {}, 0
    for j in sorted(set(by) | set(status)):
        p = status.get(j)
        rs = by.get(j, {})
        if p is None or p["status"] != "complete" or a not in rs or b not in rs:
            void += 1
            continue
        table[j] = {"task": p["task"], a: rs[a], b: rs[b], "first": (p.get("order") or [None])[0]}
    return table, void


def l1(table, a, b, tol=L1_BG_CPU, swap_tol=ARM_SWAP_MIB):
    """Rule L1. Returns {matched: [j], unmatched: [j], arm_bg, arm_swap, arm_ok, reasons}."""
    m, u = [], []
    for j, p in table.items():
        x, y = p[a]["window"].get("bg_cpu"), p[b]["window"].get("bg_cpu")
        (m if x is not None and y is not None and abs(x - y) <= tol else u).append(j)
    bg = {arm: _mean([table[j][arm]["window"]["bg_cpu"] for j in m]) for arm in (a, b)}
    sw = {arm: _mean([table[j][arm]["window"]["swap_used_mib"] for j in m
                      if table[j][arm]["window"].get("swap_used_mib") is not None]) for arm in (a, b)}
    reasons = []
    if m:
        if abs(bg[a] - bg[b]) > tol:
            reasons.append(f"mean bg_cpu spread {abs(bg[a] - bg[b]):.3f} > {tol}")
        if sw[a] is None or sw[b] is None:
            reasons.append("swap used unmeasured")
        elif abs(sw[a] - sw[b]) > swap_tol:
            reasons.append(f"mean swap used spread {abs(sw[a] - sw[b]):.1f} MiB > {swap_tol:.0f}")
    return {"matched": m, "unmatched": u, "arm_bg_cpu": bg, "arm_swap_used_mib": sw,
            "arm_ok": bool(m) and not reasons, "reasons": reasons}


def pre_mismatch(x, y):
    """Why two pre-windows do not match under rule L2 (None when they match): the first of a
    missing pre-window, a window with fewer load containers than its level runs (or no count),
    or a pre covariate outside load.TOLERANCE (limit max(abs, rel x the larger value))."""
    if not x or not y:
        return "pre-window missing"
    for w in (x, y):
        if w.get("level") != "none" and (w.get("load_running") is None or w.get("load_expected") is None
                                         or w["load_running"] < w["load_expected"]):
            return "load missing"
    for m in PRE_COVARIATES:
        u, v = x.get(m), y.get(m)
        if u is None or v is None:
            return m
        tol = LOAD.TOLERANCE[m]
        if abs(u - v) > max(tol["abs"], tol["rel"] * max(abs(u), abs(v))):
            return m
    return None


def l2(table, a, b):
    """Rule L2. Per pair: the two attempts' pre-windows (both arms idle, after the settle gap)
    match on every amendment-27 pre covariate. Arm level: load.matched_a27 over the matched
    pairs' pre-windows. During-attempt load never enters."""
    m, u, by = [], [], {}
    for j, p in table.items():
        why = pre_mismatch(p[a].get("pre"), p[b].get("pre"))
        if why is None:
            m.append(j)
        else:
            u.append(j)
            by[why] = by.get(why, 0) + 1
    reasons, check = [], None
    if m:
        ws = [dict(table[j][arm]["pre"], tag=arm, phase="pre") for j in m for arm in (a, b)]
        check = LOAD.matched_a27(ws)
        if not check["matched"]:
            reasons.append(check["verdict"])
    return {"matched": m, "unmatched": u, "unmatched_by": by, "arm_check": check,
            "arm_ok": bool(m) and not reasons, "reasons": reasons}


def per_arm_load(table, js, arm, key="window"):
    """Recorded, not gated: means over the arm's attempts in pairs js (key "pre": the pre-windows)."""
    ws = [table[j][arm][key] for j in js if table[j][arm].get(key)]
    out = {}
    for k in ("bg_cpu", "host_cpu", "swap_used_mib", "swap_growth_mib_per_min", "load1", "pkg_w",
              "throttle_per_min", "ecore_busy_pct", "busy_pct", "swap_delta_mib", "settle_s"):
        v = [w[k] for w in ws if w.get(k) is not None]
        out[k] = round(_mean(v), 3) if v else None
    return out


def boot_share(d, pred, boot=stats.BOOT, seed=stats.SEED, stat=None):
    """Share of paired bootstrap resamples (seed 40) of stat(d) satisfying pred: the
    derived confidence of a verdict side."""
    import random
    if not d:
        return None
    stat = stat or (lambda xs: sum(xs) / len(xs))
    rng = random.Random(seed)
    n = len(d)
    return sum(1 for _ in range(boot) if pred(stat([d[rng.randrange(n)] for _ in range(n)]))) / boot


# Rule L3 rows: the seed of their arm order, fixed in the pre-registration (amendment A3).
ORDER_SEEDS = {"H50": "H50-crescent-2026-10-20", "H50o": "H50o-crescent-2026-10-20",
               "H53": "H53-crescent-2026-10-20"}

# Hypothesis id -> (design, load rule). H47b and H49b are H47 and H49 under rule L2; H50 is
# H49b's design on the Xeon 6741P, H50o the same with arm B3 (EXPERIMENTS.md "H50-H52 pre-registration");
# H53 the H49 statistic over solo-hold block pairs on the Arc Pro B70s (solo.py; refused until H53 is pre-registered).
DESIGNS = {"H47": ("H47", "L1"), "H49": ("H49", "L1"), "H47b": ("H47", "L2"), "H49b": ("H49", "L2"),
           "H50": ("H49", "L3"), "H50o": ("H49", "L3"), "H53": ("H49", "L3")}


# The ledger a rule-L3 row must be pre-registered in before any of its holds runs.
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "EXPERIMENTS.md")

# Solo-hold rows: hold h runs ARM_SEQUENCE[hyp][(h - 1) % len] (block k = holds 2k-1, 2k).
ARM_SEQUENCE = {"H53": ("AG", "B2", "B2", "AG")}


def preregistered(hyp, ledger=None):
    """True when the ledger's hypotheses table has a row for hyp (a line starting "| hyp |")."""
    try:
        with open(ledger or LEDGER) as f:
            text = f.read()
    except OSError:
        return False
    return re.search(rf"^\| {re.escape(hyp)} \|", text, re.M) is not None


def design(hyp):
    """(design, load rule) of a pairs hypothesis id. A rule-L3 row is refused until EXPERIMENTS.md
    carries its pre-registered row: no hold, and no score, before the protocol is on main."""
    if hyp not in DESIGNS:
        raise ValueError(f"{hyp}: not a pairs hypothesis ({', '.join(DESIGNS)})")
    if DESIGNS[hyp][1] == "L3" and not preregistered(hyp):
        raise ValueError(f"{hyp}: no '| {hyp} |' row in {os.path.normpath(LEDGER)}; pre-register it first")
    return DESIGNS[hyp]


def expected_arm(hyp, hold):
    """The arm hold number `hold` (1, 2, ...) of a solo-hold row must run."""
    seq = ARM_SEQUENCE[hyp]
    if hold < 1:
        raise ValueError(f"{hyp}: hold numbers start at 1, not {hold}")
    return seq[(hold - 1) % len(seq)]


def register(out, hyp):
    """The registration of a rule-L3 pairs directory: {"hyp", "rule", "seed"}, written once by
    the first hold (under the host lock): a temp file, then os.link to registration.json, which
    fails if the name exists, so the file appears whole or not at all. Every later hold must find
    the same one, or the hold stops. None for an L1 or L2 row (no registration)."""
    fam, rule = design(hyp)
    if rule != "L3":
        return None
    want = {"hyp": hyp, "rule": rule, "seed": ORDER_SEEDS[hyp]}
    path = os.path.join(out, "registration.json")
    os.makedirs(out, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(want) + "\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)
        return want
    except FileExistsError:
        try:
            with open(path) as f:
                have = json.load(f)
        except ValueError as e:
            raise HoldAborted(f"{path} is not a registration ({e}); inspect it, do not delete it blind")
        if have != want:
            raise HoldAborted(f"{path} registers {have}, this hold is {want}")
        return have
    finally:
        os.unlink(tmp)


def sign_flip_p(d, n_draw=10000, seed=stats.SEED):
    """Two-sided sign-flip (randomization) p of mean(d) = 0: every |d| keeps its size, its
    sign is drawn at random; p = (count |mean| >= |observed| + 1) / (draws + 1)."""
    import random
    d = [x for x in d if x is not None]
    if len(d) < 2:
        return None
    obs = abs(sum(d) / len(d))
    if obs == 0:
        return 1.0
    rng = random.Random(seed)
    hit = sum(1 for _ in range(n_draw)
              if abs(sum(x if rng.random() < 0.5 else -x for x in d) / len(d)) >= obs - 1e-12)
    return (hit + 1) / (n_draw + 1)


def _ols(rows):
    """Least squares y ~ 1 + x1 + x2 over [(y, x1, x2)]: [b0, b1, b2], or None if singular."""
    xs = [(1.0, x1, x2) for _, x1, x2 in rows]
    ys = [y for y, _, _ in rows]
    a = [[sum(r[i] * r[k] for r in xs) for k in range(3)] for i in range(3)]
    v = [sum(r[i] * y for r, y in zip(xs, ys)) for i in range(3)]
    for c in range(3):                                   # Gauss-Jordan with partial pivoting
        piv = max(range(c, 3), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv], v[c], v[piv] = a[piv], a[c], v[piv], v[c]
        for r in range(3):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
                v[r] -= f * v[c]
    return [v[i] / a[i][i] for i in range(3)]


def l3(table, a, b, alpha=0.01, boot=stats.BOOT, seed=stats.SEED):
    """Rule L3 over every complete pair. balance: per pre covariate, the sign-flip p of
    pre(a) - pre(b) (throttle on log1p); balance_ok when every p >= alpha. adjusted (reported, never gated):
    dy = ok(a) - ok(b) ~ 1 + dlog1p(throttle/min) + dbusy, intercept with a paired bootstrap CI.
    sign_flip_p: of dy itself."""
    import math
    import random
    js = sorted(table)
    bal = {}
    for m in PRE_COVARIATES:
        # throttle_per_min is heavy-tailed (12-3342/min on crescent): tested on log1p, as adjusted.
        f = math.log1p if m == "throttle_per_min" else (lambda v: v)
        d = [(table[j][a].get("pre") or {}).get(m) for j in js]
        e = [(table[j][b].get("pre") or {}).get(m) for j in js]
        dd = [f(x) - f(y) for x, y in zip(d, e) if x is not None and y is not None]
        bal[m] = {"n": len(dd), "mean_diff": round(sum(dd) / len(dd), 4) if dd else None, "p": sign_flip_p(dd)}
    bad = [m for m, v in bal.items() if v["p"] is not None and v["p"] < alpha]
    dy = {j: int(table[j][a]["outcome"] == "pass_budget") - int(table[j][b]["outcome"] == "pass_budget") for j in js}
    rows = []
    for j in js:
        pa, pb = table[j][a].get("pre") or {}, table[j][b].get("pre") or {}
        if None in (pa.get("throttle_per_min"), pb.get("throttle_per_min"), pa.get("busy_pct"), pb.get("busy_pct")):
            continue
        rows.append((dy[j], math.log1p(pa["throttle_per_min"]) - math.log1p(pb["throttle_per_min"]),
                     pa["busy_pct"] - pb["busy_pct"]))
    adj = {"model": "d(pass) ~ 1 + d(log1p throttle/min) + d(busy_pct), pre-windows", "n": len(rows),
           "intercept": None, "ci95": [None, None]}
    fit = _ols(rows) if len(rows) >= 4 else None
    if fit:
        rng = random.Random(seed)
        bs = []
        for _ in range(boot):
            f = _ols([rows[rng.randrange(len(rows))] for _ in rows])
            if f:
                bs.append(f[0])
        bs.sort()
        adj.update(intercept=round(fit[0], 4), slopes=[round(fit[1], 4), round(fit[2], 4)],
                   ci95=[round(bs[int(0.025 * len(bs))], 4), round(bs[int(0.975 * len(bs)) - 1], 4)] if bs else [None, None])
    return {"matched": js, "unmatched": [], "balance": bal, "balance_ok": not bad, "balance_failed": bad,
            "adjusted": adj, "sign_flip_p": sign_flip_p(list(dy.values()))}


def analyze(attempts, pairs, hyp, a, b, holds=None, registration=None, solo=False):
    """The pre-registered statistics and verdict of H47 (a=A1, b=A4) or H49 (a=A1, b=B1), under
    load rule L1; H47b and H49b are those designs under rule L2; H50, H50o and H53 (its block
    pairs, solo.py) the H49 design under rule L3, which needs the directory's registration.
    solo: block pairs of solo holds, where no second server is live, so the idle-arm check does
    not apply and is not gated (it is reported as not applicable, never as a synthesized 0)."""
    base, rule_name = design(hyp)
    if rule_name == "L3":
        want = {"hyp": hyp, "rule": "L3", "seed": ORDER_SEEDS[hyp]}
        if registration != want:
            raise ValueError(f"{hyp}: registration {registration} is not {want}")
    table, void = pair_table(attempts, pairs, a, b)
    rule = {"L1": l1, "L2": l2, "L3": l3}[rule_name](table, a, b)
    m = rule["matched"]
    run = len(table)
    idle = [table[j][arm]["idle_cpu"] for j in table for arm in (a, b) if table[j][arm].get("idle_cpu") is not None]
    idle_med = stats.median(idle)
    gates = []
    if rule_name == "L3":
        if len(m) < MIN_MATCHED:
            gates.append(f"{len(m)} complete pairs < {MIN_MATCHED}")
        if not rule["balance_ok"]:
            gates.append("covariate tracks arm: " + ", ".join(rule["balance_failed"]))
    else:
        if len(m) < MIN_MATCHED:
            gates.append(f"{len(m)} matched pairs < {MIN_MATCHED}")
        if run and len(m) / run < MIN_MATCHED_FRAC:
            gates.append(f"{len(m)}/{run} pairs matched < {MIN_MATCHED_FRAC:.0%}")
        if m and not rule["arm_ok"]:
            gates.append("arm-level load check failed: " + "; ".join(rule["reasons"]))
    if not run:
        gates.append("no complete pair")
    if solo:
        idle_med = "not applicable: solo holds"
    elif idle_med is None or idle_med > IDLE_CPU:
        gates.append(f"idle-arm pod CPU median {idle_med} > {IDLE_CPU}" if idle_med is not None
                     else "idle-arm pod CPU unmeasured")
    if base == "H49" and void > MAX_VOID:
        gates.append(f"{void} void pairs > {MAX_VOID}")
    counts = {arm: {o: sum(1 for j in table if table[j][arm]["outcome"] == o) for o in OUTCOMES}
              for arm in (a, b)}
    res = {"hypothesis": hyp, "rule": rule_name, "arms": [a, b], "holds": holds, "pairs_run": run, "void_pairs": void,
           "matched": len(m), "unmatched": len(rule["unmatched"]), "unmatched_ids": [table[j]["task"] for j in rule["unmatched"]],
           "load": {k: rule[k] for k in rule if k not in ("matched", "unmatched")},
           "load_per_arm": {arm: per_arm_load(table, m, arm) for arm in (a, b)},
           "idle_cpu_median": idle_med, "outcomes_all_run_pairs": counts, "gates": gates,
           "per_pair": [{"j": j, "task": table[j]["task"], "matched": j in m,
                         **{arm: {k: table[j][arm][k] for k in ("outcome", "duration_s", "idle_cpu")}
                            | {"bg_cpu": table[j][arm]["window"].get("bg_cpu"),
                               "pkg_j": table[j][arm]["window"].get("pkg_j"),
                               "swap_used_mib": table[j][arm]["window"].get("swap_used_mib")}
                            for arm in (a, b)}} for j in sorted(table)]}
    if rule_name == "L3":
        # Secondary, never a gate: rule L2's matched subset and its paired difference.
        l2r = l2(table, a, b)
        okm = {arm: {table[j]["task"]: int(table[j][arm]["outcome"] == "pass_budget") for j in l2r["matched"]}
               for arm in (a, b)}
        res["l2_secondary"] = {"matched": len(l2r["matched"]), "unmatched_by": l2r["unmatched_by"],
                               "paired_diff": stats.paired_diff(okm[a], okm[b]) if l2r["matched"] else None}
        # Secondary, never a gate (amendment A3 review S2): the period-balanced crossover
        # estimate, the mean of d = ok(a) - ok(b) over a-first pairs and over b-first pairs, averaged.
        per = {}
        for first in (a, b):
            ds = [int(table[j][a]["outcome"] == "pass_budget") - int(table[j][b]["outcome"] == "pass_budget")
                  for j in m if table[j].get("first") == first]
            per[f"{first}_first"] = {"n": len(ds), "mean_d": round(sum(ds) / len(ds), 4) if ds else None}
        both = [v["mean_d"] for v in per.values()]
        res["period_balanced"] = dict(per, estimate=round(sum(both) / 2, 4) if None not in both else None)
    if rule_name in ("L2", "L3"):
        # Outcomes, never gates: during-attempt load (bg_cpu, package W, throttle) on the matched
        # pairs and on every pair run; the pre-windows on the matched pairs.
        res["load_during_per_arm"] = res["load_per_arm"]
        res["load_during_all_pairs_per_arm"] = {arm: per_arm_load(table, sorted(table), arm) for arm in (a, b)}
        res["load_pre_per_arm"] = {arm: per_arm_load(table, m, arm, "pre") for arm in (a, b)}
        for row in res["per_pair"]:
            for arm in (a, b):
                p = table[row["j"]][arm].get("pre") or {}
                row[arm]["pre"] = {k: p.get(k) for k in PRE_COVARIATES + ("settled", "settle_s", "load_running")}
    ok = {arm: {table[j]["task"]: int(table[j][arm]["outcome"] == "pass_budget") for j in m} for arm in (a, b)}
    succ = {}
    for arm in (a, b):
        k, n = sum(ok[arm].values()), len(ok[arm])
        lo, hi = stats.wilson(k, n)
        succ[arm] = {"k": k, "n": n, "rate": k / n if n else None, "wilson95": [lo, hi]}
    res["within_budget_success"] = succ
    res["outcomes_matched"] = {arm: {o: sum(1 for j in m if table[j][arm]["outcome"] == o) for o in OUTCOMES}
                               for arm in (a, b)}
    lat = {arm: [INF if (x is None or x > TURN_S) else x for j in m for x in (table[j][arm]["turns_s"] or [])]
           for arm in (a, b)}
    res["turn_p95_s"] = {arm: stats.nearest_rank(lat[arm], 95) for arm in (a, b)}
    res["attempt_median_s"] = {arm: stats.median([table[j][arm]["duration_s"] for j in m]) for arm in (a, b)}
    completed = {arm: [j for j in m if table[j][arm]["outcome"] in ("pass_budget", "fail_graded")] for arm in (a, b)}
    jpc = {}
    for arm in (a, b):
        js = [table[j][arm]["window"]["pkg_j"] for j in completed[arm] if table[j][arm]["window"].get("pkg_j") is not None]
        jpc[arm] = {"n": len(js), "j_per_completed_task": round(_mean(js), 1) if js else None}
    res["j_per_completed_task"] = jpc
    ja = {table[j]["task"]: table[j][a]["window"]["pkg_j"] for j in m if table[j][a]["window"].get("pkg_j")}
    jb = {table[j]["task"]: table[j][b]["window"]["pkg_j"] for j in m if table[j][b]["window"].get("pkg_j")}
    res["j_per_attempt_ratio"] = stats.paired_ratio(ja, jb)
    if base == "H49":
        pd = stats.paired_diff(ok[a], ok[b])
        res["paired_diff"] = pd
        lo, hi = pd["ci95"]
        ra = succ[a]["rate"]
        if gates:
            verdict = "INCONCLUSIVE"
        elif lo is not None and lo >= 0.30 and ra is not None and ra >= 0.6:
            verdict = "SUPPORTED"
        elif hi is not None and hi < 0.30:
            verdict = "REFUTED"
        else:
            verdict = "INCONCLUSIVE"
        d = [ok[a][i] - ok[b][i] for i in sorted(ok[a])]
        res["confidence"] = {"support_share": boot_share(d, lambda v: v >= 0.30),
                             "method": "share of 2000 paired bootstrap resamples (seed 40) with " + f"{a} - {b} >= +0.30"}
    else:
        both = [j for j in m if j in completed[a] and j in completed[b]]
        ta = {table[j]["task"]: table[j][b]["duration_s"] for j in both}   # A4
        tb = {table[j]["task"]: table[j][a]["duration_s"] for j in both}   # A1
        pr = stats.paired_ratio(ta, tb)
        gain = None if pr["ratio"] is None else 1 - pr["ratio"]
        gci = [None if pr["ci95"][1] is None else 1 - pr["ci95"][1], None if pr["ci95"][0] is None else 1 - pr["ci95"][0]]
        ds = stats.paired_diff(ok[b], ok[a])
        res.update(time_ratio_a4_a1=pr, gain=gain, gain_ci95=gci, delta_success=ds, both_completed=len(both))
        if gates:
            verdict = "INCONCLUSIVE"
        elif gci[0] is not None and gci[0] >= 0.10 and ds["diff"] is not None and ds["diff"] >= -0.05:
            verdict = "SUPPORTED"
        elif gci[1] is not None and gci[1] < 0:
            verdict = "REFUTED"
        else:
            verdict = "INCONCLUSIVE"
        import math
        d = [math.log(ta[i] / tb[i]) for i in sorted(ta) if ta[i] > 0 and tb[i] > 0]
        res["confidence"] = {"support_share": boot_share(d, lambda v: 1 - math.exp(v) >= 0.10, stat=stats.median),
                             "refute_share": boot_share(d, lambda v: 1 - math.exp(v) < 0, stat=stats.median),
                             "method": "share of 2000 paired bootstrap resamples (seed 40) of the median log ratio "
                                       "with gain >= 10% (support) or gain < 0 (refute)"}
    res["verdict"] = verdict
    return res


INF = float("inf")


if __name__ == "__main__":
    # `pairs.py design HYP`: "<design> <rule>" (live.sh reads the rule from here, DESIGNS only).
    # `pairs.py arm HYP N`: the arm solo hold N must run (solo-hold.sh checks it before the lock).
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "design":
            print(*design(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == "arm":
            design(sys.argv[2])
            print(expected_arm(sys.argv[2], int(sys.argv[3])))
        else:
            raise SystemExit("usage: pairs.py design HYP | pairs.py arm HYP N")
    except (ValueError, KeyError) as e:
        raise SystemExit(f"pairs.py: {e!r}")

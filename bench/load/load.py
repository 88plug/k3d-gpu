#!/usr/bin/env python3
"""bench/load/load.py: a reproducible background-load profile for every probe.

Real Intel boxes are busy. A probe runs every arm under the SAME controlled
background load, interleaved (A B A B ...), records that load per arm, and calls a
run INCONCLUSIVE only when the load differs between arms beyond the pre-registered
TOLERANCE below. High load is not a reason to discard a run; unmatched load is.

Usage:
  bench/load/load.py cpus                                   # the CPU set the load uses
  bench/load/load.py plan   --level typical [--cpus LIST]   # docker commands, no side effect
  bench/load/load.py start  --level none|typical|heavy [--cpus LIST] [--ttl SECONDS]
  bench/load/load.py stop                                   # removes only this tool's containers
  bench/load/load.py status
  bench/load/load.py sample --seconds S [--windows N] [--tag ARM] [--level L] [--out FILE.jsonl]
                            [--phase baseline|pre|during] [--treatment-cpus LIST]
  bench/load/load.py settle --baseline BASE.jsonl --tag ARM --level L [--out FILE.jsonl]
                            # amendment 27: wait (max 60 s) for the level baseline, write the pre-window
  bench/load/load.py matched FILE.jsonl [...]               # exit 0 MATCHED, 3 INCONCLUSIVE, 4 VOID
  bench/load/load.py order A B --reps 3                     # A B A B A B

Levels (N = the load CPU set, E-cores on a hybrid part; see load_cpus()):
  none     nothing runs
  typical  N stress-ng --cpu workers at --cpu-load 30 (duty 0.30 each)
           + max(1, N//8) --stream workers held to duty 0.50 by docker --cpus
           + 1 --hdd page-cache churn worker (write+unlink 256m) held to duty 0.25
           = about 0.37 of the N CPUs' time on a 16 E-core part, plus DRAM traffic
  heavy    N --cpu workers at --cpu-load 100 + max(1, N//4) --stream workers
           + 2 --hdd workers, no quota: a saturated neighbour on those CPUs

Every container carries the label io.k3d-gpu.bench-load=1 (stop/status touch only
those), runs with --rm, and stress-ng's own --timeout is the hard TTL; start also
removes any of its containers whose io.k3d-gpu.bench-load.expires is past.
Python 3 standard library only.
"""
import argparse
import glob
import json
import os
import shlex
import subprocess
import sys
import time

# stress-ng 0.22.01, upstream image from ColinIanKing/stress-ng's release workflow
# (tag = release commit), pinned by digest; the same pin as bench/noisy.sh.
IMAGE = os.environ.get(
    "BENCH_LOAD_IMAGE",
    "ghcr.io/colinianking/stress-ng:72a9d0cae0f420a887991d4024d3101c4af7a4fc"
    "@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe")
LABEL = "io.k3d-gpu.bench-load"
LEVELS = ("none", "typical", "heavy")
DEFAULT_TTL = 1800
MAX_TTL = 4 * 3600

# Pre-registered matching rule, fixed before any probe uses it. Each entry compares
# the per-arm means of one metric; the spread (max - min over arms) must be within
# max(abs, rel * largest arm mean). A metric no arm measured is skipped
# ("unmeasured"); one measured by some arms but not others fails.
TOLERANCE = {
    "busy_pct": {"abs": 5.0, "rel": 0.0},          # load-CPU busy, percentage points
    "load1": {"abs": 1.0, "rel": 0.15},             # /proc/loadavg 1-min
    "pkg_w": {"abs": 3.0, "rel": 0.10},             # RAPL package watts
    "throttle_per_min": {"abs": 10.0, "rel": 0.25},  # core_throttle_count events/min
    "swap_used_mib": {"abs": 256.0, "rel": 0.0},
    "swap_delta_mib": {"abs": 64.0, "rel": 0.0},    # swap growth per window
}

# Amendment 27 (EXPERIMENTS.md), for windows that carry a "phase"; windows without
# one keep the rule above, so earlier runs read the same as when they were scored.
# COVARIATES are matched across arms under TOLERANCE. "pre" windows are sampled with
# the arm idle at the end of the settle gap before it starts; "during" windows while
# it runs, with busy_pct taken over the load CPUs the treatment does not use.
# OUTCOMES are what the treatment itself moves (a cache-off arm does ~10x the prefill
# and throttles more): reported per arm during the arm, never gating.
COVARIATES = {
    "pre": ("busy_pct", "throttle_per_min", "swap_used_mib", "swap_delta_mib"),
    "during": ("busy_pct", "swap_used_mib", "swap_delta_mib"),
}
OUTCOMES = ("pkg_w", "throttle_per_min", "load1")
PHASES = ("baseline", "pre", "during")
SETTLE_WINDOW_S = 5
SETTLE_MAX_S = 60


# ---- CPU set ----------------------------------------------------------------------

def parse_cpulist(text):
    """'0-3,8,10-11' -> [0, 1, 2, 3, 8, 10, 11]."""
    out = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def format_cpulist(cpus):
    """[0, 1, 2, 3, 8] -> '0-3,8'."""
    cpus = sorted(set(cpus))
    runs, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        runs.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(runs)


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def load_cpus(sysfs="/sys"):
    """The CPUs the background load runs on.

    Hybrid Intel (cpu_atom PMU present and non-empty): the E-cores, as listed in
    /sys/devices/cpu_atom/cpus. Otherwise: the highest-numbered half of the physical
    cores (at least one), whole cores with their SMT siblings, so the load and a
    probe on the low cores never share a core.
    """
    online = set(parse_cpulist(_read(f"{sysfs}/devices/system/cpu/online") or "0"))
    atom = set(parse_cpulist(_read(f"{sysfs}/devices/cpu_atom/cpus"))) & online
    if atom:
        return sorted(atom)
    cores = {}
    for c in sorted(online):
        sib = _read(f"{sysfs}/devices/system/cpu/cpu{c}/topology/thread_siblings_list")
        key = tuple(parse_cpulist(sib)) if sib else (c,)
        cores[key] = True
    keys = sorted(cores, key=min)
    half = keys[len(keys) - max(1, len(keys) // 2):]
    return sorted(c for k in half for c in k if c in online)


# ---- plan / start / stop ------------------------------------------------------------

def components(level, n):
    """[(name, stress-ng args, docker --cpus quota or None)] for a level on n CPUs."""
    if level == "none":
        return []
    if level == "typical":
        s = max(1, n // 8)
        return [("cpu", ["--cpu", str(n), "--cpu-load", "30"], None),
                ("stream", ["--stream", str(s), "--stream-l3-size", "128m"], 0.5 * s),
                ("churn", ["--hdd", "1", "--hdd-bytes", "256m", "--temp-path", "/tmp"], 0.25)]
    if level == "heavy":
        s = max(1, n // 4)
        return [("cpu", ["--cpu", str(n), "--cpu-load", "100"], None),
                ("stream", ["--stream", str(s), "--stream-l3-size", "128m"], None),
                ("churn", ["--hdd", "2", "--hdd-bytes", "256m", "--temp-path", "/tmp"], None)]
    raise ValueError(f"level must be one of {LEVELS}")


def duty(level, n):
    """Planned fraction of the n load CPUs' time the level keeps busy (cpu-bound
    upper bound for heavy: 1.0)."""
    if level == "none":
        return 0.0
    if level == "heavy":
        return 1.0
    total = 0.0
    for name, args, quota in components(level, n):
        if name == "cpu":
            total += n * int(args[args.index("--cpu-load") + 1]) / 100
        else:
            total += quota
    return min(1.0, total / n)


def plan(level, cpus, ttl=DEFAULT_TTL, now=None):
    """The docker run argv for each container of a level. No side effects."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    if not cpus:
        raise ValueError("empty CPU set")
    if not 1 <= int(ttl) <= MAX_TTL:
        raise ValueError(f"ttl must be 1..{MAX_TTL} s")
    ttl = int(ttl)
    expires = int(now if now is not None else time.time()) + ttl
    cl = format_cpulist(cpus)
    out = []
    for name, args, quota in components(level, len(cpus)):
        argv = ["docker", "run", "-d", "--rm", "--name", f"k3d-gpu-bench-load-{name}",
                "--label", f"{LABEL}=1", "--label", f"{LABEL}.level={level}",
                "--label", f"{LABEL}.expires={expires}",
                "--cpuset-cpus", cl, "--stop-timeout", "2", "--memory", "2g"]
        if quota is not None:
            argv += ["--cpus", f"{quota:g}"]
        argv += [IMAGE] + args + ["--timeout", f"{ttl}s", "--quiet"]
        out.append(argv)
    return out


def _docker(*args, check=True):
    p = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"load: docker {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def running():
    """[(id, level, expires)] of this tool's containers (any state)."""
    out = _docker("ps", "-a", "--filter", f"label={LABEL}=1", "--format",
                  f'{{{{.ID}}}} {{{{.Label "{LABEL}.level"}}}} {{{{.Label "{LABEL}.expires"}}}}')
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows.append((parts[0], parts[1], int(parts[2]) if parts[2].isdigit() else 0))
    return rows


def stop():
    ids = [r[0] for r in running()]
    if ids:
        _docker("rm", "-f", *ids)
    return len(ids)


def start(level, cpus, ttl=DEFAULT_TTL):
    """Idempotent: the same level already running is left alone; another level (or
    an expired leftover) is replaced."""
    now = int(time.time())
    rows = running()
    expired = [r[0] for r in rows if r[2] <= now]
    if expired:
        _docker("rm", "-f", *expired)
        rows = running()
    argvs = plan(level, cpus, ttl, now)
    if rows and {r[1] for r in rows} == {level} and len(rows) == len(argvs):
        return "already running"
    stop()
    for argv in argvs:
        p = subprocess.run(argv, capture_output=True, text=True)
        if p.returncode != 0:
            stop()
            raise SystemExit(f"load: {' '.join(argv)}: {p.stderr.strip()}")
    return f"started {level} on {format_cpulist(cpus)} ({len(argvs)} containers, ttl {ttl}s)"


# ---- sampler ------------------------------------------------------------------------

def cpu_times(stat_text, cpus):
    """(busy, total) jiffies summed over the given CPUs from /proc/stat text."""
    want = {f"cpu{c}" for c in cpus}
    busy = total = 0
    for line in stat_text.splitlines():
        f = line.split()
        if f and f[0] in want:
            v = [int(x) for x in f[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)   # idle + iowait
            t = sum(v[:8])                              # guest is already in user
            busy += t - idle
            total += t
    return busy, total


def swap_used_mib(meminfo_text):
    kv = {}
    for line in meminfo_text.splitlines():
        k, _, v = line.partition(":")
        kv[k] = int(v.split()[0]) if v.split() else 0
    return (kv.get("SwapTotal", 0) - kv.get("SwapFree", 0)) / 1024


def throttle_total(sysfs="/sys"):
    return sum(int(_read(p) or 0) for p in
               glob.glob(f"{sysfs}/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count"))


def rapl_zone(sysfs="/sys"):
    for z in sorted(glob.glob(f"{sysfs}/class/powercap/intel-rapl:[0-9]")):
        if _read(f"{z}/name").startswith("package"):
            return z
    return None



def rapl_elevation():
    """Command prefix that reads a root-only energy_uj: K3D_GPU_RAPL_EXEC when set (e.g.
    `docker exec <container with /sys/class/powercap> ` on a host without sudo), else
    sudo -A (SUDO_ASKPASS set) or sudo -n."""
    cmd = os.environ.get("K3D_GPU_RAPL_EXEC")
    if cmd:
        return shlex.split(cmd)
    return ["sudo", "-A"] if os.environ.get("SUDO_ASKPASS") else ["sudo", "-n"]

def read_energy_uj(zone):
    """Package energy counter. energy_uj is root-only on current kernels: read it
    directly when allowed, else via sudo -A (SUDO_ASKPASS set) or sudo -n."""
    if zone is None:
        return None
    path = f"{zone}/energy_uj"
    v = _read(path)
    if v.isdigit():
        return int(v)
    elev = rapl_elevation()
    p = subprocess.run(elev + ["cat", path], capture_output=True, text=True)
    v = p.stdout.strip()
    return int(v) if p.returncode == 0 and v.isdigit() else None


def energy_delta(e0, e1, max_range):
    if e0 is None or e1 is None:
        return None
    d = e1 - e0
    if d < 0:
        d += max_range
    return d


def load_running_count():
    """Running containers of this tool, or None when docker cannot be asked."""
    try:
        p = subprocess.run(["docker", "ps", "-q", "--filter", f"label={LABEL}=1",
                            "--filter", "status=running"], capture_output=True, text=True)
    except OSError:
        return None
    return len(p.stdout.split()) if p.returncode == 0 else None


def load_expected(level, cpus):
    """Containers a level runs on a CPU set; None for an unknown level."""
    return len(components(level, len(cpus))) if level in LEVELS and cpus else None


def window(seconds, cpus, sysfs="/sys", proc="/proc", tag=None, level=None,
           phase=None, exclude=(), presence=None):
    """One sample window: the background load the host carried over `seconds`.

    With a phase (amendment 27): busy_pct covers the load CPUs minus `exclude` (the
    CPUs the treatment uses), and the window records how many of this tool's load
    containers ran (the minimum of a count at its start and at its end, via
    `presence`, default docker) against the number the level runs."""
    if phase is not None:
        if phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        presence = presence or load_running_count
    busy_set = sorted(set(cpus) - set(exclude))
    zone = rapl_zone(sysfs)
    max_range = int(_read(f"{zone}/max_energy_range_uj") or 0) if zone else 0
    n0 = presence() if presence else None
    s0, sw0, th0, e0 = _read(f"{proc}/stat"), swap_used_mib(_read(f"{proc}/meminfo")), throttle_total(sysfs), read_energy_uj(zone)
    t0 = time.monotonic()
    time.sleep(seconds)
    e1, dt = read_energy_uj(zone), time.monotonic() - t0
    s1, sw1, th1 = _read(f"{proc}/stat"), swap_used_mib(_read(f"{proc}/meminfo")), throttle_total(sysfs)
    n1 = presence() if presence else None
    b0, t0j = cpu_times(s0, busy_set)
    b1, t1j = cpu_times(s1, busy_set)
    de = energy_delta(e0, e1, max_range)
    dt = max(dt, 1e-9)
    w = {
        "ts": time.time(), "tag": tag, "level": level, "seconds": round(dt, 3),
        "cpus": format_cpulist(cpus),
        "busy_pct": round(100 * (b1 - b0) / (t1j - t0j), 2) if t1j > t0j else None,
        "load1": float(_read(f"{proc}/loadavg").split()[0]),
        "pkg_w": round(de / 1e6 / dt, 2) if de is not None else None,
        "throttle_per_min": round((th1 - th0) * 60 / dt, 2),
        "swap_used_mib": round(sw1, 1),
        "swap_delta_mib": round(sw1 - sw0, 1),
    }
    if phase is not None:
        w.update(phase=phase, busy_cpus=format_cpulist(busy_set),
                 load_expected=load_expected(level, cpus),
                 load_running=None if n0 is None or n1 is None else min(n0, n1))
    return w


def settle(baseline, sample, window_s=SETTLE_WINDOW_S, max_s=SETTLE_MAX_S):
    """Hog-decay gap (amendment 27): sample `window_s` windows until one reads busy_pct
    and throttle_per_min within TOLERANCE of the level baseline (the limit is
    max(abs, rel * baseline)), or `max_s` has passed. Returns the last window, which is
    the arm's pre-window, with "settled" and "settle_s" added. A metric the baseline
    did not measure is not waited on."""
    elapsed = 0
    while True:
        w = sample(window_s)
        elapsed += window_s
        ok = True
        for m in ("busy_pct", "throttle_per_min"):
            base = baseline.get(m)
            if base is None:
                continue
            tol = TOLERANCE[m]
            v = w.get(m)
            if v is None or abs(v - base) > max(tol["abs"], tol["rel"] * abs(base)):
                ok = False
        if ok or elapsed >= max_s:
            return dict(w, settled=ok, settle_s=elapsed)


# ---- matching -----------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs)


def _by_arm(windows):
    arms = {}
    for w in windows:
        arms.setdefault(w.get("tag"), []).append(w)
    return arms


def _spread(arms, metric):
    """Per-arm means of one metric: (check dict, spread or None, have-all bool)."""
    per = {}
    for arm, ws in arms.items():
        vals = [w[metric] for w in ws if w.get(metric) is not None]
        per[arm] = _mean(vals) if vals else None
    have = [v for v in per.values() if v is not None]
    c = {"metric": metric, "arms": {str(k): (round(v, 3) if v is not None else None) for k, v in per.items()}}
    return c, have, len(have) == len(per)


def _check(arms, metric, label, reasons):
    """One TOLERANCE check of the per-arm means; appends a reason on failure."""
    tol = TOLERANCE[metric]
    c, have, complete = _spread(arms, metric)
    if not have:
        c["status"] = "unmeasured"
    elif not complete:
        c["status"] = "fail"
        reasons.append(f"{label} measured in some arms only")
    else:
        spread = max(have) - min(have)
        limit = max(tol["abs"], tol["rel"] * max(abs(v) for v in have))
        c.update(spread=round(spread, 3), limit=round(limit, 3))
        c["status"] = "ok" if spread <= limit else "fail"
        if spread > limit:
            reasons.append(f"{label} spread {spread:.2f} > {limit:.2f}")
    return c


def _verdict(reasons):
    return "MATCHED" if not reasons else "INCONCLUSIVE: load unmatched (" + "; ".join(reasons) + ")"


def matched(windows):
    """Decide whether the arms' background load is matched.

    windows: sample dicts carrying "tag" (the arm). Windows with a "phase" are judged
    by amendment 27 (matched_a27); a set without any keeps the earlier rule
    (every TOLERANCE metric gates), so runs scored under it read the same.
    Returns {"matched": bool, "verdict": "MATCHED" | "INCONCLUSIVE: ..." | "VOID: ...",
    "rule": ..., "checks": [...], ...}. How high the load is never enters the decision;
    only the spread across arms."""
    if any("phase" in w for w in windows):
        return matched_a27(windows)
    arms = _by_arm(windows)
    checks, reasons = [], []
    if len(arms) < 2:
        reasons.append(f"need >= 2 arms, got {sorted(map(str, arms))}")
    levels = {w.get("level") for w in windows}
    if len(levels) > 1:
        reasons.append(f"mixed load levels {sorted(map(str, levels))}")
    for metric in TOLERANCE:
        checks.append(_check(arms, metric, metric, reasons))
    return {"matched": not reasons, "verdict": _verdict(reasons), "rule": "legacy", "checks": checks}


def matched_a27(windows):
    """Amendment 27. Gating: the COVARIATES of each phase (pre-window busy, throttle and
    swap with the arm idle after the settle gap; during-arm busy of the load CPUs the
    treatment does not use, and swap). Reported, never gating: the OUTCOMES during the
    arm. VOID (never scored) when any window saw fewer load containers running than
    its level runs, or could not count them. Every arm needs a pre-window. "baseline"
    windows (the level reference for settle) do not enter."""
    void, reasons = [], []
    if any("phase" not in w for w in windows):
        reasons.append("window without phase in an amendment-27 run")
    ws = [w for w in windows if w.get("phase") in ("pre", "during")]
    arms = _by_arm(ws)
    if len(arms) < 2:
        reasons.append(f"need >= 2 arms, got {sorted(map(str, arms))}")
    levels = {w.get("level") for w in ws}
    if len(levels) > 1:
        reasons.append(f"mixed load levels {sorted(map(str, levels))}")
    for w in ws:
        if w.get("level") == "none":
            continue
        exp, run = w.get("load_expected"), w.get("load_running")
        where = f"arm {w.get('tag')} {w.get('phase')} window"
        if exp is None or run is None:
            void.append(f"load presence unverified ({where})")
        elif run < exp:
            void.append(f"load missing: {run} of {exp} containers running ({where})")
    checks = []
    for phase, metrics in COVARIATES.items():
        sub = {a: [w for w in aw if w["phase"] == phase] for a, aw in arms.items()}
        if phase == "pre":
            missing = sorted(str(a) for a, aw in sub.items() if not aw)
            if missing:
                reasons.append(f"no pre-window for arm(s) {', '.join(missing)}")
            sub = {a: aw for a, aw in sub.items() if aw}
        if not any(sub.values()):
            continue
        for metric in metrics:
            c = _check(sub, metric, f"{phase} {metric}", reasons)
            c["phase"] = phase
            checks.append(c)
    outcomes = []
    during = {a: [w for w in aw if w["phase"] == "during"] for a, aw in arms.items()}
    for metric in OUTCOMES:
        c, have, _ = _spread(during, metric)
        if have:
            c["spread"] = round(max(have) - min(have), 3)
        outcomes.append(c)
    unsettled = {}
    for w in ws:
        if w["phase"] == "pre" and w.get("settled") is False:
            unsettled[str(w.get("tag"))] = unsettled.get(str(w.get("tag")), 0) + 1
    if void:
        verdict = "VOID: " + "; ".join(void)
    else:
        verdict = _verdict(reasons)
    return {"matched": not void and not reasons, "void": bool(void), "verdict": verdict,
            "rule": "amendment-27", "checks": checks, "outcomes": outcomes, "unsettled": unsettled}


def order(arms, reps):
    """Interleaved run order: ['A', 'B'], 2 -> ['A', 'B', 'A', 'B']."""
    return [a for _ in range(reps) for a in arms]


# ---- CLI ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cpus")
    for name in ("plan", "start"):
        p = sub.add_parser(name)
        p.add_argument("--level", required=True, choices=LEVELS)
        p.add_argument("--cpus", help="CPU list (default: load_cpus())")
        p.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    sub.add_parser("stop")
    sub.add_parser("status")
    p = sub.add_parser("sample")
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--windows", type=int, default=1)
    p.add_argument("--tag")
    p.add_argument("--level")
    p.add_argument("--cpus")
    p.add_argument("--out", help="append JSON lines here (default: stdout)")
    p.add_argument("--phase", choices=PHASES, help="amendment 27 window phase")
    p.add_argument("--treatment-cpus", default="",
                   help="CPUs the treatment uses; left out of busy_pct (amendment 27)")
    p = sub.add_parser("settle")
    p.add_argument("--baseline", required=True, help="JSON lines of the level's baseline windows")
    p.add_argument("--tag", required=True)
    p.add_argument("--level", required=True, choices=LEVELS)
    p.add_argument("--cpus")
    p.add_argument("--window", type=float, default=SETTLE_WINDOW_S)
    p.add_argument("--max", type=float, default=SETTLE_MAX_S)
    p.add_argument("--out", help="append the pre-window here (default: stdout)")
    p = sub.add_parser("matched")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("order")
    p.add_argument("arms", nargs="+")
    p.add_argument("--reps", type=int, default=2)
    o = ap.parse_args(argv)

    cpus = parse_cpulist(o.cpus) if getattr(o, "cpus", None) else load_cpus()
    if o.cmd == "cpus":
        print(format_cpulist(cpus))
    elif o.cmd == "plan":
        for argv_ in plan(o.level, cpus, o.ttl):
            print(" ".join(argv_))
    elif o.cmd == "start":
        print(start(o.level, cpus, o.ttl))
    elif o.cmd == "stop":
        print(f"removed {stop()} container(s)")
    elif o.cmd == "status":
        rows = running()
        for cid, level, exp in rows:
            print(f"{cid} level={level} expires_in={exp - int(time.time())}s")
        if not rows:
            print("none running")
    elif o.cmd == "sample":
        fh = open(o.out, "a") if o.out else sys.stdout
        try:
            for _ in range(o.windows):
                w = window(o.seconds, cpus, tag=o.tag, level=o.level, phase=o.phase,
                           exclude=parse_cpulist(o.treatment_cpus))
                fh.write(json.dumps(w) + "\n")
                fh.flush()
        finally:
            if o.out:
                fh.close()
    elif o.cmd == "settle":
        with open(o.baseline) as f:
            base = [json.loads(line) for line in f if line.strip()]
        ref = {}
        for m in ("busy_pct", "throttle_per_min"):
            vals = [w[m] for w in base if w.get(m) is not None]
            ref[m] = _mean(vals) if vals else None
        w = settle(ref, lambda s: window(s, cpus, tag=o.tag, level=o.level, phase="pre"),
                   window_s=o.window, max_s=o.max)
        line = json.dumps(w) + "\n"
        if o.out:
            with open(o.out, "a") as f:
                f.write(line)
        else:
            sys.stdout.write(line)
        print(f"settle: {'settled' if w['settled'] else 'NOT settled'} after {w['settle_s']:g} s",
              file=sys.stderr)
    elif o.cmd == "matched":
        ws = []
        for path in o.files:
            with open(path) as f:
                ws += [json.loads(line) for line in f if line.strip()]
        r = matched(ws)
        print(json.dumps(r, indent=1))
        return 0 if r["matched"] else 4 if r.get("void") else 3
    elif o.cmd == "order":
        print(" ".join(order(o.arms, o.reps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

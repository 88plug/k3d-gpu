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
  bench/load/load.py matched FILE.jsonl [...]               # exit 0 MATCHED, 3 INCONCLUSIVE
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


def read_energy_uj(zone):
    """Package energy counter. energy_uj is root-only on current kernels: read it
    directly when allowed, else via sudo -A (SUDO_ASKPASS set) or sudo -n."""
    if zone is None:
        return None
    path = f"{zone}/energy_uj"
    v = _read(path)
    if v.isdigit():
        return int(v)
    elev = ["sudo", "-A"] if os.environ.get("SUDO_ASKPASS") else ["sudo", "-n"]
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


def window(seconds, cpus, sysfs="/sys", proc="/proc", tag=None, level=None):
    """One sample window: the background load the host carried over `seconds`."""
    zone = rapl_zone(sysfs)
    max_range = int(_read(f"{zone}/max_energy_range_uj") or 0) if zone else 0
    s0, sw0, th0, e0 = _read(f"{proc}/stat"), swap_used_mib(_read(f"{proc}/meminfo")), throttle_total(sysfs), read_energy_uj(zone)
    t0 = time.monotonic()
    time.sleep(seconds)
    e1, dt = read_energy_uj(zone), time.monotonic() - t0
    s1, sw1, th1 = _read(f"{proc}/stat"), swap_used_mib(_read(f"{proc}/meminfo")), throttle_total(sysfs)
    b0, t0j = cpu_times(s0, cpus)
    b1, t1j = cpu_times(s1, cpus)
    de = energy_delta(e0, e1, max_range)
    return {
        "ts": time.time(), "tag": tag, "level": level, "seconds": round(dt, 3),
        "cpus": format_cpulist(cpus),
        "busy_pct": round(100 * (b1 - b0) / (t1j - t0j), 2) if t1j > t0j else None,
        "load1": float(_read(f"{proc}/loadavg").split()[0]),
        "pkg_w": round(de / 1e6 / dt, 2) if de is not None else None,
        "throttle_per_min": round((th1 - th0) * 60 / dt, 2),
        "swap_used_mib": round(sw1, 1),
        "swap_delta_mib": round(sw1 - sw0, 1),
    }


# ---- matching -----------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs)


def matched(windows):
    """Decide whether the arms' background load is matched under TOLERANCE.

    windows: sample dicts carrying "tag" (the arm). Returns
    {"matched": bool, "verdict": "MATCHED" | "INCONCLUSIVE: ...", "checks": [...]}.
    How high the load is never enters the decision; only the spread across arms."""
    arms = {}
    for w in windows:
        arms.setdefault(w.get("tag"), []).append(w)
    checks, reasons = [], []
    if len(arms) < 2:
        reasons.append(f"need >= 2 arms, got {sorted(map(str, arms))}")
    levels = {w.get("level") for w in windows}
    if len(levels) > 1:
        reasons.append(f"mixed load levels {sorted(map(str, levels))}")
    for metric, tol in TOLERANCE.items():
        per = {}
        for arm, ws in arms.items():
            vals = [w[metric] for w in ws if w.get(metric) is not None]
            per[arm] = _mean(vals) if vals else None
        have = [v for v in per.values() if v is not None]
        c = {"metric": metric, "arms": {str(k): (round(v, 3) if v is not None else None) for k, v in per.items()}}
        if not have:
            c["status"] = "unmeasured"
        elif len(have) < len(per):
            c["status"] = "fail"
            reasons.append(f"{metric} measured in some arms only")
        else:
            spread = max(have) - min(have)
            limit = max(tol["abs"], tol["rel"] * max(abs(v) for v in have))
            c.update(spread=round(spread, 3), limit=round(limit, 3))
            c["status"] = "ok" if spread <= limit else "fail"
            if spread > limit:
                reasons.append(f"{metric} spread {spread:.2f} > {limit:.2f}")
        checks.append(c)
    ok = not reasons
    return {"matched": ok, "verdict": "MATCHED" if ok else "INCONCLUSIVE: load unmatched (" + "; ".join(reasons) + ")",
            "checks": checks}


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
                fh.write(json.dumps(window(o.seconds, cpus, tag=o.tag, level=o.level)) + "\n")
                fh.flush()
        finally:
            if o.out:
                fh.close()
    elif o.cmd == "matched":
        ws = []
        for path in o.files:
            with open(path) as f:
                ws += [json.loads(line) for line in f if line.strip()]
        r = matched(ws)
        print(json.dumps(r, indent=1))
        return 0 if r["matched"] else 3
    elif o.cmd == "order":
        print(" ".join(order(o.arms, o.reps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

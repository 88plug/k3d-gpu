#!/usr/bin/env python3
"""capacity.py: agents per box at a p95 turn-latency SLO (`k3d-gpu capacity`).

Closed-loop agent ramp against one OpenAI-compatible chat endpoint (OVMS or
vLLM in the cluster, or --url). N agents each replay agent sessions back to
back (bench/apb/apb.py run_step over bench/agent_trace.py): send a turn, wait
for the full streamed reply, wait the trace's tool time, send the next turn.

  1. Endpoint: discovered from the cluster (kubectl get deployments,
     statefulsets, services) and reached through `kubectl port-forward`, or
     given with --url/--model.
  2. Samplers for the whole run: CPU package energy (RAPL powercap
     package-* zones, plus psys), package thermal-throttle count, /proc/stat,
     and nvidia-smi power/limit/utilization/clock-event flags when present.
  3. Quiet check over --quiet-s: CPU busy, package power against the box's own
     PL1, NVIDIA GPU utilization and power against its own limit. A box that
     is not quiet is refused (exit 5) unless --force; forced, the failed
     checks are written to the report as confounds.
  4. One discarded warm-up (2 sessions, N=1). All of it failing = exit 6.
  5. Ramp N over 1, 2, 4, 8, 12, 16, 24, 32, 48, 64, ... up to --max, --reps
     reps per step (more reps, up to 2 x --reps, while the pooled sample is
     too small). The ramp stops at the first step that fails at --stop-slo
     (default --slo). A rep whose own p95 exceeds 2 x --stop-slo ends its step.
  6. One more N=1 rep (drift check), then capacity.json.

Scoring is bench/apb/apb.py's (PROTOCOL.md section 6): steady window per rep,
nearest-rank p95 pooled over reps, step passes at pooled n >= 60, p95 <= SLO,
error rate <= 1%, tool validity within 10 points of N=1. Agents per box =
the largest N such that every step <= N passes.

Exit: 0 report written, 2 usage/discovery error, 5 box not quiet, 6 endpoint
not answering.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apb  # noqa: E402

SCHEMA = "k3d-gpu.capacity/1"
EXIT_USAGE, EXIT_NOT_QUIET, EXIT_ENDPOINT = 2, 5, 6
AGENT_NAMESPACE = "k3d-gpu-agent"  # `k3d-gpu agent up`
ENGINES = ("auto", "cpu", "igpu", "npu", "nvidia")
EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

# Quiet check limits, relative to the box's own ceilings (no host constants).
QUIET_CPU_BUSY_MAX = 0.10      # fraction of all logical CPUs busy
QUIET_PKG_FRACTION_MAX = 0.35  # package W / PL1
QUIET_GPU_UTIL_MAX = 10.0      # % utilization.gpu
QUIET_GPU_POWER_FRACTION_MAX = 0.5  # power.draw / enforced.power.limit
# Binding-limit flags during the first failing step.
BIND_PKG_FRACTION = 0.9        # mean package W >= 0.9 x PL1
BIND_GPU_FLAG_FRACTION = 0.25  # clock-event flag active in >= 25% of samples
BIND_GPU_POWER_FRACTION = 0.95
DRIFT_BAND = 0.15

NVIDIA_FIELDS = ("index,power.draw,enforced.power.limit,utilization.gpu,temperature.gpu,"
                 "clocks_event_reasons.sw_power_cap,clocks_event_reasons.sw_thermal_slowdown,"
                 "clocks_event_reasons.hw_thermal_slowdown,clocks_event_reasons.hw_slowdown")


class CapacityError(Exception):
    pass


def log(msg):
    print(f"[capacity {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def sysroot():
    return os.environ.get("K3D_GPU_SYSROOT", "")


# ---------------------------------------------------------------- ramp grid
def ramp_steps(max_agents):
    """1, 2, 4, 8, then +4 to 16, +8 to 32, +16 after; always ends at max_agents."""
    if max_agents < 1:
        raise ValueError("--max must be >= 1")
    out, n = [], 1
    while n < max_agents:
        out.append(n)
        n = n * 2 if n < 8 else n + (4 if n < 16 else 8 if n < 32 else 16)
    out.append(max_agents)
    return out


# ---------------------------------------------------------------- ramp loop
def run_ramp(run_rep, steps, reps, stop_slo, max_reps=None, on_step=None):
    """Run the ramp. run_rep(n, rep) -> (results, meta). Returns the scored
    steps (apb.score_step records) in order; stops after the first failing or
    still-insufficient step."""
    max_reps = max(reps, max_reps if max_reps is not None else 2 * reps)
    scored, base_tool = [], None
    for n in steps:
        reps_data, early = [], False
        rep = 0
        while True:
            rep += 1
            results, meta = run_rep(n, rep)
            reps_data.append((results, meta))
            if apb.early_fail(results, meta, stop_slo):
                early = True
                break
            if rep < reps:
                continue
            s = apb.score_step(reps_data, stop_slo, base_tool)
            if s["verdict"] != "insufficient" or rep >= max_reps:
                break
        s = apb.score_step(reps_data, stop_slo, base_tool)
        if early and s["verdict"] != "pass":
            s["verdict"] = "fail"
            if not any(r.startswith("p95") for r in s["reasons"]):
                s["reasons"].insert(0, f"p95 > {apb.EARLY_FAIL_FACTOR:g} x SLO {stop_slo:g} in one rep")
        if n == steps[0] and base_tool is None:
            base_tool = s["tool_valid_any"]
        s["_reps"] = reps_data
        scored.append(s)
        if on_step:
            on_step(s)
        if s["verdict"] != "pass":
            break
    return scored


def agents_per_box(steps):
    """(APB, interpolated APB): largest N such that every step <= N passes."""
    return apb.apb(sorted(steps, key=lambda s: s["agents"]))


# ---------------------------------------------------------------- discovery
def _flag(args, *names):
    """Value of --name=value or --name value in an argv list, or None."""
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                return args[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def _server(image):
    img = image.lower()
    if "model_server" in img or "/ovms" in img:
        return "ovms"
    if "vllm" in img:
        return "vllm"
    return None


def _engine(pod, containers):
    res = {}
    for c in containers:
        r = c.get("resources") or {}
        res.update(r.get("requests") or {})
        res.update(r.get("limits") or {})
    keys = list(res)
    if any(k == "nvidia.com/gpu" or k.startswith("nvidia.com/") for k in keys):
        return "nvidia"
    if any(k.startswith("gpu.intel.com/") for k in keys):
        return "igpu"
    if any(k.startswith("npu.intel.com/") for k in keys):
        return "npu"
    dev = ((pod.get("metadata") or {}).get("annotations") or {}).get("k3d-gpu.io/agent-device")
    spec = pod.get("spec") or {}
    if not dev:
        for c in containers + list(spec.get("initContainers") or []):
            dev = _flag((c.get("command") or []) + (c.get("args") or []), "--target_device")
            if dev:
                break
    dev = (dev or "").upper()
    if dev.startswith("GPU"):
        return "igpu"
    if dev.startswith("NPU"):
        return "npu"
    if spec.get("runtimeClassName") == "nvidia":
        return "nvidia"
    return "cpu"


def _model(server, argv):
    if server == "ovms":
        return _flag(argv, "--model_name")
    name = _flag(argv, "--served-model-name") or _flag(argv, "--model")
    if name:
        return name.split()[0]
    if "serve" in argv:
        i = argv.index("serve")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            return argv[i + 1]
    return None


def discover(items):
    """OpenAI chat endpoints served by OVMS or vLLM workloads that a Service
    selects. items: the 'items' of `kubectl get deployments,statefulsets,
    services -A -o json`."""
    services = [i for i in items if i.get("kind") == "Service"]
    out = []
    for w in items:
        if w.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        md, spec = w.get("metadata") or {}, w.get("spec") or {}
        pod = spec.get("template") or {}
        containers = list((pod.get("spec") or {}).get("containers") or [])
        main = next((c for c in containers if _server(c.get("image", ""))), None)
        if main is None:
            continue
        server = _server(main["image"])
        argv = list(main.get("command") or []) + list(main.get("args") or [])
        labels = (pod.get("metadata") or {}).get("labels") or {}
        ns = md.get("namespace", "default")
        cports = main.get("ports") or []
        # (targets a server port?, service, port): a Service that targets one of
        # the server container's ports wins over one that only selects the pod.
        matches = []
        for svc in services:
            smd, sspec = svc.get("metadata") or {}, svc.get("spec") or {}
            sel = sspec.get("selector") or {}
            if smd.get("namespace", "default") != ns or not sel:
                continue
            if any(labels.get(k) != v for k, v in sel.items()):
                continue
            ports = sspec.get("ports") or []
            hit = [p for p in ports
                   if any(p.get("targetPort") in (cp.get("name"), cp.get("containerPort")) for cp in cports)]
            if hit:
                matches.append((True, smd, hit[0]))
            elif ports:
                matches.append((False, smd, ports[0]))
        matches.sort(key=lambda m: not m[0])
        for _hit, smd, port in matches[:1]:
            out.append({
                "namespace": ns, "workload": f"{w['kind'].lower()}/{md.get('name')}",
                "service": smd.get("name"), "port": int(port["port"]),
                "server": server, "engine": _engine(pod, containers),
                "model": _model(server, argv),
                "path": "/v3/chat/completions" if server == "ovms" else "/v1/chat/completions",
                "ready": int((w.get("status") or {}).get("readyReplicas") or 0) > 0,
                "image": main["image"],
            })
            break
    return sorted(out, key=lambda c: (c["namespace"], c["service"]))


def _describe(c):
    return f"{c['namespace']}/{c['service']} ({c['server']}, {c['engine']}, model {c['model']})"


def choose(cands, engine="auto", endpoint=None):
    if not cands:
        raise CapacityError("no OVMS or vLLM chat endpoint behind a Service in the cluster; "
                            "deploy one first (k3d-gpu agent up) or pass --url and --model")
    pool = cands
    if endpoint:
        pool = [c for c in cands if f"{c['namespace']}/{c['service']}" == endpoint]
        if not pool:
            raise CapacityError(f"--endpoint {endpoint} not found; endpoints: "
                                + "; ".join(_describe(c) for c in cands))
    if engine != "auto":
        pool = [c for c in pool if c["engine"] == engine]
        if not pool:
            raise CapacityError(f"no endpoint on engine '{engine}'; endpoints: "
                                + "; ".join(_describe(c) for c in cands))
    if len(pool) > 1:
        agent = [c for c in pool if c["namespace"] == AGENT_NAMESPACE]
        if len(agent) != 1:
            raise CapacityError("several endpoints match; pick one with --endpoint NAMESPACE/SERVICE: "
                                + "; ".join(_describe(c) for c in pool))
        pool = agent
    c = pool[0]
    if not c["ready"]:
        raise CapacityError(f"endpoint {_describe(c)} has no ready replica; "
                            f"kubectl -n {c['namespace']} get pods")
    if not c["model"]:
        raise CapacityError(f"cannot read the served model name of {_describe(c)}; pass --model")
    return c


def kubectl_items():
    try:
        p = subprocess.run(["kubectl", "get", "deployments,statefulsets,services", "-A", "-o", "json",
                            "--request-timeout=10s"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise CapacityError("kubectl not found; pass --url and --model to skip discovery")
    except subprocess.TimeoutExpired:
        raise CapacityError("kubectl get timed out; is the cluster up? (k3d-gpu up)")
    if p.returncode != 0:
        raise CapacityError(f"kubectl get failed: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout).get("items") or []


class PortForward:
    """kubectl port-forward svc/NAME :PORT on a free local port."""

    def __init__(self, ns, svc, port, timeout=30.0):
        self.proc = subprocess.Popen(["kubectl", "-n", ns, "port-forward", f"svc/{svc}", f":{port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.local = None
        found = threading.Event()

        def read():
            for line in self.proc.stdout:
                m = re.search(r"Forwarding from 127\.0\.0\.1:(\d+)", line)
                if m and self.local is None:
                    self.local = int(m.group(1))
                    found.set()
            found.set()

        threading.Thread(target=read, daemon=True).start()
        if not found.wait(timeout) or self.local is None:
            err = ""
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().strip()[:300]
            self.close()
            raise CapacityError(f"kubectl port-forward svc/{svc} failed {err}".strip())

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------- host reads
def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def cpu_busy(stat_a, stat_b):
    """Busy fraction of all CPUs between two /proc/stat 'cpu' lines."""
    def split(line):
        v = [int(x) for x in line.split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        return sum(v) - idle, sum(v)
    ba, ta = split(stat_a)
    bb, tb = split(stat_b)
    return 0.0 if tb == ta else (bb - ba) / (tb - ta)


def rapl_zones(root=None):
    """{'pkg': [zone dirs], 'psys': zone dir or None, 'pl1_w': sum of long_term limits}."""
    root = sysroot() if root is None else root
    pkg, psys, pl1 = [], None, 0.0
    for z in sorted(glob.glob(f"{root}/sys/class/powercap/intel-rapl:*")):
        if z.count(":") != 1:
            continue
        name = _read(f"{z}/name") or ""
        if name.startswith("package"):
            pkg.append(z)
            for c in glob.glob(f"{z}/constraint_*_name"):
                if _read(c) == "long_term":
                    v = _read(c.replace("_name", "_power_limit_uw"))
                    if v and v.isdigit():
                        pl1 += int(v) / 1e6
        elif name == "psys":
            psys = z
    return {"pkg": pkg, "psys": psys, "pl1_w": pl1 or None}


def throttle_count(root=None):
    """Sum of package_throttle_count over packages, or None."""
    root = sysroot() if root is None else root
    seen, total = set(), None
    for d in sorted(glob.glob(f"{root}/sys/devices/system/cpu/cpu[0-9]*")):
        pid = _read(f"{d}/topology/physical_package_id")
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        v = _read(f"{d}/thermal_throttle/package_throttle_count")
        if v and v.isdigit():
            total = (total or 0) + int(v)
    return total


def host_facts():
    root = sysroot()
    model = None
    for line in (_read(f"{root}/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    cpus = len(glob.glob(f"{root}/sys/devices/system/cpu/cpu[0-9]*")) or os.cpu_count()
    z = rapl_zones()
    return {"cpu_model": model, "logical_cpus": cpus, "pl1_w": z["pl1_w"],
            "kernel": platform.release(), "loadavg": _read(f"{root}/proc/loadavg")}


class Sampler:
    """Samples every `interval` s; tick() also samples on demand. Writes
    rapl.log and nvidia.log in apb.py's formats and signals.jsonl."""

    def __init__(self, run_dir, interval=1.0):
        self.dir, self.interval = run_dir, interval
        self.zones = rapl_zones()
        self.rapl_mode = self._rapl_mode()
        self.nvidia = shutil.which(os.environ.get("K3D_GPU_NVIDIA_SMI", "nvidia-smi"))
        self.nvidia_seen = False
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.signals = []
        self.thread = None
        wrap = _read(f"{self.zones['pkg'][0]}/max_energy_range_uj") if self.zones["pkg"] else None
        if wrap and wrap.isdigit():
            os.environ["APB_RAPL_WRAP_UJ"] = wrap

    def _rapl_mode(self):
        if not self.zones["pkg"]:
            return None
        path = f"{self.zones['pkg'][0]}/energy_uj"
        try:
            with open(path) as f:
                int(f.read())
            return "direct"
        except PermissionError:
            pass
        except (OSError, ValueError):
            return None
        if shutil.which("sudo"):
            p = subprocess.run(["sudo", "-n", "cat", path], capture_output=True, text=True, timeout=10)
            if p.returncode == 0 and p.stdout.strip().isdigit():
                return "sudo"
        return None

    def _energy(self, zone):
        path = f"{zone}/energy_uj"
        if self.rapl_mode == "direct":
            v = _read(path)
        else:
            p = subprocess.run(["sudo", "-n", "cat", path], capture_output=True, text=True, timeout=10)
            v = p.stdout.strip() if p.returncode == 0 else None
        return int(v) if v and v.isdigit() else None

    def _gpus(self):
        if not self.nvidia:
            return []
        try:
            p = subprocess.run([self.nvidia, f"--query-gpu={NVIDIA_FIELDS}", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            p = None
        if p is None or p.returncode != 0:
            if not self.nvidia_seen:
                self.nvidia = None  # never answered: stop asking
            return []
        out = []
        for line in p.stdout.splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) < 9:
                continue

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            if num(f[0]) is None:
                continue
            out.append({"index": int(num(f[0])), "power_w": num(f[1]), "limit_w": num(f[2]),
                        "util": num(f[3]), "temp_c": num(f[4]),
                        "power_cap": f[5] == "Active", "thermal": "Active" in (f[6], f[7]),
                        "hw_slowdown": f[8] == "Active"})
        self.nvidia_seen = self.nvidia_seen or bool(out)
        return out

    def tick(self):
        with self.lock:
            t = round(time.time(), 6)  # written and returned as the same value
            stat = (_read(f"{sysroot()}/proc/stat") or "").splitlines()
            cpu = next((ln for ln in stat if ln.startswith("cpu ")), None)
            sig = {"t": t, "cpu": cpu, "throttle": throttle_count(), "gpus": self._gpus()}
            if self.rapl_mode:
                vals = [self._energy(z) for z in self.zones["pkg"]]
                if all(v is not None for v in vals):
                    psys = self._energy(self.zones["psys"]) if self.zones["psys"] else None
                    with open(os.path.join(self.dir, "rapl.log"), "a") as f:
                        f.write(f"{t:.6f} {sum(vals)} {'-' if psys is None else psys}\n")
            if sig["gpus"]:
                with open(os.path.join(self.dir, "nvidia.log"), "a") as f:
                    for g in sig["gpus"]:
                        if g["power_w"] is not None:
                            f.write(f"{t:.6f}, {g['index']}, {g['power_w']}\n")
            self.signals.append(sig)
            with open(os.path.join(self.dir, "signals.jsonl"), "a") as f:
                f.write(json.dumps(sig) + "\n")
            return t

    def start(self):
        self.tick()

        def loop():
            while not self.stop_ev.wait(self.interval):
                self.tick()
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        if self.thread:
            self.thread.join()
        self.tick()

    def window(self, t0, t1):
        with self.lock:
            return [s for s in self.signals if t0 <= s["t"] <= t1]


def summarize_signals(sigs):
    """CPU busy, throttle events and per-GPU stats over a run of samples."""
    out = {"cpu_busy": None, "package_throttle_events": None, "gpu": {}}
    cpus = [s["cpu"] for s in sigs if s.get("cpu")]
    if len(cpus) >= 2:
        out["cpu_busy"] = cpu_busy(cpus[0], cpus[-1])
    thr = [s["throttle"] for s in sigs if s.get("throttle") is not None]
    if len(thr) >= 2:
        out["package_throttle_events"] = thr[-1] - thr[0]
    per = {}
    for s in sigs:
        for g in s.get("gpus") or []:
            per.setdefault(str(g["index"]), []).append(g)
    for idx, gs in per.items():
        pw = [g["power_w"] for g in gs if g["power_w"] is not None]
        ut = [g["util"] for g in gs if g["util"] is not None]
        out["gpu"][idx] = {
            "power_w_mean": sum(pw) / len(pw) if pw else None,
            "limit_w": next((g["limit_w"] for g in gs if g["limit_w"]), None),
            "util_mean": sum(ut) / len(ut) if ut else None,
            "util_max": max(ut) if ut else None,
            "power_cap_frac": sum(g["power_cap"] for g in gs) / len(gs),
            "thermal_frac": sum(g["thermal"] for g in gs) / len(gs),
        }
    return out


# ---------------------------------------------------------------- quiet check
def quiet_verdict(sample):
    """sample: cpu_busy, pkg_w, pl1_w, gpus [{index, power_w, limit_w, util}].
    Returns (ok, checks); a check that cannot be read has ok None."""
    checks = []

    def add(name, value, limit, ok):
        checks.append({"check": name, "value": value, "limit": limit, "ok": ok})

    b = sample.get("cpu_busy")
    add("cpu_busy", b, QUIET_CPU_BUSY_MAX, None if b is None else b <= QUIET_CPU_BUSY_MAX)
    pw, pl1 = sample.get("pkg_w"), sample.get("pl1_w")
    if pw is None or not pl1:
        add("pkg_power", pw, None, None)
    else:
        lim = QUIET_PKG_FRACTION_MAX * pl1
        add("pkg_power", pw, lim, pw <= lim)
    for g in sample.get("gpus") or []:
        u = g.get("util")
        add(f"gpu{g['index']}_util", u, QUIET_GPU_UTIL_MAX, None if u is None else u <= QUIET_GPU_UTIL_MAX)
        p, lim = g.get("power_w"), g.get("limit_w")
        if p is not None and lim:
            add(f"gpu{g['index']}_power", p, QUIET_GPU_POWER_FRACTION_MAX * lim,
                p <= QUIET_GPU_POWER_FRACTION_MAX * lim)
    return all(c["ok"] is not False for c in checks), checks


def quiet_sample(run_dir, sampler, t0, t1):
    sig = summarize_signals(sampler.window(t0, t1))
    pkg_w = None
    rp = os.path.join(run_dir, "rapl.log")
    if os.path.exists(rp):
        e = apb.rapl_energy(apb.parse_rapl(rp), t0, t1, int(os.environ.get("APB_RAPL_WRAP_UJ", "262143328850")))
        if e and e["pkg_j"] is not None and e["seconds"] > 0:
            pkg_w = e["pkg_j"] / e["seconds"]
    gpus = [{"index": int(k), "power_w": v["power_w_mean"], "limit_w": v["limit_w"], "util": v["util_max"]}
            for k, v in sorted(sig["gpu"].items())]
    return {"cpu_busy": sig["cpu_busy"], "pkg_w": pkg_w, "pl1_w": sampler.zones["pl1_w"], "gpus": gpus}


# ---------------------------------------------------------------- binding limit
def hardware_flags(hw):
    flags = []
    if not hw:
        return flags
    if (hw.get("package_throttle_events") or 0) > 0:
        flags.append("cpu_thermal_throttle")
    pw, pl1 = hw.get("pkg_w_mean"), hw.get("pl1_w")
    if pw is not None and pl1 and pw >= BIND_PKG_FRACTION * pl1:
        flags.append("cpu_package_power_limit")
    for idx, g in sorted((hw.get("gpu") or {}).items()):
        if g.get("power_cap_frac", 0) >= BIND_GPU_FLAG_FRACTION or (
                g.get("power_w_mean") is not None and g.get("limit_w")
                and g["power_w_mean"] >= BIND_GPU_POWER_FRACTION * g["limit_w"]):
            flags.append(f"gpu{idx}_power_cap")
        if g.get("thermal_frac", 0) >= BIND_GPU_FLAG_FRACTION:
            flags.append(f"gpu{idx}_thermal")
    return flags


def binding_limit(steps, max_agents):
    """What stopped the ramp: the first non-passing step's reason, or
    max_reached; plus the hardware flags seen during that step."""
    stop = next((s for s in steps if s["verdict"] != "pass"), None)
    if stop is None:
        last = steps[-1] if steps else None
        kind = "max_reached" if last and last["agents"] >= max_agents else "ramp_incomplete"
        return {"kind": kind, "at_agents": last["agents"] if last else None,
                "detail": f"every step up to N={last['agents'] if last else 0} passed",
                "hardware": hardware_flags((last or {}).get("hardware"))}
    reasons = stop.get("reasons") or []
    if stop["verdict"] == "insufficient":
        kind = "insufficient_data"
    elif any(r.startswith("p95") for r in reasons):
        kind = "slo"
    elif any(r.startswith("error rate") for r in reasons):
        kind = "errors"
    elif any(r.startswith("tool validity") for r in reasons):
        kind = "tool_validity"
    else:
        kind = "fail"
    return {"kind": kind, "at_agents": stop["agents"], "detail": "; ".join(reasons),
            "hardware": hardware_flags(stop.get("hardware"))}


# ---------------------------------------------------------------- report
def step_record(s, energy, hw):
    turns = energy.get("turns") or 0
    gpu_j = sum(energy["gpu_j"].values()) if energy.get("gpu_j") else None

    def per(v):
        return None if v is None or not turns else v / turns
    keep = ("agents", "reps", "verdict", "reasons", "n", "p50_s", "p95_s", "p99_s", "p95_ci95_s",
            "ttft_p95_s", "errors", "error_rate", "tool_valid_any", "cached_fraction",
            "window_output_tok_per_s")
    rec = {k: s[k] for k in keep}
    rec["per_rep_p95_s"] = [r["p95_s"] for r in s["per_rep"]]
    rec["j_per_turn"] = {"cpu_package": per(energy.get("pkg_j")), "gpu": per(gpu_j),
                         "gross": energy.get("gross_j_per_turn"), "net": energy.get("net_j_per_turn")}
    rec["j_per_output_token"] = {"gross": energy.get("gross_j_per_out_token"),
                                 "net": energy.get("net_j_per_out_token")}
    rec["energy"] = energy
    rec["hardware"] = hw
    return rec


def step_hardware(sampler, reps, pl1_w, energy):
    sigs = []
    events, busy = 0, []
    have_thr = False
    for _results, meta in reps:
        w = sampler.window(meta["epoch_start"] - sampler.interval, meta["epoch_end"] + sampler.interval)
        sm = summarize_signals(w)
        if sm["package_throttle_events"] is not None:
            events += sm["package_throttle_events"]
            have_thr = True
        if sm["cpu_busy"] is not None:
            busy.append(sm["cpu_busy"])
        sigs += w
    g = summarize_signals(sigs)["gpu"]
    secs = energy.get("seconds") or 0
    return {"package_throttle_events": events if have_thr else None,
            "cpu_busy_mean": sum(busy) / len(busy) if busy else None,
            "pkg_w_mean": None if energy.get("pkg_j") is None or not secs else energy["pkg_j"] / secs,
            "pl1_w": pl1_w, "gpu": g}


def confounds(rep, forwarded, sampler, engine_known):
    out = ["synthetic, teacher-forced agent trace: measures the serving path, not task quality"]
    if forwarded:
        out.append("access path is kubectl port-forward: every stream crosses it and its CPU use is on the box")
    if sampler.rapl_mode is None:
        out.append("RAPL package energy not readable (energy_uj is root-only on most kernels): "
                   "J per turn has no CPU package term; run 'sudo -v' before capacity to enable the sudo -n reader")
    else:
        out.append("CPU package energy (RAPL) includes the iGPU and excludes DRAM and discrete GPUs")
    if sampler.nvidia_seen:
        out.append("GPU energy integrates nvidia-smi power.draw of every NVIDIA GPU in the box")
    if rep["idle"]:
        out.append("net J subtracts the quiet-window power and assumes the background stays constant")
    q = rep["quiet_check"]
    if q.get("forced"):
        bad = [c["check"] for c in q["checks"] if c["ok"] is False]
        out.append("box not quiet at start (forced past: " + ", ".join(bad) + "): other work shares the box")
    thr = sum((s["hardware"] or {}).get("package_throttle_events") or 0 for s in rep["steps"])
    if thr > 0:
        out.append(f"CPU package thermal throttling during the ramp (+{thr} events): "
                   "clocks, and so latency, depend on the box's cooling and on co-running work")
    d = rep.get("drift_check")
    if d and d.get("ratio") is not None and abs(d["ratio"] - 1) > DRIFT_BAND:
        out.append(f"drift: N=1 p95 end/start = {d['ratio']:.2f}, outside 1 +/- {DRIFT_BAND}: the box changed during the run")
    if rep["binding_limit"]["kind"] == "max_reached":
        out.append(f"ramp reached --max {rep['max_agents']} without breaking the SLO: "
                   "agents per box is a lower bound")
    if not engine_known:
        out.append("endpoint given by --url: engine and server not read from the cluster")
    out.append("p95 pools requests as independent; turns of one session are correlated (per-rep p95 listed)")
    return out


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "inf" if v == float("inf") else f"{v:.{nd}f}"
    return str(v)


def print_report(rep):
    ep = rep["endpoint"]
    print(f"AGENTS PER BOX: {rep['agents_per_box']}  "
          f"(p95 turn latency <= {fmt(rep['slo_s'], 3)} s, trace {rep['trace']}, "
          f"engine {ep['engine']}, model {ep['model']})")
    if rep["agents_per_box_interpolated"] is not None:
        print(f"  interpolated: {rep['agents_per_box_interpolated']:.1f}")
    b = rep["binding_limit"]
    hw = ", ".join(b["hardware"]) or "none flagged"
    print(f"  binding limit: {b['kind']} at N={b['at_agents']} ({b['detail']}); hardware: {hw}")
    print()
    print(f"  {'N':>4} {'verdict':12} {'n':>5} {'p50 s':>8} {'p95 s':>8} {'p99 s':>8} {'err':>6} "
          f"{'J/turn':>8} {'J/turn net':>10}")
    for s in rep["steps"]:
        j = s["j_per_turn"]
        tot = j["gross"]
        if tot is None and j["gpu"] is not None:
            tot = j["gpu"]
        print(f"  {s['agents']:>4} {s['verdict']:12} {s['n']:>5} {fmt(s['p50_s']):>8} {fmt(s['p95_s']):>8} "
              f"{fmt(s['p99_s']):>8} {fmt(s['error_rate'], 3):>6} {fmt(tot, 1):>8} {fmt(j['net'], 1):>10}")
    print()
    print("  confounds:")
    for c in rep["confounds"]:
        print(f"    - {c}")
    print(f"  json: {rep['json_path']}")


# ---------------------------------------------------------------- main
def default_out(engine):
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "k3d-gpu", "capacity", f"{engine}-{time.strftime('%Y%m%d-%H%M%S')}")


def parse(argv):
    ap = argparse.ArgumentParser(prog="k3d-gpu capacity",
                                 description="Agents per box at a p95 turn-latency SLO (closed-loop agent ramp).")
    ap.add_argument("--slo", type=float, default=10.0, help="p95 turn latency SLO, s (default 10)")
    ap.add_argument("--trace", default="support", choices=sorted(apb.BASE_SESSIONS),
                    help="agent trace family (default support)")
    ap.add_argument("--max", type=int, default=32, help="largest agent count tried (default 32)")
    ap.add_argument("--engine", default="auto", choices=ENGINES, help="endpoint engine (default auto)")
    ap.add_argument("--endpoint", help="NAMESPACE/SERVICE when several endpoints match")
    ap.add_argument("--url", help="chat completions URL; skips cluster discovery")
    ap.add_argument("--model", help="model name (required with --url)")
    ap.add_argument("--reps", type=int, default=3, help="reps per step (default 3)")
    ap.add_argument("--stop-slo", type=float, help="the ramp stops at a step failing this p95 (default --slo)")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-request timeout, s (default 120)")
    ap.add_argument("--time-scale", type=float, default=1.0, help="multiplies the trace's tool waits")
    ap.add_argument("--extra-body", default=json.dumps(EXTRA_BODY),
                    help="JSON merged into every request (default: thinking off)")
    ap.add_argument("--quiet-s", type=float, default=10.0, help="quiet-check window, s (default 10)")
    ap.add_argument("--interval", type=float, default=1.0, help="sampler period, s (default 1)")
    ap.add_argument("--force", action="store_true", help="run on a box that is not quiet; states it as a confound")
    ap.add_argument("--dry-run", action="store_true", help="discover, quiet-check and print the plan; send nothing")
    ap.add_argument("--out", help="run directory (default $XDG_STATE_HOME/k3d-gpu/capacity/ENGINE-TIME)")
    ap.add_argument("--json", help="report path (default RUN_DIR/capacity.json)")
    a = ap.parse_args(argv)
    if a.url and not a.model:
        ap.error("--url needs --model")
    if a.slo <= 0 or a.reps < 1 or a.max < 1:
        ap.error("--slo, --reps and --max must be positive")
    a.stop_slo = a.stop_slo or a.slo
    if a.stop_slo < a.slo:
        ap.error("--stop-slo must be >= --slo")
    try:
        a.extra = json.loads(a.extra_body)
    except ValueError:
        ap.error("--extra-body is not JSON")
    return a


def main(argv=None):
    a = parse(argv)
    forward = None
    try:
        if a.url:
            ep = {"namespace": None, "service": None, "workload": None, "server": None,
                  "engine": a.engine if a.engine != "auto" else "unspecified", "model": a.model, "url": a.url}
        else:
            c = choose(discover(kubectl_items()), a.engine, a.endpoint)
            ep = {"namespace": c["namespace"], "service": f"{c['namespace']}/{c['service']}",
                  "workload": c["workload"], "server": c["server"], "engine": c["engine"],
                  "model": a.model or c["model"], "image": c["image"], "url": None}
            if not a.dry_run:
                forward = PortForward(c["namespace"], c["service"], c["port"])
                ep["url"] = f"http://127.0.0.1:{forward.local}{c['path']}"
            log(f"endpoint {_describe(c)}")
    except CapacityError as e:
        print(f"capacity: {e}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return run(a, ep, forward is not None)
    finally:
        if forward:
            forward.close()


def run(a, ep, forwarded):
    run_dir = a.out or default_out(ep["engine"])
    os.makedirs(run_dir, exist_ok=True)
    json_path = a.json or os.path.join(run_dir, "capacity.json")
    steps_plan = ramp_steps(a.max)
    sampler = Sampler(run_dir, a.interval)
    sampler.start()
    started = time.time()
    rep = {"schema": SCHEMA, "status": "running", "started_at": started, "finished_at": None,
           "slo_s": a.slo, "stop_slo_s": a.stop_slo, "trace": a.trace, "max_agents": a.max,
           "reps": a.reps, "steps_planned": steps_plan, "timeout_s": a.timeout,
           "time_scale": a.time_scale, "extra_body": a.extra, "endpoint": ep, "host": host_facts(),
           "run_dir": run_dir, "json_path": json_path, "agents_per_box": None,
           "agents_per_box_interpolated": None, "binding_limit": None, "steps": [],
           "idle": None, "drift_check": None, "confounds": []}

    def write():
        with open(json_path, "w") as f:
            json.dump(apb.finite(rep), f, indent=2)

    try:
        log(f"quiet check {a.quiet_s:g} s")
        q0 = sampler.tick()
        time.sleep(a.quiet_s)
        q1 = sampler.tick()
        with open(os.path.join(run_dir, "idle.json"), "w") as f:
            json.dump({"epoch_start": q0, "epoch_end": q1}, f)
        sample = quiet_sample(run_dir, sampler, q0, q1)
        ok, checks = quiet_verdict(sample)
        rep["quiet_check"] = {"ok": ok, "forced": bool(not ok and a.force), "checks": checks, "sample": sample}
        bad = [c for c in checks if c["ok"] is False]
        for c in bad:
            log(f"not quiet: {c['check']} = {fmt(c['value'], 3)} > {fmt(c['limit'], 3)}")
        if a.dry_run:
            rep["status"] = "dry_run"
            print(f"plan: N = {' '.join(map(str, steps_plan))}, {a.reps} reps/step, SLO p95 <= {a.slo:g} s, "
                  f"trace {a.trace}, endpoint {ep.get('service') or ep['url']} "
                  f"({ep['engine']}, model {ep['model']}); quiet: {'yes' if ok else 'NO'}")
            write()
            return 0 if ok or a.force else EXIT_NOT_QUIET
        if not ok and not a.force:
            rep["status"] = "refused"
            write()
            print("capacity: box not quiet; stop the other load, or pass --force to measure anyway "
                  "(the report then lists it as a confound)", file=sys.stderr)
            return EXIT_NOT_QUIET

        api_key = os.environ.get("OPENAI_API_KEY")

        def run_rep(n, r, sub=""):
            d = os.path.join(run_dir, sub) if sub else run_dir
            os.makedirs(os.path.join(d, "steps"), exist_ok=True)
            os.makedirs(os.path.join(d, "traces"), exist_ok=True)
            tr = (apb.build_trace(a.trace, n, 1000 * n + r, per_agent=1, min_sessions=2) if sub == "warmup"
                  else apb.build_trace(a.trace, n, 1000 * n + r))
            with open(os.path.join(d, "traces", f"n{n}-r{r}.json"), "w") as f:
                json.dump(tr, f)
            sampler.tick()
            results, meta = apb.run_step(tr, ep["url"], ep["model"], n, a.extra, a.timeout,
                                         a.time_scale, api_key)
            sampler.tick()
            meta["early_fail"] = apb.early_fail(results, meta, a.stop_slo)
            prefix = os.path.join(d, "steps", f"n{n:03d}-r{r}")
            with open(prefix + ".jsonl", "w") as f:
                for x in results:
                    f.write(json.dumps(x) + "\n")
            with open(prefix + ".meta.json", "w") as f:
                json.dump(meta, f, indent=2)
            rows, _ = apb.window_requests(results, n)
            p95 = apb.pctl([apb.latency(x) for x in rows], 0.95)
            log(f"N={n} rep {r}: {len(results)} turns, {sum(1 for x in results if x['error'])} errors, "
                f"window p95 {fmt(p95)} s")
            return results, meta

        log("warm-up (discarded)")
        wres, _ = run_rep(1, 0, "warmup")
        if wres and all(x["error"] for x in wres):
            rep["status"] = "endpoint_failed"
            rep["warmup_error"] = wres[0]["error"]
            write()
            print(f"capacity: every warm-up request failed: {wres[0]['error']}", file=sys.stderr)
            return EXIT_ENDPOINT

        def on_step(s):
            log(f"N={s['agents']}: {s['verdict']} (p95 {fmt(s['p95_s'])} s, n {s['n']})")

        scored = run_ramp(run_rep, steps_plan, a.reps, a.stop_slo, on_step=on_step)
        log("drift check: N=1 once more")
        run_rep(1, 9, "drift")
        sampler.stop()

        idle = apb.idle_power(run_dir)
        rep["idle"] = idle
        base_tool, steps = None, []
        for s in scored:
            reps = s.pop("_reps")
            final = apb.score_step(reps, a.slo, base_tool)
            if s["verdict"] == "fail" and final["verdict"] != "fail":
                final["verdict"], final["reasons"] = "fail", s["reasons"]
            if base_tool is None:
                base_tool = final["tool_valid_any"]
            energy = apb.energy_for(run_dir, reps, idle)
            hw = step_hardware(sampler, reps, sampler.zones["pl1_w"], energy)
            steps.append(step_record(final, energy, hw))
        best, interp = agents_per_box([{**s, "_slo": a.slo} for s in steps])
        rep["steps"] = steps
        rep["agents_per_box"], rep["agents_per_box_interpolated"] = best, interp
        rep["binding_limit"] = binding_limit(steps, a.max)
        rep["apb_at_secondary_slo_p95_only"] = {
            str(x): apb.apb_from_p95([(s["agents"], s["p95_s"] if s["verdict"] != "insufficient" else None)
                                      for s in steps], x)
            for x in apb.SECONDARY_SLOS if x >= a.slo}
        drift = apb.load_run(os.path.join(run_dir, "drift")).get(1)
        if drift and steps:
            d = apb.score_step(drift, a.slo)
            p0 = steps[0]["p95_s"]
            rep["drift_check"] = {"p95_start_s": p0, "p95_end_s": d["p95_s"],
                                  "ratio": None if not p0 or d["p95_s"] in (None, apb.INF) or p0 == apb.INF
                                  else d["p95_s"] / p0}
        rep["status"] = "ok"
        rep["finished_at"] = time.time()
        rep["confounds"] = confounds(rep, forwarded, sampler, ep.get("server") is not None)
        write()
        print_report(rep)
        return 0
    finally:
        sampler.stop_ev.set()
        if rep["status"] == "running":  # interrupted or crashed: keep what is known
            rep["status"] = "aborted"
            write()


if __name__ == "__main__":
    sys.exit(main())

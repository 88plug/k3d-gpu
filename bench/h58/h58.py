#!/usr/bin/env python3
"""h58.py: EXPERIMENTS.md H58 (the zero-touch thesis on crescent) helpers and scorer, as
amendment R1 defines them. Python 3 standard library only.

  h58.py pins OUT.json                   P1 item 3: each pinned repo's Hub sha; exit 3 if one moved,
                                         2 if one could not be read
  h58.py env                             the K3D_GPU_* variables set here; exit 1 if there is one
  h58.py step STEPS KIND RC T0 T1 LOG -- CMD...
                                         append one steps.jsonl line (KIND user, record, harness,
                                         setup or teardown)
  h58.py interlock KUBECONFIG CONTEXT DIR  until TERM: every 0.5 s, delete any Deployment or pod of the
                                         cluster that asks for an accelerator (amendment R1, the GPU
                                         interlock); DIR/beat, DIR/events.jsonl, one JSON per object
  h58.py placement LOG [DEPLOY.json]     one agent up's placement from its output and Deployment
  h58.py decode TURNS ARM                decode tok/s of one arm's turn records (the (c) statistic)
  h58.py success ATTEMPTS ARM            within-budget success of one arm's attempts
  h58.py squeeze-bytes FIT [MEMINFO]     the stress-ng --vm-bytes that takes MemAvailable under the
                                         guard's floor by SQUEEZE_MARGIN
  h58.py squeeze DIR APP PRE.json        clause (f): the squeeze, its stop rule and its records
                                         (PRE.json: the containers running when the run started)
  h58.py redact-events FILE              blank secret-looking labels in a docker events JSONL, in place
  h58.py score OUT                       the clause table and the verdict, written to OUT/score.json

Decode tok/s (clause (c)): over an arm's turn records with status 200, no error, at least 2
completion tokens and e2e_s > ttft_s, sum(completion_tokens - 1) / sum(e2e_s - ttft_s). H50's A1
(bench/eatflow/results/2026-10-20/H50/pairs) gives 20.50 tok/s over 54 turns, and 19 of 24
attempts within budget (0.7917)."""
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
GIB = 1024 ** 3

# Amendment P1: the three repos H58's commands name, at the revision each was pinned to.
PINS = {"Intel/Qwen3.8-Flash-Next-W4A16-AutoRound": "4c67bf686b7f7fd386bae6b07ab59e8ff1d5b897",
        "google/gemma-4-26B-A4B-it": "4d7ae4984b7db7de8f8457170b3f1a419ee76d52",
        "Qwen/Qwen3.8-27B": "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"}
GEMMA, QWEN27, FLASH = "google/gemma-4-26B-A4B-it", "Qwen/Qwen3.8-27B", "Intel/Qwen3.8-Flash-Next-W4A16-AutoRound"
# Each model's directory under OUT, and the substring a served variant of it carries.
MODELS = {GEMMA: ("m-gemma", "gemma-4-26b-a4b"), QWEN27: ("m-qwen27b", "qwen3.8-27b"),
          FLASH: ("m-flashnext", "flash-next")}

# The user steps, in run order: the pre-registered commands, with no other argument (clause (a)).
UP, DETECT = ["k3d-gpu", "up"], ["k3d-gpu", "detect"]
USER_FORMS = [UP, DETECT] + [["k3d-gpu", "agent", "up", "--model", m] for m in (GEMMA, QWEN27, FLASH)]
GEMMA_UP = USER_FORMS[2]

# The (c) baseline: H50's A1 (H58 pre-registration: H50 ran a valid hold), its records.
H50_PAIRS = os.path.join(REPO, "bench", "eatflow", "results", "2026-10-20", "H50", "pairs")
BASE_ARM = "A1"
DECODE_RATIO = 0.90      # decode >= 0.90x the baseline's
SUCCESS_POINTS = 0.05    # success no more than 5 points under the baseline's
MIN_ATTEMPTS = 16        # complete B24 attempts needed to read (c)

# Clause (d): what H52 and H52b found usable on crescent. IAA (no work queue host-setup can add,
# PR #210) and QAT (the plugin crash-looped) were not made usable by H52b, so they are not read.
XE_CARDS = 4
AMX_LABELS = ("AMXTILE", "AMXBF16", "AMXINT8", "AMXFP16")
DSA_RES = ("dsa.intel.com/wq-user-shared", "dsa.intel.com/wq-user-dedicated")

# Clause (e): the fit's placements (console/fit.py) each model may land on.
GPU_OR_CPU = ("gpu", "gpu-tp", "gpu-split", "cpu")
EXPERTS_IN_DRAM = ("moe-offload",)
ALLOWED = {GEMMA: GPU_OR_CPU, QWEN27: GPU_OR_CPU, FLASH: EXPERTS_IN_DRAM}

SQUEEZE_MARGIN = 4 * GIB  # how far under the guard's floor the squeeze takes MemAvailable

ACCEL = re.compile(r"^(gpu\.intel\.com/|nvidia\.com/|habana\.ai/|npu\.intel\.com/|amd\.com/)")


def read_jsonl(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_text(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def append(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


# --- P1 item 3 -----------------------------------------------------------------------------

HF_API = os.environ.get("H58_HF_API", "https://huggingface.co/api/models")   # the tests serve a stand-in


def hub_sha(repo, opener=urllib.request.urlopen):
    with opener("%s/%s" % (HF_API, repo), timeout=30) as r:
        return json.load(r).get("sha")


def check_pins(opener=urllib.request.urlopen):
    """({repo: {pin, sha, equal|error}}, rc): rc 3 when a repo moved, 2 when one was not read."""
    got, moved, unread = {}, False, False
    for repo, pin in PINS.items():
        try:
            sha = hub_sha(repo, opener)
        except (OSError, ValueError) as e:
            got[repo] = {"pin": pin, "sha": None, "error": "%s: %s" % (type(e).__name__, e)}
            unread = True
            continue
        got[repo] = {"pin": pin, "sha": sha, "equal": sha == pin}
        moved = moved or sha != pin
    return got, (3 if moved else 2 if unread else 0)


def env_flags(environ):
    """The K3D_GPU_* variables in an environment: each one would be a flag on a user step."""
    return sorted(k for k in environ if k.startswith("K3D_GPU_"))


# --- The GPU interlock ----------------------------------------------------------------------

def accel_keys(podspec):
    """What a pod spec asks for that would put work on an accelerator: extended resources of a
    GPU/NPU vendor and resource claims (DRA). A /dev/dri hostPath is not one: k3d-gpu's Intel GPU
    plugin mounts its cards that way to advertise them and runs no work on them, and k3d-gpu's
    agents reach a card only through the plugin's resource (amendment R2)."""
    keys = set()
    for c in (podspec.get("containers") or []) + (podspec.get("initContainers") or []):
        res = c.get("resources") or {}
        for part in ("limits", "requests"):
            keys.update(k for k in (res.get(part) or {}) if ACCEL.match(k))
    if podspec.get("resourceClaims"):
        keys.add("resourceClaims")
    return sorted(keys)


SYSTEM_NS = ("kube-system",)   # k3d-gpu's own components (device plugins): never touched
AGENT_NS = "k3d-gpu-agent"     # where agent up deploys


def interlock_targets(items):
    """[(kind, namespace, name, uid, keys, obj)] of the Deployments and pods outside kube-system
    that ask for an accelerator. A pod owned by a ReplicaSet is caught through its Deployment too."""
    out = []
    for o in items:
        kind = o.get("kind")
        md = o.get("metadata") or {}
        if md.get("namespace") in SYSTEM_NS:
            continue
        if kind == "Deployment":
            spec = ((o.get("spec") or {}).get("template") or {}).get("spec") or {}
        elif kind == "Pod":
            spec = o.get("spec") or {}
        else:
            continue
        keys = accel_keys(spec)
        if keys:
            out.append((kind, md.get("namespace", "default"), md.get("name"), md.get("uid"), keys, o))
    return out


def kubectl_run(args, timeout=20):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def interlock_pass(kubeconfig, context, d, seen, run=kubectl_run, clock=time.time, kubectl="kubectl"):
    """One look at the cluster: delete each new accelerator-asking object. Returns the rows
    recorded (also appended to d/events.jsonl). d/beat says whether the cluster answered."""
    kc = [kubectl, "--kubeconfig", kubeconfig, "--context", context]
    rc, out, err = run(kc + ["get", "deployments,pods", "-A", "-o", "json"])
    with open(os.path.join(d, "beat"), "w") as f:
        f.write("%.3f %s\n" % (clock(), "ok" if rc == 0 else "unreachable"))
    if rc != 0:
        return []
    try:
        items = json.loads(out).get("items") or []
    except ValueError:
        return []
    rows = []
    for kind, ns, name, uid, keys, obj in interlock_targets(items):
        if uid in seen:
            continue
        seen.add(uid)
        t = clock()
        with open(os.path.join(d, "%s-%s-%s-%d.json" % (kind.lower(), ns, name, int(t))), "w") as f:
            json.dump(obj, f, indent=1)
        base = kc + ["-n", ns]
        if kind == "Deployment":
            r1 = run(base + ["scale", "deployment/%s" % name, "--replicas=0"])[0]
            r2 = run(base + ["delete", "deployment/%s" % name, "--wait=false"])[0]
        else:
            r1 = 0
            r2 = run(base + ["delete", "pod/%s" % name, "--grace-period=0", "--force", "--wait=false"])[0]
        ann = ((((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("annotations")
               if kind == "Deployment" else (obj.get("metadata") or {}).get("annotations")) or {}
        row = {"t": round(t, 3), "kind": kind, "namespace": ns, "name": name, "uid": uid, "keys": keys,
               "fit": ann.get("k3d-gpu.io/agent-fit"), "device": ann.get("k3d-gpu.io/agent-device"),
               "scale_rc": r1, "delete_rc": r2}
        append(os.path.join(d, "events.jsonl"), row)
        rows.append(row)
    return rows


def run_interlock(kubeconfig, context, d, step=0.5):
    os.makedirs(d, exist_ok=True)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    seen = set()
    while not stop:
        interlock_pass(kubeconfig, context, d, seen)
        time.sleep(step)
    return 0


# --- Clause (e): one agent up's placement ---------------------------------------------------

FIT_OK = re.compile(r"fit: (?P<name>\S+) on (?P<where>.+?) via (?P<engine>[\w.]+), context (?P<ctx>\d+) x (?P<seqs>\d+) seqs")
FIT_NO = re.compile(r"fit: (?P<name>\S+) does not fit this host: (?P<why>[^;\n]+)")
def served_model(deploy):
    """The model a rendered Deployment serves: OVMS --source_model=, llama-server --model or
    --model-url, vLLM's first argument (vllm serve MODEL)."""
    spec = ((deploy.get("spec") or {}).get("template") or {}).get("spec") or {}
    for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
        args = [str(a) for a in (c.get("command") or []) + (c.get("args") or [])]
        for i, a in enumerate(args):
            if a.startswith("--source_model="):
                return a.split("=", 1)[1]
            if a.startswith("--model=") or a.startswith("--model-url="):
                return a.split("=", 1)[1]
            if a in ("--model", "-m", "--model-url", "-mu") and i + 1 < len(args):
                return args[i + 1]
        cargs = [str(a) for a in c.get("args") or []]
        if c.get("name") == "vllm" and cargs and not cargs[0].startswith("-"):
            return cargs[0]
    return None


def placement(log, deploy=None, rc=None, blocked=False):
    """{placement, fit, device, served, why, accel, landed, blocked} of one agent up. A refusal
    is read from the launcher's own words ("does not fit this host"). Otherwise the Deployment is
    the record: as the interlock saved it (blocked), or as agent up left it when it exited 0
    (agent up deletes every other agent Deployment, so the one serving is its own). Any other
    exit is an error whose reason is the log's last line."""
    p = {"placement": None, "fit": None, "device": None, "served": None, "why": None, "accel": [],
         "landed": bool(rc == 0 and deploy is not None and not blocked), "blocked": bool(blocked), "rc": rc}
    m = FIT_OK.search(log or "")
    if m:
        p["fit_summary"] = m.group(0)
    no = FIT_NO.search(log or "")
    if no and not blocked:
        p.update(placement="refused", served=no.group("name"), why=no.group("why").strip(), landed=False)
        return p
    if deploy is not None and (rc == 0 or blocked):
        tmpl = (deploy.get("spec") or {}).get("template") or {}
        ann = (tmpl.get("metadata") or {}).get("annotations") or {}
        p["fit"] = ann.get("k3d-gpu.io/agent-fit")
        p["device"] = ann.get("k3d-gpu.io/agent-device")
        p["accel"] = accel_keys(tmpl.get("spec") or {})
        p["served"] = served_model(deploy)
        if p["fit"]:
            p["placement"] = p["fit"].split()[0]
        else:
            p["placement"] = "unfitted-gpu" if p["accel"] else "unfitted-cpu"
        return p
    if rc not in (None, 0):
        lines = [x for x in (log or "").splitlines() if x.strip()]
        p.update(placement="error", why=lines[-1][-300:] if lines else "exit %s" % rc, landed=False)
    return p


def clause_e(model, p):
    """'holds', 'fails' or 'inconclusive' for one model, with the reason."""
    want, tag = ALLOWED[model], MODELS[model][1]
    served_ok = bool(p.get("served")) and tag in p["served"].lower()
    ok_render = p.get("placement") in want and served_ok and bool(p.get("fit"))
    why = "placement %s, served %s, fit %s" % (p.get("placement"), p.get("served"), p.get("fit"))
    if p.get("blocked"):
        return ("inconclusive" if ok_render else "fails"), why + "; not landed: the GPU interlock removed it"
    if p.get("landed"):
        return ("holds" if ok_render else "fails"), why + "; landed"
    return "fails", why + "; did not land" + (": %s" % p["why"] if p.get("why") else "")


# --- Clause (c) ------------------------------------------------------------------------------

def decode_rate(turns, arm):
    """(tok/s, turns used): sum(completion_tokens - 1) / sum(e2e_s - ttft_s) over arm's turns."""
    tok = dt = 0.0
    n = 0
    for r in turns:
        if r.get("arm") != arm or r.get("status") != 200 or r.get("error"):
            continue
        c = r.get("completion_tokens") or 0
        e2e, ttft = r.get("e2e_s"), r.get("ttft_s")
        if c < 2 or e2e is None or ttft is None or e2e <= ttft:
            continue
        tok += c - 1
        dt += e2e - ttft
        n += 1
    return (tok / dt if dt > 0 else None), n


def success(attempts, arm):
    """(passed within budget, complete attempts): void attempts do not count."""
    rows = [a for a in attempts if a.get("arm") == arm and a.get("outcome") != "void"]
    return sum(1 for a in rows if a.get("outcome") == "pass_budget"), len(rows)


def baseline(pairs_dir=H50_PAIRS, arm=BASE_ARM):
    rate, n = decode_rate(read_jsonl(os.path.join(pairs_dir, "turns.jsonl")), arm)
    k, m = success(read_jsonl(os.path.join(pairs_dir, "attempts.jsonl")), arm)
    return {"source": os.path.relpath(pairs_dir, REPO), "arm": arm, "decode_tok_s": rate, "turns": n,
            "passed": k, "attempts": m, "success": (k / m if m else None)}


# --- Clause (f) ------------------------------------------------------------------------------

def meminfo(text):
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        f = v.split()
        if f and f[0].isdigit():
            out[k.strip()] = int(f[0]) * (1024 if len(f) > 1 and f[1] == "kB" else 1)
    return out


def squeeze_bytes(fit, mem_text, margin=SQUEEZE_MARGIN):
    """Bytes for stress-ng to hold so MemAvailable lands `margin` under the guard's floor
    (fit.json guard.mem_available_floor, half the fit's host reserve)."""
    floor = ((fit or {}).get("guard") or {}).get("mem_available_floor")
    avail = meminfo(mem_text).get("MemAvailable")
    if not floor or not avail:
        raise ValueError("no guard floor in the fit, or no MemAvailable")
    want = avail - floor + margin
    if want <= 0:
        raise ValueError("MemAvailable %d is already under the floor %d" % (avail, floor))
    return int(want)


def psi(text):
    """{some10, full10} from /proc/pressure/memory."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if f and f[0] in ("some", "full"):
            for kv in f[1:]:
                k, _, v = kv.partition("=")
                if k == "avg10":
                    out[f[0] + "10"] = float(v)
    return out


def docker_containers(run=subprocess.run):
    """[{name, restarts, running, oom_killed, health}] of every running container."""
    ids = run(["docker", "ps", "-q"], capture_output=True, text=True).stdout.split()
    if not ids:
        return []
    js = json.loads(run(["docker", "inspect"] + ids, capture_output=True, text=True).stdout or "[]")
    return [{"name": c["Name"].lstrip("/"), "restarts": c["RestartCount"], "running": c["State"]["Running"],
             "oom_killed": c["State"]["OOMKilled"], "health": (c["State"].get("Health") or {}).get("Status")}
            for c in js]


PROBE = {"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 4,
         "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
PAIR_PORTS = (30000, 30001)


def probe_pair(port, opener=urllib.request.urlopen, clock=time.monotonic):
    """(seconds, ok) of one 4-token chat on a serving pair's engine port."""
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(PROBE).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = clock()
    try:
        with opener(req, timeout=60) as r:
            ok = bool(json.load(r).get("choices"))
    except (OSError, ValueError):
        ok = False
    return clock() - t0, ok


def median(xs):
    s = sorted(xs)
    n = len(s)
    return None if not n else s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def stop_reason(pre, now, lat, pre_med, fails, t, window=60.0):
    """The stop rule (pre-registration (f)): a pre-existing container no longer healthy, a pair
    failing its probe twice in a row, or a pair's median probe latency over the last `window`
    seconds at twice its pre-squeeze median or more. None when the squeeze may go on."""
    cur = {c["name"]: c for c in now}
    for c in pre:
        if not c.get("running"):
            continue
        n = cur.get(c["name"])
        if n is None or not n.get("running"):
            return "container %s stopped" % c["name"]
        if n.get("restarts") != c.get("restarts"):
            return "container %s restarted" % c["name"]
        if n.get("health") == "unhealthy" and c.get("health") != "unhealthy":
            return "container %s unhealthy" % c["name"]
    for port, k in fails.items():
        if k >= 2:
            return "pair :%d failed its probe twice" % port
    for port, m in pre_med.items():
        recent = [s for (ts, p, s, ok) in lat if p == port and ok and ts >= t - window]
        if m and len(recent) >= 2 and median(recent) >= 2 * m:
            return "pair :%d latency %.2f s >= 2 x %.2f s" % (port, median(recent), m)
    return None


GUARD_ACTED = re.compile(r"agent guard: .*(\d+ seqs x \d+ tokens|scaled to 0)")


def run_squeeze(d, app, sn, state, pre_names, hold_s=420, pre_s=120, step=5.0, probe_s=15.0, after_s=60.0,
                run=subprocess.run, popen=subprocess.Popen, opener=urllib.request.urlopen,
                clock=time.time, sleep=time.sleep, proc="/proc"):
    """Clause (f), amendment R1. pre_names: the containers running when the run started (the
    pre-existing services; this run's own k3d nodes are not among them). Records into d;
    returns the status (also d/status.json)."""
    import threading
    os.makedirs(d, exist_ok=True)
    st = {"ran": False, "app": app}

    def done(**kw):
        st.update(kw)
        with open(os.path.join(d, "status.json"), "w") as f:
            json.dump(st, f, indent=1, sort_keys=True)
        return st
    fit = read_json(os.path.join(state, "agent-fit.json"))
    pre_c = [c for c in docker_containers(run) if c["name"] in pre_names]
    with open(os.path.join(d, "containers-pre.json"), "w") as f:
        json.dump(pre_c, f, indent=1)
    dmesg = lambda: run(["sudo", "-n", "dmesg"], capture_output=True, text=True).stdout  # noqa: E731
    with open(os.path.join(d, "dmesg-pre.txt"), "w") as f:
        f.write(dmesg())
    glog = os.path.join(state, "agent-guard.log")
    goff = os.path.getsize(glog) if os.path.exists(glog) else 0
    lat, fails, lock, stop_ev = [], {p: 0 for p in PAIR_PORTS}, threading.Lock(), threading.Event()

    def prober():
        while not stop_ev.is_set():
            for p in PAIR_PORTS:
                s, ok = probe_pair(p, opener)
                row = (clock(), p, s, ok)
                with lock:
                    lat.append(row)
                    fails[p] = 0 if ok else fails[p] + 1
                append(os.path.join(d, "latency.jsonl"), {"t": round(row[0], 3), "port": p, "s": round(s, 3), "ok": ok})
            stop_ev.wait(probe_s)

    def sample(phase):
        mi = meminfo(read_text(os.path.join(proc, "meminfo")))
        ps = psi(read_text(os.path.join(proc, "pressure", "memory")))
        row = {"t": round(clock(), 3), "phase": phase, "mem_available": mi.get("MemAvailable"), **ps}
        append(os.path.join(d, "mem.jsonl"), row)
        return row

    def guard_lines():
        txt = read_text(glog)
        return txt[goff:] if len(txt) >= goff else txt
    th = threading.Thread(target=prober, daemon=True)
    th.start()
    t0 = clock()
    while clock() - t0 < pre_s:
        sample("pre")
        sleep(step)
    with lock:
        pre_med = {p: median([s for (_, q, s, ok) in lat if q == p and ok]) for p in PAIR_PORTS}
    st["pre_latency_median_s"] = {str(k): v for k, v in pre_med.items()}
    mem_text = read_text(os.path.join(proc, "meminfo"))
    try:
        nbytes = squeeze_bytes(fit, mem_text)
    except ValueError as e:
        stop_ev.set()
        return done(why="no squeeze size: %s" % e)
    floor = fit["guard"]["mem_available_floor"]
    unit = "h58-squeeze-%d" % os.getpid()
    cmd = ["sudo", "-n", "systemd-run", "--quiet", "--scope", "--unit", unit, "-p", "MemoryMax=%d" % (nbytes + 2 * GIB),
           "-p", "MemorySwapMax=0", "--", "choom", "-n", "1000", "--", sn, "--vm", "1", "--vm-bytes", str(nbytes),
           "--vm-keep", "--timeout", "%ds" % hold_s, "--metrics-brief"]
    st.update(ran=True, bytes=nbytes, floor=floor, unit=unit, cmd=cmd, started=round(clock(), 3))
    log = open(os.path.join(d, "stress-ng.log"), "w")
    p = popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    stopped, acted = None, None
    while p.poll() is None:
        sleep(step)
        row = sample("squeeze")
        with lock:
            reason = stop_reason(pre_c, docker_containers(run), list(lat), pre_med, dict(fails), clock())
        if acted is None and GUARD_ACTED.search(guard_lines()):
            acted = round(clock(), 3)
        if reason:
            stopped = reason
        elif acted is not None and clock() - acted >= after_s:
            st["ended_by"] = "guard acted %.0f s before" % after_s
        elif clock() - st["started"] >= hold_s + 30:
            st["ended_by"] = "hold time"
        else:
            continue
        run(["sudo", "-n", "systemctl", "stop", unit + ".scope"], capture_output=True, text=True)
        break
    try:
        p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
    log.close()
    sq = [r["mem_available"] for r in read_jsonl(os.path.join(d, "mem.jsonl"))
          if r.get("phase") == "squeeze" and r.get("mem_available")]
    st.update(ended=round(clock(), 3), stress_rc=p.returncode, stopped=stopped, guard_acted=acted,
              min_mem_available=min(sq) if sq else None)
    t1 = clock()
    while clock() - t1 < after_s:
        sample("after")
        sleep(step)
    stop_ev.set()
    th.join(timeout=90)
    with open(os.path.join(d, "guard.log"), "w") as f:
        f.write(guard_lines())
    with open(os.path.join(d, "containers-post.json"), "w") as f:
        json.dump([c for c in docker_containers(run) if c["name"] in pre_names], f, indent=1)
    with open(os.path.join(d, "dmesg-post.txt"), "w") as f:
        f.write(dmesg())
    ev = run(["docker", "events", "--since", "%d" % int(t0), "--until", "%d" % int(clock()), "--filter",
              "type=container", "--format", "{{json .}}"], capture_output=True, text=True).stdout
    with open(os.path.join(d, "docker-events.jsonl"), "w") as f:
        f.write(redact_events(ev))
    return done()


SECRET_KEY = re.compile(r"(?i)token|secret|passw|api_?key|credential")


def redact_events(text):
    """docker events JSON lines with every label whose name looks like a secret blanked (k3d
    labels its node containers with the cluster's join token, k3d.cluster.token)."""
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        attrs = (e.get("Actor") or {}).get("Attributes") or {}
        for k in list(attrs):
            if SECRET_KEY.search(k):
                attrs[k] = "REDACTED"
        out.append(json.dumps(e, sort_keys=True))
    return "".join(x + "\n" for x in out)


OOM_LINE = re.compile(r"oom-kill:.*task_memcg=(?P<memcg>[^,\s]*).*task=(?P<task>[^,\s]+)")


def oom_kills(pre, post):
    """The kernel's oom-kill lines (one per kill, global or cgroup) in post that pre does not
    have: raw dmesg, whose monotonic stamps do not move between reads."""
    seen = set(pre.splitlines())
    return [line for line in post.splitlines() if line not in seen and OOM_LINE.search(line)]


def outside_ours(kills):
    """The kills that hit neither stress-ng (the squeeze) nor a pod of this run's cluster (a
    kubepods cgroup: the node is a k3d container, whose pods' cgroups sit under kubepods)."""
    out = []
    for line in kills:
        m = OOM_LINE.search(line)
        if m.group("task").startswith("stress-ng") or "kubepods" in m.group("memcg"):
            continue
        out.append(line)
    return out


def clause_f(d):
    """(sub-clause 0 kills, sub-clause guard lowers admission, detail) from OUT/squeeze."""
    st = read_json(os.path.join(d, "status.json"))
    if not st:
        return "inconclusive", "inconclusive", "not run"
    if not st.get("ran"):
        why = st.get("why") or "not run"
        return "inconclusive", "inconclusive", why
    kills = outside_ours(oom_kills(read_text(os.path.join(d, "dmesg-pre.txt")),
                                   read_text(os.path.join(d, "dmesg-post.txt"))))
    pre = {c["name"]: c for c in read_json(os.path.join(d, "containers-pre.json"), []) or []}
    post = {c["name"]: c for c in read_json(os.path.join(d, "containers-post.json"), []) or []}
    hurt = [n for n, c in pre.items() if c.get("running") and (
        n not in post or not post[n].get("running") or post[n].get("restarts") != c.get("restarts")
        or (post[n].get("oom_killed") and not c.get("oom_killed")))]
    events = [e for e in read_jsonl(os.path.join(d, "docker-events.jsonl"))
              if e.get("Action") in ("oom", "die") and ((e.get("Actor") or {}).get("Attributes") or {}).get("name") in pre]
    zero = "holds" if not kills and not hurt and not events else "fails"
    guard = read_text(os.path.join(d, "guard.log"))
    lowered = re.search(r"agent guard: .*(emergency|pressure|PSI|MemAvailable).*\d+ seqs x \d+ tokens", guard) \
        or re.search(r"agent guard: .*scaled to 0", guard)
    if st.get("stopped"):
        adm = "inconclusive"
    else:
        adm = "holds" if lowered else "fails"
    detail = "kills outside ours %d, containers hurt %s, docker oom/die %d, guard %s, stop rule %s" % (
        len(kills), hurt or "none", len(events), "lowered" if lowered else "no step", st.get("stopped") or "no")
    return zero, adm, detail


# --- The verdict ----------------------------------------------------------------------------

def validity(out):
    """(valid, reasons): the run's preconditions (pre-registration Clean state, P1, R1)."""
    why = []
    pins = read_json(os.path.join(out, "pins.json"), {}) or {}
    if not pins or not all(v.get("equal") for v in pins.values()):
        why.append("P1: a pinned repo moved or was not read")
    if read_text(os.path.join(out, "inventory-diff.txt")).strip():
        why.append("host differs from H52b's revert (inventory-diff.txt)")
    if read_text(os.path.join(out, "clusters-pre.txt")).strip():
        why.append("a k3d cluster existed at the start")
    c0, c1 = read_text(os.path.join(out, "commit.txt")).split(), read_text(os.path.join(out, "commit-end.txt")).split()
    if not c0 or c0 != c1:
        why.append("the commit changed during the run, or was not recorded")
    if read_text(os.path.join(out, "dirty.txt")).strip():
        why.append("the checkout had local changes")
    # Amendment R2: the interlock may remove only what agent up deployed. Anything else it removed
    # was k3d-gpu's own, so the run would measure the harness, not k3d-gpu.
    other = [e for e in read_jsonl(os.path.join(out, "interlock", "events.jsonl")) if e.get("namespace") != AGENT_NS]
    if other:
        why.append("the GPU interlock deleted %d object(s) outside %s (k3d-gpu's own)" % (len(other), AGENT_NS))
    return (not why), why


def clause_ab(steps, out):
    """(a) flags on the user steps; (b) manual steps the banking flow needed."""
    flags = []
    for s in steps:
        if s.get("kind") != "user":
            continue
        if s.get("cmd") not in USER_FORMS:
            flags.append("argv %s" % s.get("cmd"))
        flags += ["%s on %s" % (v, " ".join(s.get("cmd") or [])) for v in s.get("env_k3d_gpu") or []]
    flags += ["manual step: %s" % " ".join(s.get("cmd") or []) for s in steps if s.get("kind") == "manual"]
    a = ("holds" if not flags else "fails"), flags
    # (b): each user step of the flow (up, agent up --model Gemma) that exited non-zero stopped it.
    flow = [s for s in steps if s.get("kind") == "user" and s.get("cmd") in (UP, GEMMA_UP)]
    stops = ["%s exited %s" % (" ".join(s["cmd"]), s.get("rc")) for s in flow if s.get("rc") != 0]
    gem = read_json(os.path.join(out, MODELS[GEMMA][0], "placement.json"), {}) or {}
    manual = [s for s in steps if s.get("kind") == "manual"]
    if gem.get("blocked") and not stops:
        b = "inconclusive", ["the GPU interlock removed Gemma's server"]
    elif stops or manual:
        b = "fails", stops + ["manual: %s" % " ".join(s.get("cmd") or []) for s in manual]
    elif not read_json(os.path.join(out, "b24", "status.json"), {}).get("ran"):
        b = "inconclusive", ["the banking flow did not run (harness)"]
    else:
        b = "holds", []
    return a, b


def clause_c(out, base):
    gem = read_json(os.path.join(out, MODELS[GEMMA][0], "placement.json"), {}) or {}
    st = read_json(os.path.join(out, "b24", "status.json"), {}) or {}
    if not base.get("decode_tok_s") or not base.get("attempts"):
        return "inconclusive", "no valid baseline", {}
    if gem.get("blocked"):
        return "inconclusive", "the GPU interlock removed Gemma's server", {}
    if not gem.get("landed"):
        return "fails", "agent up --model %s did not serve (%s)" % (GEMMA, gem.get("placement")), \
            {"decode_tok_s": None, "passed": 0, "attempts": 0}
    if not st.get("ran"):
        return "inconclusive", "the banking flow did not run: %s" % (st.get("why") or "no record"), {}
    sd = os.path.join(out, "b24", "pairs")
    rate, n = decode_rate(read_jsonl(os.path.join(sd, "turns.jsonl")), st.get("arm", "Z"))
    k, m = success(read_jsonl(os.path.join(sd, "attempts.jsonl")), st.get("arm", "Z"))
    got = {"decode_tok_s": rate, "turns": n, "passed": k, "attempts": m, "success": (k / m if m else None)}
    if m < MIN_ATTEMPTS or rate is None:
        return "inconclusive", "%d complete attempts (need %d)" % (m, MIN_ATTEMPTS), got
    dec_ok = rate >= DECODE_RATIO * base["decode_tok_s"]
    suc_ok = got["success"] >= base["success"] - SUCCESS_POINTS - 1e-12
    return ("holds" if dec_ok and suc_ok else "fails"), \
        "decode %.2f vs %.2f x %.2f; success %d/%d vs %.4f - %.2f" % (
            rate, DECODE_RATIO, base["decode_tok_s"], k, m, base["success"], SUCCESS_POINTS), got


def clause_d(out):
    node = read_json(os.path.join(out, "node.json"))
    if not node:
        return "fails", ["no node: k3d-gpu up did not make a cluster"], {}
    items = node.get("items") if "items" in node else [node]
    alloc, labels = {}, {}
    for n in items:
        for k, v in ((n.get("status") or {}).get("allocatable") or {}).items():
            try:
                alloc[k] = alloc.get(k, 0) + int(v)
            except ValueError:
                pass
        labels.update((n.get("metadata") or {}).get("labels") or {})
    miss = []
    xe = alloc.get("gpu.intel.com/xe", 0)
    if xe < XE_CARDS:
        miss.append("Arc Pro B70: gpu.intel.com/xe %d of %d" % (xe, XE_CARDS))
    for f in AMX_LABELS:
        if labels.get("feature.node.kubernetes.io/cpu-cpuid.%s" % f) != "true":
            miss.append("AMX: no %s label" % f)
    dsa = sum(alloc.get(r, 0) for r in DSA_RES)
    if dsa <= 0:
        miss.append("DSA: no dsa.intel.com work queue advertised")
    return ("holds" if not miss else "fails"), miss, {"xe": xe, "dsa": dsa}


def score(out):
    valid, invalid_why = validity(out)
    steps = read_jsonl(os.path.join(out, "steps.jsonl"))
    base = baseline()
    a, b = clause_ab(steps, out)
    c = clause_c(out, base)
    d = clause_d(out)
    e = {}
    for model, (sub, _tag) in MODELS.items():
        p = read_json(os.path.join(out, sub, "placement.json"))
        e[model] = clause_e(model, p) if p else ("fails", "no agent up record")
    e_all = [v[0] for v in e.values()]
    e_v = "fails" if "fails" in e_all else "holds" if all(v == "holds" for v in e_all) else "inconclusive"
    f0, f1, fd = clause_f(os.path.join(out, "squeeze"))
    clauses = {"a": a[0], "b": b[0], "c": c[0], "d": d[0], "e": e_v, "f_kills": f0, "f_guard": f1}
    if not valid:
        verdict = "NOT VALID"
    elif "fails" in clauses.values():
        verdict = "REFUTED"
    elif all(v == "holds" for v in clauses.values()):
        verdict = "SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    res = {"hypothesis": "H58", "valid": valid, "invalid_why": invalid_why, "verdict": verdict,
           "clauses": clauses, "baseline": base,
           "a": {"flags": a[1]}, "b": {"stops": b[1]}, "c": {"why": c[1], "measured": c[2]},
           "d": {"missing": d[1], "read": d[2]}, "e": {m: {"verdict": v[0], "why": v[1]} for m, v in e.items()},
           "f": {"detail": fd}}
    with open(os.path.join(out, "score.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
    return res


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "pins" and len(rest) == 1:
        got, rc = check_pins()
        with open(rest[0], "w") as f:
            json.dump(got, f, indent=1, sort_keys=True)
        print(json.dumps(got, sort_keys=True))
        return rc
    if cmd == "env" and not rest:
        names = env_flags(os.environ)
        print(" ".join(names))
        return 1 if names else 0
    if cmd == "step" and "--" in rest and rest.index("--") == 6:
        steps, kind, rc, t0, t1, log = rest[:6]
        append(steps, {"kind": kind, "rc": int(rc), "t0": float(t0), "t1": float(t1), "log": log,
                       "cmd": rest[7:], "env_k3d_gpu": env_flags(os.environ)})
        return 0
    if cmd == "interlock" and len(rest) == 3:
        return run_interlock(rest[0], rest[1], rest[2])
    if cmd == "placement" and len(rest) in (2, 3, 4):
        log = read_text(rest[0])
        rc = int(rest[1])
        deploy = read_json(rest[2]) if len(rest) >= 3 and rest[2] != "-" else None
        blocked = len(rest) == 4 and rest[3] == "blocked"
        print(json.dumps(placement(log, deploy, rc, blocked), sort_keys=True))
        return 0
    if cmd == "decode" and len(rest) == 2:
        rate, n = decode_rate(read_jsonl(rest[0]), rest[1])
        print(json.dumps({"decode_tok_s": rate, "turns": n}))
        return 0
    if cmd == "success" and len(rest) == 2:
        k, m = success(read_jsonl(rest[0]), rest[1])
        print(json.dumps({"passed": k, "attempts": m}))
        return 0
    if cmd == "squeeze-bytes" and len(rest) in (1, 2):
        text = read_text(rest[1] if len(rest) == 2 else "/proc/meminfo")
        try:
            print(squeeze_bytes(read_json(rest[0]), text))
        except ValueError as e:
            print("squeeze-bytes: %s" % e, file=sys.stderr)
            return 1
        return 0
    if cmd == "squeeze" and len(rest) == 3:
        pre = {c["name"] for c in read_json(rest[2], []) or []}
        bench = os.environ.get("H58_BENCH") or os.path.expanduser("~/k3dgpu-bench")
        state = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "k3d-gpu")
        st = run_squeeze(rest[0], rest[1], os.path.join(bench, "bin", "stress-ng"), state, pre)
        print(json.dumps(st, sort_keys=True))
        return 0 if st.get("ran") or st.get("why") else 1
    if cmd == "redact-events" and len(rest) == 1:
        text = read_text(rest[0])
        with open(rest[0], "w") as f:
            f.write(redact_events(text))
        return 0
    if cmd == "score" and len(rest) == 1:
        res = score(rest[0])
        print(json.dumps({k: res[k] for k in ("verdict", "valid", "invalid_why", "clauses")}, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

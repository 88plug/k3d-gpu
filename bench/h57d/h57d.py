#!/usr/bin/env python3
"""h57d.py: the H57d runner's arm table and renders, P-core load sampler, load gate and
scorers (EXPERIMENTS.md "H57d pre-registration", "H57d amendment R1", "H57dp pre-registration",
"H57dp amendment R1" and "H57dp amendment R2").

  h57d.py arms                          the arm table as JSON
  h57d.py field ARM KEY                 one field of an arm (for arm.sh)
  h57d.py order ROW HOLD                a results row's registered arm order for one hold (for
                                        hold.sh): H57dp's two holds, else H57d's (H57w too)
  h57d.py render ARM OUT.yaml           Namespace + Deployment + Service for Ld, Ld-mtp, Ld-mtp-u
  h57d.py od-ro IN.yaml OUT.yaml        Od's staged render on a read-only model bind
  h57d.py sample OUT.jsonl NODE         every 5 s until killed: P-core (CPUs 0-15) busy CPU-s/s,
                                        the k3d node container's CPU-s/s, load1, package C
  h57d.py score RESULTS_DIR             the H57da and H57dm verdicts from the recorded holds
  h57d.py score-p RESULTS_DIR           the H57dp and H57de verdicts from the two H57dp holds, with
                                        the quality records they inherit from H57d hold 4

The GSM8K grader, the bootstrap and the 1,000-token TTFT prompt are H54's (bench/h54).
Python 3 standard library only, except render (PyYAML)."""

import glob
import subprocess
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "h54"))
import h54  # noqa: E402  (boot_ratio, median, the GSM8K grader)

NS = "h57d"
MODELS = "/var/apb-models"          # the node path k3d's volume maps the campaign's models to
GGUF = "google_gemma-4-26B-A4B-it-qat-q4_0-gguf/gemma-4-26B_q4_0-it.gguf"
DRAFTER_GGUF = "k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf/gemma-4-26B-A4B-it-qat-assistant-q4_0.gguf"
OVB = "OpenVINO/gemma-4-26b-a4b-it-int4-ov"
LLAMACPP = h54.IMG["llamacpp"]       # server-b11749, H54's pin
PCORES = list(range(16))             # CPUs 0-15: the 8 P-cores and their HT siblings
MASK = "0x5555"                      # CPUs 0, 2, ..., 14: one thread per physical P-core
MASK_CPUS = [i for i in range(32) if int(MASK, 16) >> i & 1]

# rank: 0 OpenVINO / Google, 2 built here. svc/ns/port/api: where the OpenAI API answers.
ARMS = {
    "Od": dict(rank=0, deploy="k3d-gpu", model=OVB, svc="ovms", ns="k3d-gpu-agent", port=8000, api="/v3"),
    "Ld": dict(rank=0, deploy="render", svc="ld", ns=NS, port=8080, api="/v1"),
    "Ld-mtp": dict(rank=2, deploy="render", svc="ld-mtp", ns=NS, port=8080, api="/v1"),
    # H57dp: Ld-mtp without -C and --cpu-strict, its 8 threads left to the kernel inside the
    # pod's P-core cpuset.
    "Ld-mtp-u": dict(rank=2, deploy="render", svc="ld-mtp-u", ns=NS, port=8080, api="/v1"),
}
SERVED = "gemma"
H57D_ARMS = ("Od", "Ld", "Ld-mtp")   # the arms H57da and H57dm score (and H57w's replication)
# Rotated arm orders of performance holds 1-3, and the quality hold's order.
ORDERS = {1: ["Od", "Ld", "Ld-mtp"], 2: ["Ld", "Ld-mtp", "Od"], 3: ["Ld-mtp", "Od", "Ld"], 4: ["Ld", "Od", "Ld-mtp"]}
# H57dp and H57de: two performance holds, no quality hold (EXPERIMENTS.md "H57dp pre-registration").
P_ARMS = ("Ld-mtp", "Ld-mtp-u", "Od")
P_ORDERS = {1: ["Ld-mtp-u", "Od", "Ld-mtp"], 2: ["Ld-mtp", "Ld-mtp-u", "Od"]}
P_QUALITY = ("inherited, not re-measured (H57dp pre-registration): H57d's Ld-mtp record (50/50) "
             "stands for Ld-mtp-u and Od's (50/50) for Od; neither row has a quality gate here")
# Where each H57dp arm's inherited quality record is: H57d's committed quality hold (H57dp
# amendment R2). Ld-mtp-u runs Ld-mtp's binary, model files and greedy settings.
H57D_RESULTS = os.path.join(HERE, "results", "2026-10-21")
P_QUALITY_FROM = {"Ld-mtp-u": "Ld-mtp", "Ld-mtp": "Ld-mtp", "Od": "Od"}


def orders(row):
    """A results row's registered arm orders: H57dp's two holds, else H57d's (H57w's too)."""
    return P_ORDERS if row == "H57dp" else ORDERS


def llamacpp_args(mtp, pinned=True):
    a = ["-m", f"/models/{GGUF}", "--host", "0.0.0.0", "--port", "8080", "--alias", SERVED, "-t", "8"]
    if pinned:
        a += ["-C", MASK, "--cpu-strict", "1"]
    a += ["--jinja"]
    if mtp:
        a += ["-md", f"/models/{DRAFTER_GGUF}", "--spec-type", "draft-mtp"]
    return a


def render(arm):
    if arm not in ("Ld", "Ld-mtp", "Ld-mtp-u"):
        raise SystemExit(f"h57d.py render: {arm} is not rendered here")
    name = ARMS[arm]["svc"]
    c = {"name": "server", "image": LLAMACPP, "imagePullPolicy": "IfNotPresent",
         "args": llamacpp_args(arm != "Ld", pinned=arm != "Ld-mtp-u"),
         "ports": [{"name": "http", "containerPort": 8080}],
         # Od's request and balloon (k3d-gpu agent_cpus on a hybrid node: every P-core CPU).
         "resources": {"requests": {"cpu": "16", "memory": "6Gi"}},
         "volumeMounts": [{"name": "models", "mountPath": "/models", "readOnly": True}]}
    labels = {"app": name, "k3d-gpu.io/cores": "performance"}
    dep = {"apiVersion": "apps/v1", "kind": "Deployment",
           "metadata": {"name": name, "namespace": NS, "labels": {"app": name}},
           "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                    "template": {"metadata": {"labels": labels},
                                 "spec": {"containers": [c], "terminationGracePeriodSeconds": 30,
                                          "volumes": [{"name": "models",
                                                       "hostPath": {"path": MODELS, "type": "Directory"}}]}}}}
    svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": NS},
           "spec": {"selector": {"app": name}, "ports": [{"name": "http", "port": 8080, "targetPort": 8080}]}}
    return [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}, dep, svc]


def od_readonly(docs):
    """Od on a read-only model bind: `stage.py ovms`'s render with the `--pull` init container
    removed (its only write, graph.pbtxt, was made beforehand by the same image and arguments,
    `convert.sh`), the models mount read-only, and OVMS's compiled-model cache in an emptyDir.
    The serving container's image, arguments otherwise, requests and labels are untouched."""
    out = []
    for d in docs:
        d = json.loads(json.dumps(d))
        if d.get("kind") == "Deployment":
            ps = d["spec"]["template"]["spec"]
            ps.pop("initContainers", None)
            c = ps["containers"][0]
            c["args"] = ["--cache_dir=/ov-cache" if x.startswith("--cache_dir=") else x for x in c["args"]]
            for m in c["volumeMounts"]:
                if m["name"] == "models":
                    m["readOnly"] = True
            c["volumeMounts"].append({"name": "ov-cache", "mountPath": "/ov-cache"})
            ps["volumes"].append({"name": "ov-cache", "emptyDir": {}})
        out.append(d)
    return out


# ---- load sampler ------------------------------------------------------------------------------

def cpu_ticks(stat_text, cpus):
    """(busy, total) jiffies summed over the listed CPUs from /proc/stat text."""
    busy = total = 0
    want = {f"cpu{c}" for c in cpus}
    for line in stat_text.splitlines():
        f = line.split()
        if f and f[0] in want:
            v = [int(x) for x in f[1:9]]   # user nice system idle iowait irq softirq steal
            total += sum(v)
            busy += sum(v) - v[3] - v[4]
    return busy, total


def node_cgroup(node):
    cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", node], capture_output=True, text=True).stdout.strip()
    return f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"


def node_usage_usec(cg):
    try:
        with open(cg) as f:
            for line in f:
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def pkg_temp():
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            if open(f"{z}/type").read().strip() == "x86_pkg_temp":
                return int(open(f"{z}/temp").read()) / 1000
        except OSError:
            continue
    return None


def sample(out, node, period=5.0):
    hz = os.sysconf("SC_CLK_TCK")
    cg = node_cgroup(node)
    b0, _ = cpu_ticks(open("/proc/stat").read(), PCORES)
    n0, t0 = node_usage_usec(cg), time.time()
    while True:
        time.sleep(period)
        b1, _ = cpu_ticks(open("/proc/stat").read(), PCORES)
        n1, t1 = node_usage_usec(cg), time.time()
        dt = t1 - t0
        pc = (b1 - b0) / hz / dt
        nd = (n1 - n0) / 1e6 / dt if n0 is not None and n1 is not None else None
        rec = {"ts": round(t1, 3), "seconds": round(dt, 3), "pcore_busy": round(pc, 3),
               "node_cpu": None if nd is None else round(nd, 3),
               "load1": float(open("/proc/loadavg").read().split()[0]), "pkg_c": pkg_temp()}
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        b0, n0, t0 = b1, n1, t1


def foreign(samples, t0, t1):
    """Mean foreign P-core load (P-core busy minus the node's own CPU, at least 0) over the
    samples overlapping [t0, t1]; None when no sample overlaps."""
    win = [s for s in samples if s["ts"] > t0 and s["ts"] - s.get("seconds", 5) < t1 and s.get("node_cpu") is not None]
    if not win:
        return None
    return statistics.mean(max(0.0, s["pcore_busy"] - s["node_cpu"]) for s in win)


def load_gate(means):
    """means: {arm: mean foreign P-core CPU-s/s}. Matched when the spread is within
    max(2.0, 25% of the mean of the means)."""
    v = [m for m in means.values() if m is not None]
    if len(v) < 2:
        return {"matched": False, "reason": "fewer than 2 arms with load samples"}
    spread, tol = max(v) - min(v), max(2.0, 0.25 * statistics.mean(v))
    return {"matched": spread <= tol + 1e-9, "spread": spread, "tolerance": tol,
            "reason": "matched" if spread <= tol + 1e-9 else f"foreign P-core load spread {spread:.2f} > {tol:.2f} CPU-s/s"}


# ---- scoring -----------------------------------------------------------------------------------

def jload(p):
    with open(p) as f:
        return json.load(f)


def n1_samples(arm_dir):
    p = os.path.join(arm_dir, "load-n1.json")
    if not os.path.exists(p):
        return []
    return [x["decode_tokens_per_s"] for x in jload(p)["phases"][0]["samples"]
            if x.get("ok") and x.get("decode_tokens_per_s")]


def hold_dirs(results):
    """{1: dir, 2: dir, 3: dir, 4: dir}, with the spare hold (hold5, file `repeats` = k) in place
    of hold k."""
    d = {k: os.path.join(results, f"hold{k}") for k in (1, 2, 3, 4)}
    spare = os.path.join(results, "hold5")
    if os.path.exists(os.path.join(spare, "repeats")):
        d[int(open(os.path.join(spare, "repeats")).read().strip())] = spare
    return d


def measured(arm_dir):
    st = os.path.join(arm_dir, "status.json")
    return os.path.exists(st) and jload(st).get("status") == "measured"


def collect(results, names=H57D_ARMS, holds=(1, 2, 3), hd=None, quality_hold=4):
    """Per arm: pooled and per-hold N=1 decode, foreign P-core load over the N=1 phases, and the
    quality record of quality_hold (None: no quality hold, H57dp)."""
    hd = hd or hold_dirs(results)
    arms = {}
    for a in names:
        rec = {"decode": [], "per_hold": {}, "foreign": [], "quality": None, "holds_measured": 0}
        for k in holds:
            ad = os.path.join(hd[k], a)
            if not measured(ad):
                continue
            s = n1_samples(ad)
            if not s:
                continue
            rec["holds_measured"] += 1
            rec["decode"] += s
            rec["per_hold"][k] = h54.median(s)
            ph = os.path.join(ad, "phases.jsonl")
            sp = os.path.join(hd[k], "load.jsonl")
            if os.path.exists(ph) and os.path.exists(sp):
                samples = [json.loads(x) for x in open(sp) if x.strip()]
                for r in (json.loads(x) for x in open(ph) if x.strip()):
                    if r["phase"] == "n1":
                        f = foreign(samples, r["t0"], r["t1"])
                        if f is not None:
                            rec["foreign"].append(f)
        q = None if quality_hold is None else os.path.join(hd[quality_hold], a, "quality.json")
        if q and measured(os.path.dirname(q)) and os.path.exists(q):
            qq = jload(q)
            rec["quality"] = {"correct": qq["correct"], "n": qq["n"], "score": qq["score"]}
        rec["median"] = h54.median(rec["decode"])
        rec["foreign_mean"] = statistics.mean(rec["foreign"]) if rec["foreign"] else None
        arms[a] = rec
    return arms


def gate(arms):
    """{arm: passes}: score >= the best arm's score - 0.10; an arm with no quality record fails."""
    scores = [a["quality"]["score"] for a in arms.values() if a["quality"]]
    best = max(scores) if scores else None
    return {k: bool(a["quality"]) and a["quality"]["score"] >= best - 0.10 - 1e-9 for k, a in arms.items()}, best


def delivered(a, holds=3):
    return a["holds_measured"] == holds


def ratio(num, den, holds=(1, 2, 3)):
    lo, hi = h54.boot_ratio(num["decode"], den["decode"])
    per = {k: num["per_hold"][k] / den["per_hold"][k] for k in holds}
    return {"ratio": num["median"] / den["median"], "ci": [lo, hi], "per_hold": per,
            "all_holds_faster": all(v > 1 for v in per.values())}


def score(results):
    arms = collect(results)
    g, best = gate(arms)
    lg = load_gate({k: a["foreign_mean"] for k, a in arms.items() if delivered(a)})
    rep = {"arms": {k: {x: v for x, v in a.items() if x not in ("decode", "foreign")} for k, a in arms.items()},
           "gate": g, "best_score": best, "load_gate": lg}

    # H57da: Od vs the faster gate-passing delivered llama.cpp arm.
    od = arms["Od"]
    others = [k for k in ("Ld", "Ld-mtp") if delivered(arms[k]) and g[k]]
    if not delivered(od):
        da = {"verdict": "INCONCLUSIVE", "reason": "Od not delivered in 3 holds"}
    elif od["quality"] is None:
        da = {"verdict": "INCONCLUSIVE", "reason": "Od has no quality record"}
    elif not g["Od"]:
        da = {"verdict": "REFUTED", "reason": "Od fails the quality gate"}
    elif not others:
        da = {"verdict": "INCONCLUSIVE", "reason": "no other gate-passing arm delivered in 3 holds"}
    elif not lg["matched"]:
        da = {"verdict": "INCONCLUSIVE", "reason": "load unmatched: " + lg["reason"]}
    else:
        x = max(others, key=lambda k: arms[k]["median"])
        r = ratio(arms[x], od)
        da = {"x": x, **r}
        if r["ci"][0] >= 1.05 and r["all_holds_faster"]:
            da.update(verdict="REFUTED", reason=f"{x}/Od CI lower bound >= 1.05 and {x} faster in each hold")
        elif r["ci"][1] < 1.05:
            da.update(verdict="SUPPORTED", reason=f"{x}/Od CI upper bound < 1.05")
        else:
            da.update(verdict="INCONCLUSIVE", reason="CI not resolved against 1.05")
    rep["H57da"] = da

    # H57dm: Ld-mtp vs Ld.
    m, l = arms["Ld-mtp"], arms["Ld"]
    if not (delivered(m) and delivered(l)):
        dm = {"verdict": "INCONCLUSIVE", "reason": "Ld or Ld-mtp not delivered in 3 holds"}
    elif m["quality"] is None:
        dm = {"verdict": "INCONCLUSIVE", "reason": "Ld-mtp has no quality record"}
    elif not g["Ld-mtp"]:
        dm = {"verdict": "REFUTED", "reason": "Ld-mtp fails the quality gate"}
    elif not lg["matched"]:
        dm = {"verdict": "INCONCLUSIVE", "reason": "load unmatched: " + lg["reason"]}
    else:
        r = ratio(m, l)
        dm = dict(r)
        if r["ratio"] >= 1.20 and r["ci"][0] > 1.0 and r["all_holds_faster"]:
            dm.update(verdict="SUPPORTED", reason="ratio >= 1.20, CI lower bound > 1.0, faster in each hold")
        elif r["ci"][1] < 1.20:
            dm.update(verdict="REFUTED", reason="CI upper bound < 1.20")
        else:
            dm.update(verdict="INCONCLUSIVE", reason="not resolved against 1.20")
    rep["H57dm"] = dm
    return rep


def single_cpu_threads(cpus_txt):
    """{cpu: threads} for the server threads whose affinity is a single CPU, from the thread table
    arm.sh writes to cpus.txt after readiness; None when the file or its table is missing."""
    try:
        with open(cpus_txt) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    head = "server threads by affinity:"
    if head not in lines:
        return None
    out = {}
    for line in lines[lines.index(head) + 1:]:
        f = line.split()
        if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
            out[int(f[1])] = out.get(int(f[1]), 0) + int(f[0])
    return out


def inherited_quality(res=H57D_RESULTS):
    """{arm: the quality record H57dp inherits}, read from res/hold4/<P_QUALITY_FROM[arm]>/
    quality.json (None when that arm was not measured there)."""
    out = {}
    for arm, src in P_QUALITY_FROM.items():
        ad = os.path.join(res, "hold4", src)
        q = os.path.join(ad, "quality.json")
        if not (measured(ad) and os.path.exists(q)):
            out[arm] = None
            continue
        qq = jload(q)
        out[arm] = {"from": f"H57d hold 4, {src}", "correct": qq["correct"], "n": qq["n"], "score": qq["score"]}
    return out


def score_p(results, quality_from=H57D_RESULTS):
    """H57dp (Ld-mtp-u vs Ld-mtp) and H57de (Ld-mtp-u vs Od) from results/hold1 and hold2. As
    H57d: an arm is delivered when it was measured in every hold, and one load gate covers the
    delivered arms. Quality is inherited (P_QUALITY; the records, from quality_from, are
    reported), so neither row is gated on it. Affinity check, per hold, from cpus.txt: Ld-mtp's
    single-CPU threads sit exactly on MASK's CPUs and Ld-mtp-u has none; a row whose arm fails
    it, or has no table, is INCONCLUSIVE."""
    holds = (1, 2)
    hd = {k: os.path.join(results, f"hold{k}") for k in holds}
    arms = collect(results, P_ARMS, holds, hd, quality_hold=None)
    dl = {k: delivered(a, len(holds)) for k, a in arms.items()}
    lg = load_gate({k: a["foreign_mean"] for k, a in arms.items() if dl[k]})
    aff = {}
    for a, want in (("Ld-mtp", set(MASK_CPUS)), ("Ld-mtp-u", set())):
        got = {k: single_cpu_threads(os.path.join(hd[k], a, "cpus.txt")) for k in holds}
        aff[a] = {"single_cpu_threads": got, "as_registered": all(g is not None and set(g) == want for g in got.values())}
    rep = {"arms": {k: {x: v for x, v in a.items() if x not in ("decode", "foreign", "quality")} for k, a in arms.items()},
           "delivered": dl, "quality": P_QUALITY, "quality_records": inherited_quality(quality_from),
           "affinity": aff, "load_gate": lg}
    u = arms["Ld-mtp-u"]

    def precheck(den):
        """The INCONCLUSIVE reason that stops a row before its statistic, or None."""
        if not (dl["Ld-mtp-u"] and dl[den]):
            return f"Ld-mtp-u or {den} not delivered in both holds"
        off = [a for a in ("Ld-mtp-u", den) if a in aff and not aff[a]["as_registered"]]
        if off:
            return "thread affinity not as registered: " + ", ".join(off)
        if not lg["matched"]:
            return "load unmatched: " + lg["reason"]
        return None

    # H57dp: Ld-mtp-u vs Ld-mtp, bar 1.10.
    why = precheck("Ld-mtp")
    if why:
        dp = {"verdict": "INCONCLUSIVE", "reason": why}
    else:
        dp = ratio(u, arms["Ld-mtp"], holds)
        if dp["ratio"] >= 1.10 and dp["ci"][0] > 1.0 and dp["all_holds_faster"]:
            dp.update(verdict="SUPPORTED", reason="ratio >= 1.10, CI lower bound > 1.0, faster in both holds")
        elif dp["ci"][1] < 1.10:
            dp.update(verdict="REFUTED", reason="CI upper bound < 1.10")
        else:
            dp.update(verdict="INCONCLUSIVE", reason="not resolved against 1.10")
    rep["H57dp"] = dp

    # H57de: H57da's rule with X = Ld-mtp-u, bar 1.05.
    why = precheck("Od")
    if why:
        de = {"x": "Ld-mtp-u", "verdict": "INCONCLUSIVE", "reason": why}
    else:
        de = {"x": "Ld-mtp-u", **ratio(u, arms["Od"], holds)}
        if de["ci"][0] >= 1.05 and de["all_holds_faster"]:
            de.update(verdict="REFUTED", reason="Ld-mtp-u/Od CI lower bound >= 1.05 and faster in both holds")
        elif de["ci"][1] < 1.05:
            de.update(verdict="SUPPORTED", reason="Ld-mtp-u/Od CI upper bound < 1.05")
        else:
            de.update(verdict="INCONCLUSIVE", reason="CI not resolved against 1.05")
    rep["H57de"] = de
    return rep


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    if cmd == "arms":
        print(json.dumps({"arms": ARMS, "orders": ORDERS, "orders_h57dp": P_ORDERS, "served": SERVED,
                          "llamacpp": LLAMACPP,
                          "args": {"Ld": llamacpp_args(False), "Ld-mtp": llamacpp_args(True),
                                   "Ld-mtp-u": llamacpp_args(True, pinned=False)}}, indent=1))
    elif cmd == "field":
        v = ARMS[a[0]].get(a[1], "")
        print(v)
    elif cmd == "order":
        o = {str(k): v for k, v in orders(a[0]).items()}
        if a[1] not in o:   # the hold's own number only: "01" would record into hold01/
            raise SystemExit(f"h57d.py order: {a[0]} registers no hold {a[1]}")
        print(" ".join(o[a[1]]))
    elif cmd == "render":
        import yaml
        with open(a[1], "w") as f:
            yaml.safe_dump_all(render(a[0]), f, sort_keys=False)
    elif cmd == "od-ro":
        import yaml
        with open(a[0]) as f:
            docs = [d for d in yaml.safe_load_all(f) if d]
        with open(a[1], "w") as f:
            yaml.safe_dump_all(od_readonly(docs), f, sort_keys=False)
    elif cmd == "sample":
        sample(a[0], a[1])
    elif cmd == "score":
        print(json.dumps(score(a[0]), indent=1))
    elif cmd == "score-p":
        print(json.dumps(score_p(a[0]), indent=1))
    else:
        raise SystemExit(f"h57d.py: unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

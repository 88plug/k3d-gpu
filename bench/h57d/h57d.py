#!/usr/bin/env python3
"""h57d.py: the H57d runner's arm table and renders, P-core load sampler, load gate and
scorer (EXPERIMENTS.md "H57d pre-registration" and "H57d amendment R1").

  h57d.py arms                          the arm table as JSON
  h57d.py field ARM KEY                 one field of an arm (for arm.sh)
  h57d.py render ARM OUT.yaml           Namespace + Deployment + Service for Ld, Ld-mtp
  h57d.py od-ro IN.yaml OUT.yaml        Od's staged render on a read-only model bind
  h57d.py sample OUT.jsonl NODE         every 5 s until killed: P-core (CPUs 0-15) busy CPU-s/s,
                                        the k3d node container's CPU-s/s, load1, package C
  h57d.py score RESULTS_DIR             the H57da and H57dm verdicts from the recorded holds

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

# rank: 0 OpenVINO / Google, 2 built here. svc/ns/port/api: where the OpenAI API answers.
ARMS = {
    "Od": dict(rank=0, deploy="k3d-gpu", model=OVB, svc="ovms", ns="k3d-gpu-agent", port=8000, api="/v3"),
    "Ld": dict(rank=0, deploy="render", svc="ld", ns=NS, port=8080, api="/v1"),
    "Ld-mtp": dict(rank=2, deploy="render", svc="ld-mtp", ns=NS, port=8080, api="/v1"),
}
SERVED = "gemma"
# Rotated arm orders of performance holds 1-3, and the quality hold's order.
ORDERS = {1: ["Od", "Ld", "Ld-mtp"], 2: ["Ld", "Ld-mtp", "Od"], 3: ["Ld-mtp", "Od", "Ld"], 4: ["Ld", "Od", "Ld-mtp"]}


def llamacpp_args(mtp):
    a = ["-m", f"/models/{GGUF}", "--host", "0.0.0.0", "--port", "8080", "--alias", SERVED,
         "-t", "8", "-C", MASK, "--cpu-strict", "1", "--jinja"]
    if mtp:
        a += ["-md", f"/models/{DRAFTER_GGUF}", "--spec-type", "draft-mtp"]
    return a


def render(arm):
    if arm not in ("Ld", "Ld-mtp"):
        raise SystemExit(f"h57d.py render: {arm} is not rendered here")
    name = ARMS[arm]["svc"]
    c = {"name": "server", "image": LLAMACPP, "imagePullPolicy": "IfNotPresent",
         "args": llamacpp_args(arm == "Ld-mtp"),
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


def collect(results):
    hd = hold_dirs(results)
    arms = {}
    for a in ARMS:
        rec = {"decode": [], "per_hold": {}, "foreign": [], "quality": None, "holds_measured": 0}
        for k in (1, 2, 3):
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
        q = os.path.join(hd[4], a, "quality.json")
        if measured(os.path.join(hd[4], a)) and os.path.exists(q):
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


def delivered(a):
    return a["holds_measured"] == 3


def ratio(num, den):
    lo, hi = h54.boot_ratio(num["decode"], den["decode"])
    per = {k: num["per_hold"][k] / den["per_hold"][k] for k in (1, 2, 3)}
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


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    if cmd == "arms":
        print(json.dumps({"arms": ARMS, "orders": ORDERS, "served": SERVED, "llamacpp": LLAMACPP,
                          "args": {"Ld": llamacpp_args(False), "Ld-mtp": llamacpp_args(True)}}, indent=1))
    elif cmd == "field":
        v = ARMS[a[0]].get(a[1], "")
        print(v)
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
    else:
        raise SystemExit(f"h57d.py: unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

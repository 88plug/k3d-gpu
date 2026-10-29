#!/usr/bin/env python3
"""b1g_diag.py: helpers for the UNSCORED B1g stall diagnostic (bench/h54/diag/b1g-stall.sh).

Nothing here is a hold or an arm, and no hypothesis is scored from what it records.

  b1g_diag.py render DEPLOY.yaml OUTDIR
      Reads B1g's committed render (the Deployment, its env ConfigMap and its chat-template
      ConfigMap) and writes what `docker run` needs to start the same container outside k3d:
      OUTDIR/env.list (docker --env-file), OUTDIR/args.json (the container args),
      OUTDIR/templates/<file> (the mounted chat template) and OUTDIR/render.json (image,
      securityContext, volumes, hostIPC, resources, as rendered).
  b1g_diag.py snap PID OUTFILE
      One per-thread table of every process in PID's tree (state, utime+stime ticks, CPU,
      wchan, current syscall, voluntary/involuntary context switches, kernel stack). Run as root
      for wchan/syscall/stack of another user's process.
  b1g_diag.py watch URL CGROUP_CPU_STAT OUTDIR CLIENT_PID ROOT_PID
      Polls URL/metrics every 5 s and the container's cgroup CPU. When a request is running and
      vllm:generation_tokens_total has not moved for STALL_S (30 s), it records the stall, takes
      two thread snapshots 10 s apart and a py-spy dump of every process in the tree, then keeps
      polling until the container exits or 400 s after the stall began. It returns when the
      client process ends with no stall, or after the stall record is complete.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

EXPECTED_IMAGE = ("vllm/vllm-openai-cpu:v0.24.0@sha256:"
                  "b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1")
STALL_S = float(os.environ.get("DIAG_STALL_S", "30"))
STEP_S = float(os.environ.get("DIAG_STEP_S", "5"))
AFTER_S = float(os.environ.get("DIAG_AFTER_S", "400"))
PYSPY = os.environ.get("DIAG_PYSPY", "")


def _docs(path):
    import yaml
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def render(deploy, outdir):
    docs = _docs(deploy)
    dep = next(d for d in docs if d.get("kind") == "Deployment")
    spec = dep["spec"]["template"]["spec"]
    (c,) = [c for c in spec["containers"] if c["name"] == "vllm"]
    if c["image"] != EXPECTED_IMAGE:
        raise SystemExit(f"render: image {c['image']!r} is not B1g's {EXPECTED_IMAGE!r}")
    cms = {d["metadata"]["name"]: d.get("data") or {} for d in docs if d.get("kind") == "ConfigMap"}
    env = {}
    for ef in c.get("envFrom", []):
        env.update(cms[ef["configMapRef"]["name"]])
    for e in c.get("env", []):
        env[e["name"]] = e.get("value", "")
    vols = {v["name"]: v for v in spec.get("volumes", [])}
    tmpl = None
    for m in c.get("volumeMounts", []):
        v = vols[m["name"]]
        if "configMap" in v and m["mountPath"] == "/workspace/vllm/examples":
            tmpl = cms[v["configMap"]["name"]]
    if not tmpl:
        raise SystemExit("render: no chat-template ConfigMap mounted at /workspace/vllm/examples")
    os.makedirs(os.path.join(outdir, "templates"), exist_ok=True)
    for name, body in tmpl.items():
        with open(os.path.join(outdir, "templates", name), "w") as f:
            f.write(body)
    with open(os.path.join(outdir, "env.list"), "w") as f:
        for k, v in env.items():
            if "\n" in str(v):
                raise SystemExit(f"render: env {k} has a newline; --env-file cannot carry it")
            f.write(f"{k}={v}\n")
    with open(os.path.join(outdir, "args.json"), "w") as f:
        json.dump([str(a) for a in c["args"]], f, indent=1)
    with open(os.path.join(outdir, "render.json"), "w") as f:
        json.dump({"image": c["image"], "securityContext": c.get("securityContext"),
                   "podSecurityContext": spec.get("securityContext"), "hostIPC": spec.get("hostIPC"),
                   "resources": c.get("resources"), "volumeMounts": c.get("volumeMounts"),
                   "volumes": spec.get("volumes"), "templates": sorted(tmpl)}, f, indent=1)
    return env, c["args"]


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        return f"<{e.__class__.__name__}>"


def tree(root):
    """PIDs of root and all its descendants, from /proc/*/stat ppid."""
    kids = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        s = _read(f"/proc/{p}/stat")
        m = re.match(r"\d+ \(.*\) \S (\d+)", s)
        if m:
            kids.setdefault(int(m.group(1)), []).append(int(p))
    out, todo = [], [int(root)]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(kids.get(p, []))
    return sorted(out)


def thread_rows(pid):
    rows = []
    base = f"/proc/{pid}/task"
    try:
        tids = sorted(int(t) for t in os.listdir(base))
    except OSError:
        return rows
    for tid in tids:
        t = f"{base}/{tid}"
        stat = _read(f"{t}/stat")
        m = re.match(r"\d+ \((.*)\) (\S) (.*)", stat)
        if not m:
            continue
        f = m.group(3).split()   # fields from ppid (field 4) on
        status = _read(f"{t}/status")
        vcs = re.search(r"^voluntary_ctxt_switches:\s+(\d+)", status, re.M)
        ncs = re.search(r"^nonvoluntary_ctxt_switches:\s+(\d+)", status, re.M)
        rows.append({
            "tid": tid, "comm": m.group(1), "state": m.group(2),
            "ticks": int(f[10]) + int(f[11]),   # utime + stime (fields 14, 15)
            "cpu": int(f[35]),                  # processor (field 39)
            "wchan": _read(f"{t}/wchan").strip(),
            "syscall": _read(f"{t}/syscall").strip(),
            "vcs": int(vcs.group(1)) if vcs else None, "ncs": int(ncs.group(1)) if ncs else None,
            "stack": _read(f"{t}/stack").strip().splitlines()[:12],
        })
    return rows


def snap(root, outfile):
    procs = []
    for pid in tree(root):
        procs.append({"pid": pid, "cmdline": _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip(),
                      "comm": _read(f"/proc/{pid}/comm").strip(), "threads": thread_rows(pid)})
    rec = {"ts": time.time(), "root": int(root), "loadavg": _read("/proc/loadavg").strip(),
           "pressure": {k: _read(f"/proc/pressure/{k}").strip() for k in ("cpu", "memory", "io")},
           "procs": procs}
    with open(outfile, "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def metrics(url):
    with urllib.request.urlopen(url.rstrip("/") + "/metrics", timeout=10) as r:
        text = r.read().decode()
    def val(name):
        tot, seen = 0.0, False
        for line in text.splitlines():
            if line.startswith(name + "{") or line.startswith(name + " "):
                tot += float(line.rsplit(" ", 1)[1])
                seen = True
        return tot if seen else None
    return {"gen": val("vllm:generation_tokens_total"), "running": val("vllm:num_requests_running")}


def cg_usage(path):
    for line in _read(path).splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return None


def alive(pid):
    """pid exists and is not a zombie (an exited child nobody has reaped yet)."""
    m = re.match(r"\d+ \(.*\) (\S)", _read(f"/proc/{int(pid)}/stat"))
    return bool(m) and m.group(1) not in ("Z", "X")


def _sudo_self(*args):
    return subprocess.run(["sudo", "-n", sys.executable, os.path.abspath(__file__), *args],
                          capture_output=True, text=True, timeout=120)


def pyspy(pid, outfile):
    if not PYSPY:
        return "DIAG_PYSPY unset"
    # No --native: on this image py-spy 0.4.2's unwinder stops with UNW_EBADREG (run1, 2026-10-29).
    p = subprocess.run(["sudo", "-n", PYSPY, "dump", "--pid", str(pid)],
                       capture_output=True, text=True, timeout=180)
    with open(outfile, "w") as f:
        f.write(f"# rc={p.returncode}\n{p.stdout}\n# stderr\n{p.stderr}")
    return p.returncode


def running_threads(snapfile):
    """(tid, comm) of every engine thread in state R in a snapshot file; [] when it is missing."""
    try:
        rec = json.load(open(snapfile))
    except (OSError, ValueError):
        return []
    return [(t["tid"], p["comm"]) for p in rec["procs"] if p["comm"].startswith("VLLM::")
            for t in p["threads"] if t["state"] == "R"]


def perf_thread(tid, outdir):
    """5 s of perf samples (call graphs) of one running thread, and its report."""
    data = os.path.join(outdir, f"perf-{tid}.data")
    rec = subprocess.run(["sudo", "-n", "perf", "record", "-t", str(tid), "-g", "-F", "999", "-o", data,
                          "--", "sleep", "5"], capture_output=True, text=True, timeout=60)
    rep = subprocess.run(["sudo", "-n", "perf", "report", "-i", data, "--stdio", "--no-children"],
                         capture_output=True, text=True, timeout=120)
    with open(os.path.join(outdir, f"perf-{tid}-report.txt"), "w") as f:
        f.write(f"# perf record rc={rec.returncode}: {rec.stderr.strip()}\n{rep.stdout}")
    return rec.returncode


def watch(url, cgroup, outdir, client_pid, root_pid):
    with open(os.path.join(outdir, "watch.jsonl"), "a") as log:
        return _watch(log, url, cgroup, outdir, client_pid, root_pid)


def _watch(log, url, cgroup, outdir, client_pid, root_pid):
    last_gen, last_change, last_cg, last_t = None, time.time(), cg_usage(cgroup), time.time()
    stall = None
    while True:
        now = time.time()
        try:
            m = metrics(url)
        except Exception as e:   # the server going away is itself a record
            m = {"gen": None, "running": None, "error": f"{e.__class__.__name__}: {e}"}
        cg = cg_usage(cgroup)
        cpu = None if (cg is None or last_cg is None or now - last_t < 1) else (cg - last_cg) / 1e6 / (now - last_t)
        last_cg, last_t = cg, now
        if m.get("gen") is not None and m["gen"] != last_gen:
            last_gen, last_change = m["gen"], now
        row = {"ts": now, **m, "container_cpu_s_per_s": cpu, "load1": _read("/proc/loadavg").split()[0]}
        log.write(json.dumps(row) + "\n")
        log.flush()
        if stall is None and (m.get("running") or 0) >= 1 and now - last_change >= STALL_S:
            stall = {"onset_ts": last_change, "detected_ts": now, "gen_tokens_at_onset": last_gen}
            for i, gap in (("a", 0), ("b", 10)):
                time.sleep(gap)
                p = _sudo_self("snap", str(root_pid), os.path.join(outdir, f"threads-{i}.json"))
                stall[f"snap_{i}_rc"] = p.returncode
            for tid, _ in running_threads(os.path.join(outdir, "threads-b.json"))[:4]:
                stall[f"perf_{tid}"] = perf_thread(tid, outdir)
            for pid in tree(root_pid):
                stall[f"pyspy_{pid}"] = pyspy(pid, os.path.join(outdir, f"pyspy-{pid}.txt"))
            with open(os.path.join(outdir, "stall.json"), "w") as f:
                json.dump(stall, f, indent=1)
        if stall is not None and (not alive(root_pid) or now - stall["onset_ts"] >= AFTER_S):
            stall["end_ts"], stall["container_alive_at_end"] = now, alive(root_pid)
            with open(os.path.join(outdir, "stall.json"), "w") as f:
                json.dump(stall, f, indent=1)
            return 1
        if stall is None and not alive(client_pid):
            with open(os.path.join(outdir, "nostall.json"), "w") as f:
                json.dump({"ts": now, "gen_tokens": last_gen}, f)
            return 0
        time.sleep(STEP_S)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "render" and len(argv) == 4:
        render(argv[2], argv[3])
        return 0
    if cmd == "snap" and len(argv) == 4:
        snap(argv[2], argv[3])
        return 0
    if cmd == "watch" and len(argv) == 7:
        return watch(*argv[2:7])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

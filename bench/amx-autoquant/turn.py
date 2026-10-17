#!/usr/bin/env python3
"""H27 request runner: probe repeats and reference rounds against OVMS arms.

One request = one streamed /v3/completions call with a nonce-prefixed prompt
(so no prefix-cache reuse), greedy, a fixed max_tokens. Recorded per request:
TTFT, total time, streamed chunks, usage.completion_tokens when the server sends
it, and host confounds (RAPL package energy, package throttle count, 1-min load,
CPU busy on the pinned CPUs, pgmajfault and pswpin deltas).

  turn.py --ports int4=18101,int8=18102,fp16=18103 --prompts prompts.json \
          --phase probe|reference --label P1 --out results.jsonl [--cpus 0-15]

Stdlib only. No sysfs writes; RAPL is read through `sudo -n cat` when not readable.
"""

import argparse
import http.client
import json
import os
import subprocess
import time
import uuid

RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"
THROTTLE = "/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count"


def read(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        try:
            return subprocess.run(["sudo", "-n", "cat", path], capture_output=True, text=True,
                                  timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None


def cpulist(spec):
    out = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def snap(cpus):
    s = {"t": time.monotonic(), "rapl_uj": read(RAPL), "throttle": read(THROTTLE)}
    busy = total = 0
    with open("/proc/stat", encoding="ascii") as f:
        for line in f:
            w = line.split()
            if w[0].startswith("cpu") and w[0][3:].isdigit() and int(w[0][3:]) in cpus:
                v = [int(x) for x in w[1:9]]
                total += sum(v)
                busy += sum(v) - v[3] - v[4]
    s["busy"], s["total"] = busy, total
    with open("/proc/vmstat", encoding="ascii") as f:
        vm = dict(line.split() for line in f)
    s["pgmajfault"], s["pswpin"] = int(vm.get("pgmajfault", 0)), int(vm.get("pswpin", 0))
    with open("/proc/loadavg", encoding="ascii") as f:
        s["load1"] = float(f.read().split()[0])
    return s


def delta(a, b):
    d = {"load1": b["load1"], "pgmajfault": b["pgmajfault"] - a["pgmajfault"],
         "pswpin": b["pswpin"] - a["pswpin"],
         "cpu_busy_frac": (b["busy"] - a["busy"]) / max(1, b["total"] - a["total"])}
    try:
        e = int(b["rapl_uj"]) - int(a["rapl_uj"])
        d["pkg_w"] = e / 1e6 / (b["t"] - a["t"]) if e >= 0 else None  # counter wrap reads None
    except (TypeError, ValueError):
        d["pkg_w"] = None
    try:
        d["throttle_delta"] = int(b["throttle"]) - int(a["throttle"])
    except (TypeError, ValueError):
        d["throttle_delta"] = None
    return d


def request(port, model, prompt, max_tokens, timeout=300):
    body = json.dumps({"model": model, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0,
                       "ignore_eos": True, "stream": True,
                       "stream_options": {"include_usage": True}})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    t0 = time.monotonic()
    c.request("POST", "/v3/completions", body, {"Content-Type": "application/json"})
    r = c.getresponse()
    if r.status != 200:
        return {"error": "HTTP %d: %s" % (r.status, r.read()[:300].decode("utf-8", "replace"))}
    ttft, chunks, usage = None, 0, None
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        ev = json.loads(line[5:])
        if ev.get("usage"):
            usage = ev["usage"]
        if any(ch.get("text") for ch in ev.get("choices") or []):
            chunks += 1
            if ttft is None:
                ttft = time.monotonic() - t0
    total = time.monotonic() - t0
    c.close()
    toks = (usage or {}).get("completion_tokens") or chunks
    dec = (toks - 1) / (total - ttft) if ttft is not None and toks > 1 and total > ttft else None
    return {"ttft_s": ttft, "total_s": total, "chunks": chunks, "usage": usage,
            "decode_tok_s": dec, "decode_from": "usage" if usage else "chunks"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--phase", choices=["probe", "reference", "warmup"], required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--order", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cpus", default="0-15")
    a = ap.parse_args()
    ports = dict(kv.split("=") for kv in a.ports.split(","))
    with open(a.prompts, encoding="utf-8") as f:
        prompts = json.load(f)
    cpus = cpulist(a.cpus)
    # probe: short shape (128 in, 32 out) per arm; reference/warmup: turn shape (396 in, 96 out).
    shape = prompts["probe"] if a.phase == "probe" else prompts["turn"]
    max_tokens = 32 if a.phase == "probe" else 96
    order = a.order.split(",") if a.order else sorted(ports)
    with open(a.out, "a", encoding="utf-8") as out:
        for arm in order:
            prec = arm.rstrip("'")
            nonce = "[%s %s %s] " % (a.label, arm, uuid.uuid4().hex)
            before = snap(cpus)
            res = request(int(ports[prec]), prec, nonce + shape, max_tokens)
            res.update(delta(before, snap(cpus)))
            res.update(phase=a.phase, label=a.label, arm=arm, precision=prec, wall=time.time())
            out.write(json.dumps(res) + "\n")
            out.flush()
            print("%s %s %s ttft=%s dec=%s" % (a.phase, a.label, arm, res.get("ttft_s"), res.get("decode_tok_s")),
                  flush=True)


if __name__ == "__main__":
    main()

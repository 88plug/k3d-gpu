#!/usr/bin/env python3
"""h54.py: the H54a runner's arm renders, quality grader, void check and scorer
(EXPERIMENTS.md "H54 pre-registration" and "H54 amendment R1"; R3 for served/carry).

  h54.py arms                                   the arm table as JSON
  h54.py render ARM OUT.yaml                     Deployment + Service for an arm that is not
                                                 an EAT chart or a k3d-gpu manifest (Lg, Lg-mtp, Sg)
  h54.py derive-vllm ARM EAT_DEPLOY.yaml OUT     Vq, Vq-mtp, Vi from B1g's staged EAT render
  h54.py gsm8k URL MODEL ITEMS.jsonl OUT.json    GSM8K quality spot-check (16 in flight)
  h54.py void SERVING.jsonl T0 T1 [W0 W1]        exit 1 when the phase [T0, T1] is void (R4: CPU
                                                 read on the idle window [W0, W1])
  h54.py active-bytes FILE                       bytes read per decoded token from a GGUF or
                                                 safetensors tensor table
  h54.py score-a RESULTS_DIR                     the H54a verdict from the recorded arms
  h54.py served OUT                              exit 1 when the arm's server stopped answering
                                                 after readiness (amendment R3)
  h54.py carry RESULTS_DIR HOLD ARM...           the arms HOLD carries to the next hold's front
  h54.py recheck RESULTS_DIR                     apply `served` to every arm recorded measured

Python 3 standard library only, except render/derive-vllm (PyYAML)."""

import concurrent.futures
import glob
import json
import os
import random
import re
import statistics
import struct
import sys
import time
import urllib.request

NS = "h54"
MODELS = "/var/apb-models"          # the node path k3d's volume maps ~/k3dgpu-gemma/models to
QAT = "google_gemma-4-26B-A4B-it-qat-q4_0-unquantized"
DRAFTER = "google_gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"
GGUF = "google_gemma-4-26B-A4B-it-qat-q4_0-gguf/gemma-4-26B_q4_0-it.gguf"
DRAFTER_GGUF = "k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf/gemma-4-26B-A4B-it-qat-assistant-q4_0.gguf"
AUTOROUND = "Intel_gemma-4-26B-A4B-it-int4-AutoRound"
OVB = "OpenVINO/gemma-4-26b-a4b-it-int4-ov"
OVQ = "k3dgpu/gemma-4-26b-a4b-it-qat-int4-sym-g32-ov"
SERVED = "google/gemma-4-26B-A4B-it"   # B1g's served name (EAT's model id); every vLLM arm keeps it

IMG = {
    "vllm024": "vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1",
    "vllm032": "vllm/vllm-openai-cpu:v0.32.0@sha256:ffaba3989b9b96d95a576143c872a4110ce9800f5199b6a36eb27bdff1cbbe05",
    # Held at b11749 while rows depend on it: H54q pairs Lg with H54a's b11749 records (amendment
    # Q1), H57dp/H57de reuse H57d's b11749 quality records, and H57wa/wm/wb replicate H57d. A newer
    # llama.cpp (v0.8.0 is b11960) needs a dated amendment for each of those rows first.
    "llamacpp": "ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d7f4449e2ae0d02b40e0431f7c212dcf231c9e3a7777a6afc2cc8f312",
    "sglang": "lmsysorg/sglang:v0.5.22-xeon@sha256:4e5c413ed9a31f38f74e18aa9b645d65a0b0a023d42f63edb4e2b412ea1a230f",
    "ovms": "openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58",
    "ovms_eat": "2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a",
}

# rank: 0 model author / Intel, 1 RedHatAI, 2 88plug or built here, 3 third party; None = stock.
# boot: the readiness limit, s (1,500 for bf16 vLLM loads). deploy: who renders the arm.
# probe: one boot attempt per hold. svc/port/api: where the OpenAI API answers.
ARMS = {
    "B1g": dict(rank=None, boot=1500, deploy="eat-vllm", svc="vllm-gemma4-26b-a4b-cpu-service", ns="default", port=80, api="/v1"),
    "B3g": dict(rank=None, boot=900, deploy="eat-ovms", svc="ovms-model-server", ns="default", port=8080, api="/v3"),
    "A1o": dict(rank=0, boot=900, deploy="k3d-gpu", model=OVB, svc="ovms", ns="k3d-gpu-agent", port=8000, api="/v3"),
    "A1g": dict(rank=2, boot=900, deploy="k3d-gpu", model=OVQ, svc="ovms", ns="k3d-gpu-agent", port=8000, api="/v3"),
    "Lg": dict(rank=0, boot=900, deploy="render", svc="lg", ns=NS, port=8080, api="/v1"),
    "Lg-mtp": dict(rank=2, boot=900, deploy="render", svc="lg-mtp", ns=NS, port=8080, api="/v1"),
    "Vq": dict(rank=0, boot=1500, deploy="derive-vllm", svc="vq", ns=NS, port=2080, api="/v1"),
    "Vq-mtp": dict(rank=0, boot=1500, deploy="derive-vllm", svc="vq-mtp", ns=NS, port=2080, api="/v1"),
    "Vi": dict(rank=0, boot=900, deploy="derive-vllm", svc="vi", ns=NS, port=2080, api="/v1", probe=True),
    "Sg": dict(rank=0, boot=900, deploy="render", svc="sg", ns=NS, port=30000, api="/v1", probe=True),
}
SERVED_NAME = {"B1g": SERVED, "B3g": "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "A1o": "gemma", "A1g": "gemma",
               "Lg": "gemma", "Lg-mtp": "gemma", "Vq": SERVED, "Vq-mtp": SERVED, "Vi": SERVED, "Sg": "gemma"}
THREADS = "0-47"   # one inference thread per physical core (STREAM peaked at 48, not 96)


def llamacpp_args(mtp):
    a = ["-m", f"/models/{GGUF}", "--host", "0.0.0.0", "--port", "8080", "--alias", "gemma",
         "-t", "48", "-Cr", THREADS, "--cpu-strict", "1", "--jinja"]
    if mtp:
        # b11749 names the type draft-mtp (common/speculative.cpp); the draft length stays at
        # its default (--spec-draft-n-max, 3).
        a += ["-md", f"/models/{DRAFTER_GGUF}", "--spec-type", "draft-mtp"]
    return a


def sglang_args():
    return ["python3", "-m", "sglang.launch_server", "--model-path", f"/models/{QAT}", "--device", "cpu",
            "--attention-backend", "intel_amx", "--tp", "1", "--host", "0.0.0.0", "--port", "30000",
            "--served-model-name", "gemma", "--max-total-tokens", "65536"]


def deployment(name, image, port, command=None, args=None, env=None):
    c = {"name": "server", "image": image, "imagePullPolicy": "IfNotPresent",
         "ports": [{"name": "http", "containerPort": port}],
         "volumeMounts": [{"name": "models", "mountPath": "/models", "readOnly": True},
                          {"name": "shm", "mountPath": "/dev/shm"}]}
    if command:
        c["command"] = command
    if args:
        c["args"] = args
    if env:
        c["env"] = [{"name": k, "value": v} for k, v in env.items()]
    meta = {"name": name, "namespace": NS, "labels": {"app": name}}
    dep = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta,
           "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                    "template": {"metadata": {"labels": {"app": name}},
                                 "spec": {"containers": [c], "terminationGracePeriodSeconds": 30,
                                          "volumes": [{"name": "models", "hostPath": {"path": MODELS, "type": "Directory"}},
                                                      {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "8Gi"}}]}}}}
    svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": NS},
           "spec": {"selector": {"app": name}, "ports": [{"name": "http", "port": port, "targetPort": port}]}}
    ns = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}
    return [ns, dep, svc]


def render(arm):
    if arm in ("Lg", "Lg-mtp"):
        return deployment(ARMS[arm]["svc"], IMG["llamacpp"], 8080, args=llamacpp_args(arm == "Lg-mtp"))
    if arm == "Sg":
        return deployment("sg", IMG["sglang"], 30000, command=sglang_args(),
                          env={"SGLANG_CPU_OMP_THREADS_BIND": THREADS})
    raise SystemExit(f"h54.py render: {arm} is not rendered here")


def derive_vllm(arm, docs):
    """Vq, Vq-mtp, Vi: B1g's staged EAT render (bench/apb/stage.py vllm), with the v0.32.0 image,
    the arm's model, the attention backend named, threads bound to CPUs 0-47, and the namespace,
    names and labels changed so it cannot be mistaken for B1g."""
    name = ARMS[arm]["svc"]
    out = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}]
    cms = {}   # B1g's ConfigMap names -> this arm's (the env and the chat-template ConfigMaps)
    for d in docs:
        if d.get("kind") == "ConfigMap":
            old = d["metadata"]["name"]
            cms[old] = f"{name}-chat-templates" if old.endswith("-chat-templates") else f"{name}-config"
    for d in docs:
        d = json.loads(json.dumps(d))
        kind = d.get("kind")
        old = d["metadata"]["name"]
        d["metadata"] = {"name": cms.get(old, name) if kind == "ConfigMap" else name, "namespace": NS,
                         "labels": {"app": name}}
        if kind == "ConfigMap" and d["metadata"]["name"].endswith("-config"):
            d["data"]["VLLM_CPU_OMP_THREADS_BIND"] = THREADS
        elif kind == "Service":
            d["spec"]["selector"] = {"app": name}
            d["spec"]["ports"] = [{"name": "http", "port": 2080, "targetPort": 2080, "protocol": "TCP"}]
        elif kind == "Deployment":
            d["spec"]["selector"] = {"matchLabels": {"app": name}}
            t = d["spec"]["template"]
            t["metadata"] = {"labels": {"app": name}}
            for v in t["spec"].get("volumes") or []:
                if "configMap" in v:
                    v["configMap"]["name"] = cms[v["configMap"]["name"]]
            c = t["spec"]["containers"][0]
            c["image"] = IMG["vllm032"]
            for e in c.get("envFrom") or []:
                if "configMapRef" in e:
                    e["configMapRef"]["name"] = cms[e["configMapRef"]["name"]]
            a = c["args"]
            if arm == "Vi":
                a[a.index("--model") + 1] = f"/data/{AUTOROUND}"
            a += ["--attention-backend", "CPU_ATTN"]
            if arm == "Vq-mtp":
                # num_speculative_tokens 1: the n_predict default config/speculative.py sets for a
                # gemma4_assistant drafter, passed because v0.32.0 compares it with 1 before it
                # sets it (TypeError on None; amendment R2).
                a += ["--speculative-config", json.dumps({"method": "gemma4_mtp", "model": f"/data/{DRAFTER}",
                                                          "num_speculative_tokens": 1})]
        out.append(d)
    return out


# ---- quality: GSM8K items, zero-shot, last number vs the #### answer ------------------------

PROMPT = "Solve the problem. End with a line 'Answer: <number>'."
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def gold(answer):
    return answer.split("####")[-1].strip().replace(",", "")


def grade(reply, answer):
    """True when the last number in the reply (commas removed) equals the #### answer."""
    nums = NUM.findall((reply or "").replace(",", ""))
    if not nums:
        return False
    try:
        return float(nums[-1]) == float(gold(answer))
    except ValueError:
        return False


def chat(url, model, question, timeout=1800):
    body = {"model": model, "temperature": 0, "max_tokens": 1024, "stream": False,
            "messages": [{"role": "user", "content": f"{PROMPT}\n\n{question}"}],
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    m = d["choices"][0]["message"]
    return m.get("content") or "", d.get("usage") or {}


def gsm8k(url, model, items, workers=16):
    def one(it):
        t0 = time.time()
        try:
            reply, usage = chat(url, model, it["question"])
            err = None
        except Exception as e:  # noqa: BLE001 - recorded per item, scored as wrong
            reply, usage, err = "", {}, f"{type(e).__name__}: {e}"
        return {"idx": it["idx"], "ok": grade(reply, it["answer"]), "gold": gold(it["answer"]),
                "reply": reply, "usage": usage, "error": err, "s": round(time.time() - t0, 2)}
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, items))
    n = len(rows)
    return {"items": [r["idx"] for r in rows], "n": n, "correct": sum(r["ok"] for r in rows),
            "score": sum(r["ok"] for r in rows) / n if n else None, "errors": sum(1 for r in rows if r["error"]),
            "wall_s": round(time.time() - t0, 1), "prompt": PROMPT, "rows": rows}


# ---- void phases (H50 serving covariate) -----------------------------------------------------

def void(samples, t0, t1, cpu_max=4.0, w0=None, w1=None):
    """(void, reason) for the phase [t0, t1]: an aiperf process in any sample in it, or the
    non-campaign containers' mean CPU-s/s above cpu_max. Amendment R4: with a pre-phase idle
    window [w0, w1] (the arm's 60 s idle, no arm load) the CPU part is read there, not over the
    phase, whose non-campaign CPU the arm's own load raises; the phase's value is still reported.
    No sample in the window read is not void."""
    def within(a, b):
        # serving.py writes one sample per 10 s, covering (ts - seconds, ts].
        return [s for s in samples if s.get("ts", -1) > a and s["ts"] - s.get("seconds", 10) < b]
    win = within(t0, t1)
    if any((s.get("aiperf_procs") or 0) > 0 for s in win):
        return True, "aiperf ran"
    ph = f"; phase {statistics.mean(s.get('serving_cpu') or 0.0 for s in win):.2f}" if win else ""
    ref = within(w0, w1) if w0 is not None else win
    if not ref:
        return False, "no serving sample" + (" in the idle window" if w0 is not None else "")
    cpu = statistics.mean(s.get("serving_cpu") or 0.0 for s in ref)
    where = "idle window " if w0 is not None else ""
    if cpu > cpu_max:
        return True, f"non-campaign CPU {where}{cpu:.2f} CPU-s/s > {cpu_max}" + (ph if w0 is not None else "")
    return False, f"non-campaign CPU {where}{cpu:.2f} CPU-s/s" + (ph if w0 is not None else "")


# ---- active bytes per token ------------------------------------------------------------------

EXP = re.compile(r"ffn_(gate|up|down|gate_up)_exps")
SKIP_ST = re.compile(r"vision|audio|embed_vision|embed_audio")


def _gguf_tensors(path):
    """[(name, nbytes)] from a GGUF v3 header (sizes from the offset table)."""
    with open(path, "rb") as f:
        magic, ver = f.read(4), struct.unpack("<I", f.read(4))[0]
        if magic != b"GGUF" or ver < 2:
            raise ValueError(f"{path}: not a GGUF v2+ file")
        n_t, n_kv = struct.unpack("<QQ", f.read(16))
        align = 32

        def rstr():
            (n,) = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")
        sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

        def rval(t):
            if t == 8:
                return rstr()
            if t == 9:
                et, n = struct.unpack("<IQ", f.read(12))
                return [rval(et) for _ in range(n)] if et in (8, 9) else f.read(sizes[et] * n)
            fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}[t]
            return struct.unpack(fmt, f.read(sizes[t]))[0]
        for _ in range(n_kv):
            k = rstr()
            (t,) = struct.unpack("<I", f.read(4))
            v = rval(t)
            if k == "general.alignment":
                align = v
        infos = []
        for _ in range(n_t):
            name = rstr()
            (nd,) = struct.unpack("<I", f.read(4))
            f.read(8 * nd)
            _typ, off = struct.unpack("<IQ", f.read(12))
            infos.append((name, off))
        start = (f.tell() + align - 1) // align * align
    end = os.path.getsize(path) - start
    infos.sort(key=lambda x: x[1])
    return [(n, (infos[i + 1][1] if i + 1 < len(infos) else end) - o) for i, (n, o) in enumerate(infos)]


def _st_tensors(path):
    out = []
    for p in sorted(glob.glob(os.path.join(path, "*.safetensors")) if os.path.isdir(path) else [path]):
        with open(p, "rb") as f:
            (n,) = struct.unpack("<Q", f.read(8))
            hdr = json.loads(f.read(n))
        out += [(k, v["data_offsets"][1] - v["data_offsets"][0]) for k, v in hdr.items() if k != "__metadata__"]
    return out


def active_bytes(path, routed=8, experts=128):
    """All non-expert tensors plus routed/experts of the routed-expert tensors (vision and audio
    towers left out of a safetensors checkpoint)."""
    if path.endswith(".gguf"):
        t, is_exp = _gguf_tensors(path), (lambda n: bool(EXP.search(n)))
    else:
        t = [(n, b) for n, b in _st_tensors(path) if not SKIP_ST.search(n)]
        is_exp = (lambda n: ".experts." in n)
    e = sum(b for n, b in t if is_exp(n))
    r = sum(b for n, b in t if not is_exp(n))
    return {"expert_bytes": e, "rest_bytes": r, "active_bytes": r + e * routed / experts}


# ---- H54a scoring -----------------------------------------------------------------------------

def median(v):
    return statistics.median(v) if v else None


def boot_ratio(a, b, n=10000, seed=1):
    """95% percentile bootstrap CI of median(a) / median(b), each resampled on its own."""
    rng = random.Random(seed)
    r = []
    for _ in range(n):
        ma = statistics.median(rng.choices(a, k=len(a)))
        mb = statistics.median(rng.choices(b, k=len(b)))
        r.append(ma / mb)
    r.sort()
    return r[int(0.025 * n)], r[int(0.975 * n) - 1]


def choose(arms):
    """arms: {name: {"rank", "decode", "correct", "n"}} of optimized arms passing the gate.
    The fastest arm of the best rank present; a lower-ranked arm replaces it only when its median
    N=1 decode is >= 5% higher at no more than 2 items fewer correct."""
    if not arms:
        return None
    by_rank = {}
    for name, a in arms.items():
        cur = by_rank.get(a["rank"])
        if cur is None or a["decode"] > arms[cur]["decode"]:
            by_rank[a["rank"]] = name
    order = sorted(by_rank)
    chosen = by_rank[order[0]]
    for rk in order[1:]:
        c = by_rank[rk]
        if arms[c]["decode"] >= 1.05 * arms[chosen]["decode"] and arms[c]["correct"] >= arms[chosen]["correct"] - 2:
            chosen = c
    return chosen


def jload(path):
    with open(path) as f:
        return json.load(f)


def load_arm(d):
    """One recorded arm directory: status.json, phases.json (llm_load per phase), quality.json."""
    st = jload(os.path.join(d, "status.json"))
    out = {"status": st.get("status"), "dir": d}
    ph = os.path.join(d, "phases.json")
    if os.path.exists(ph):
        phases = jload(ph)
        n1 = phases.get("n1") or {}
        out["void_missing"] = any(p.get("void") and p.get("rerun_void") for p in phases.values() if isinstance(p, dict))
        if n1 and not (n1.get("void") and n1.get("rerun_void")):
            s = [x["decode_tokens_per_s"] for x in n1["result"]["phases"][0]["samples"]
                 if x.get("ok") and x.get("decode_tokens_per_s")]
            out["decode_samples"] = s
            out["decode"] = median(s)
    q = os.path.join(d, "quality.json")
    if os.path.exists(q):
        qq = jload(q)
        out["correct"], out["n"], out["score"] = qq["correct"], qq["n"], qq["score"]
    return out


# ---- amendment R3: a server that stops answering after readiness --------------------------------

STOPPED = "server stopped answering"


def served(out):
    """(ok, reason) for a measured arm directory: not ok when its N=1 phase completed no request
    or every quality item errored, i.e. the server stopped answering after readiness."""
    try:
        n1 = jload(os.path.join(out, "load-n1.json"))
        done = [s for s in n1["phases"][0]["samples"] if s.get("ok") and s.get("decode_tokens_per_s")]
    except (OSError, ValueError, KeyError, IndexError):
        done = []
    if not done:
        return False, f"{STOPPED}: the N=1 phase completed no request"
    try:
        q = jload(os.path.join(out, "quality.json"))
    except (OSError, ValueError):
        return False, f"{STOPPED}: no quality record"
    if q.get("n") and q.get("errors", 0) >= q["n"]:
        return False, f"{STOPPED}: every quality item errored"
    return True, "phases and quality recorded"


def carry(results, hold, arms):
    """Arms hold HOLD passes to the next hold's front: not reached (or no record), and, once per
    arm, failed because the server stopped answering after readiness (R3) or measured with its
    N=1 phase void twice (R4)."""
    def st(h, a):
        try:
            return jload(os.path.join(results, f"hold{h}", a, "status.json"))
        except (OSError, ValueError):
            return {"status": "missing"}
    out = []
    for a in arms:
        s = st(hold, a)
        if s["status"] in ("not_reached", "missing"):
            out.append(a)
        elif s["status"] == "failed" and str(s.get("reason", "")).startswith(STOPPED):
            if not any(str(st(h, a).get("reason", "")).startswith(STOPPED) for h in range(1, hold)):
                out.append(a)
        elif s["status"] == "measured" and n1_void(os.path.join(results, f"hold{hold}", a)):
            # Amendment R4: the N=1 metric missing because its phase was void twice; once per arm.
            if not any(n1_void(os.path.join(results, f"hold{h}", a)) for h in range(1, hold)):
                out.append(a)
    return out


def n1_void(d):
    """True when the arm directory's N=1 phase was void on both tries (its metric is missing)."""
    try:
        n1 = jload(os.path.join(d, "phases.json")).get("n1") or {}
    except (OSError, ValueError):
        return False
    return bool(n1.get("void") and n1.get("rerun_void"))


def recheck(results):
    """Rewrite as failed every arm recorded measured whose server stopped answering (arm.sh
    before R3 wrote measured whenever quality.json existed). Returns the arm dirs changed."""
    changed = []
    for st in sorted(glob.glob(os.path.join(results, "hold*", "*", "status.json"))):
        s = jload(st)
        if s.get("status") != "measured":
            continue
        ok, why = served(os.path.dirname(st))
        if not ok:
            s.update(status="failed", reason=why, was="measured", rechecked=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            with open(st, "w") as f:
                json.dump(s, f)
            changed.append(os.path.dirname(st))
    return changed


def score_a(results):
    arms = {}
    for st in sorted(glob.glob(os.path.join(results, "hold*", "*", "status.json"))):
        d = os.path.dirname(st)
        a = load_arm(d)
        name = os.path.basename(d)
        if a["status"] == "measured" or name not in arms:
            arms[name] = a
    rep = {"arms": {k: {x: v for x, v in a.items() if x != "decode_samples"} for k, a in arms.items()}}
    b = arms.get("B1g") or {}
    if b.get("status") != "measured" or not b.get("decode") or b.get("score") is None:
        rep.update(verdict="INCONCLUSIVE", reason="B1g not delivered or not measured")
        return rep
    gate = b["score"] - 0.10
    cands, ratios = {}, {}
    for k, a in arms.items():
        if ARMS.get(k, {}).get("rank") is None or a.get("status") != "measured" or not a.get("decode"):
            continue
        ratios[k] = a["decode"] / b["decode"]
        a["gate"] = a.get("score") is not None and a["score"] >= gate - 1e-9
        rep["arms"][k].update(ratio=ratios[k], gate=a["gate"])
        if a["gate"]:
            cands[k] = {"rank": ARMS[k]["rank"], "decode": a["decode"], "correct": a["correct"], "n": a["n"]}
    chosen = choose(cands)
    rep["gate_score"] = gate
    rep["chosen"] = chosen
    high = [k for k, r in ratios.items() if r >= 3.0]
    if chosen is None:
        if high and all(not arms[k]["gate"] for k in high):
            rep.update(verdict="REFUTED", reason="every arm with a point ratio >= 3.0 fails the quality gate")
        else:
            rep.update(verdict="INCONCLUSIVE", reason="no optimized arm passed the gate")
        return rep
    lo, hi = boot_ratio(arms[chosen]["decode_samples"], b["decode_samples"])
    rep.update(ratio=ratios[chosen], ci=[lo, hi])
    if arms[chosen].get("void_missing") or b.get("void_missing"):
        rep.update(verdict="INCONCLUSIVE", reason="a void phase was not re-run")
    elif lo >= 3.0:
        rep.update(verdict="SUPPORTED", reason="CI lower bound >= 3.0 and the gate passed")
    elif hi < 3.0:
        rep.update(verdict="REFUTED", reason="CI upper bound < 3.0")
    elif high and all(not arms[k]["gate"] for k in high):
        rep.update(verdict="REFUTED", reason="every arm with a point ratio >= 3.0 fails the quality gate")
    else:
        rep.update(verdict="INCONCLUSIVE", reason="CI straddles 3.0")
    return rep


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    if cmd == "arms":
        print(json.dumps({"arms": ARMS, "images": IMG, "served": SERVED_NAME}, indent=1))
    elif cmd in ("render", "derive-vllm"):
        import yaml
        if cmd == "render":
            docs, out = render(a[0]), a[1]
        else:
            with open(a[1]) as f:
                docs, out = derive_vllm(a[0], [d for d in yaml.safe_load_all(f) if d]), a[2]
        with open(out, "w") as f:
            yaml.safe_dump_all(docs, f, sort_keys=False)
    elif cmd == "gsm8k":
        url, model, items, out = a[:4]
        rows = [json.loads(x) for x in open(items) if x.strip()]
        lo, hi = (int(x) for x in (a[4] if len(a) > 4 else "0-49").split("-"))
        res = gsm8k(url, model, [r for r in rows if lo <= r["idx"] <= hi])
        json.dump(res, open(out, "w"), indent=1)
        print(f"gsm8k {res['correct']}/{res['n']} errors={res['errors']} wall={res['wall_s']}s")
    elif cmd == "void":
        samples = [json.loads(x) for x in open(a[0]) if x.strip()]
        w = (float(a[3]), float(a[4])) if len(a) >= 5 else (None, None)
        v, why = void(samples, float(a[1]), float(a[2]), w0=w[0], w1=w[1])
        print(json.dumps({"void": v, "reason": why}))
        return 1 if v else 0
    elif cmd == "active-bytes":
        print(json.dumps(active_bytes(a[0])))
    elif cmd == "served":
        ok, why = served(a[0])
        print(why)
        return 0 if ok else 1
    elif cmd == "recheck":
        for d in recheck(a[0]):
            print(f"failed (server stopped answering): {d}")
    elif cmd == "carry":
        print(" ".join(carry(a[0], int(a[1]), a[2:])))
    elif cmd == "score-a":
        print(json.dumps(score_a(a[0]), indent=1))
    else:
        raise SystemExit(f"h54.py: unknown command {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

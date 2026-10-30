#!/usr/bin/env python3
"""fit: size a model to this host, and guard the host while it serves.

  fit.py probe [--sysroot DIR] [--stream FILE]          host/v1 JSON: what this host has free now
  fit.py fetch REPO [--revision R] [--file F] --out DIR config + weights/v1 from a Hugging Face repo
  fit.py weights DIR                                    weights/v1 from local safetensors/GGUF files
  fit.py plan --host FILE --model DIR [--variants DIR...] [--engine E] [--ctx N] [--seqs N]
              [--gpus N] [--kv-dtype T] [--mode text|explain|json]
  fit.py guard-step --fit FILE --state FILE [--sysroot DIR]   one guard decision (JSON on stdout)
  fit.py busy-cards --host FILE                         Intel cards others hold (the fit leaves them out)

Three layers, each a plain function:

  probe(sysroot)   reads /proc and /sys under sysroot: Intel GPU VRAM total and in
                   use (xe/i915 sysfs and DRM fdinfo, so busy cards are seen), RAM,
                   MemAvailable, swap, the cgroup memory limit, NUMA nodes, THP and
                   hugetlb pools, PSI memory pressure, CPU ISA (AMX, AVX-512, AVX2)
                   and memory bandwidth (a cached STREAM run if present, else the
                   theoretical peak from SMBIOS type 17, labelled as such).
  fit(host, model) pure: weights (per tensor class, from the safetensors or GGUF
                   headers), KV cache per token from config.json (full-attention,
                   sliding-window and linear-attention layers counted apart),
                   activations and engine overhead (estimates, formula printed),
                   and the host and GPU reserves. It tries placements in order,
                   one GPU, several GPUs (vLLM tensor parallel, llama.cpp layer
                   split), MoE experts to DRAM (llama.cpp --n-cpu-moe, only on a CPU
                   with AMX or AVX-512 and only when the DRAM side's bandwidth bound
                   stays above a floor), CPU only, and returns the first that fits
                   with its context length, sequence count, engine flags and
                   Kubernetes memory request/limit. When nothing fits it refuses,
                   says why, and names the smallest variant that would.
  guard_step(...)  pure: one decision from a PSI/MemAvailable sample. A server whose
                   KV cache is in host RAM steps down a ladder of smaller (seqs,
                   context) rungs under pressure and back up after a calm spell; a
                   server whose KV cache is in VRAM is bounded by its pod limit and
                   only reported; an emergency with nothing left to shed stops it.

Decided at start: placement, tensor parallel size, engine memory flags, pod memory
request and limit. Dynamic: the guard's rung (seqs and context), applied by a
restart with the smaller fit, because none of vLLM, OVMS or llama-server can lower
admission or KV size on a live server.

Policy constants (overridable from the CLI) are named below with their reasons.
Engine overheads are estimates from the formula printed in --explain, not
measurements; K3D_GPU_FIT_OVERHEAD_GIB replaces them with a measured value.

Python standard library only.
"""

import argparse
import bisect
import glob
import json
import math
import os
import re
import struct
import sys
import urllib.request

GIB = 1024 ** 3
MIB = 1024 ** 2
HOST_SCHEMA = "k3d-gpu.host/v1"
WEIGHTS_SCHEMA = "k3d-gpu.weights/v1"
FIT_SCHEMA = "k3d-gpu.fit/v1"

# ---- policy -------------------------------------------------------------------
# Host reserve: what the OS and other services keep, on top of what they already
# use (MemAvailable has that subtracted). The larger of 10% of RAM and 4 GiB.
HOST_RESERVE_PCT = 0.10
HOST_RESERVE_FLOOR = 4 * GIB
# GPU reserve per card: driver, display and other clients' growth. The larger of
# 5% of VRAM and 1 GiB.
GPU_RESERVE_PCT = 0.05
GPU_RESERVE_FLOOR = 1 * GIB
# A card whose other clients hold half its VRAM or more is busy: another
# workload owns it, so tensor parallelism does not count it.
BUSY_CARD_PCT = 0.50
# Kubernetes limit = request = the computed host need plus 10% for allocator
# slack, rounded up to whole GiB.
LIMIT_SLACK = 0.10
# Context ladder: the native maximum first, then halving to 4096.
CTX_FLOOR = 4096
# Sequences a context must hold at once before the ladder takes it, and the context
# an agent session needs: a placement that holds TARGET_SEQS at CTX_GOOD (or the
# model's cap, if lower) wins over one that does not; only when none does is the
# best smaller fit taken, down to one sequence at CTX_FLOOR, and flagged.
TARGET_SEQS = 4
CTX_GOOD = 32768
MAX_SEQS = 256
# DRAM-side decode floor: the fast-RAM path is taken only when DRAM bandwidth /
# bytes read from DRAM per token stays at or above this many tokens/s.
MIN_DRAM_TOK_S = 5.0
# Batch the activation estimate is sized for (vLLM --max-num-batched-tokens as
# the agent stack sets it, llama.cpp's default -ub 512).
VLLM_BATCHED_TOKENS = 2048
LLAMA_UBATCH = 512
# llama-server context checkpoints per slot (-ctxcp; v0.8.0 default 32, common/common.h). Each
# is a host-RAM copy of the slot's SWA cache and recurrent state (tools/server/server-context.cpp
# create_checkpoint, LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY). A request makes them at the last user
# message and 4 + n_ubatch and 4 tokens before its prompt's end; an appended turn restores from
# those, so four keep the latest request's set. The host prompt cache (--cache-ram, default
# 8192 MiB) is turned off with --cache-ram 0: a slot's KV stays in its own cache.
LLAMA_CTX_CHECKPOINTS = 4
# Fixed runtime per GPU process (device context, allocator pools, collective
# buffers) and per pod on the host (Python, torch or the server binary). Estimates.
GPU_RUNTIME = 1 * GIB
HOST_RUNTIME = {"vllm": 6 * GIB, "ovms": 2 * GIB, "llama.cpp": 1 * GIB}
# /dev/shm emptyDir (medium: Memory) the vLLM pod mounts: charged to the pod.
VLLM_SHM = 2 * GIB

# Guard thresholds (PSI from Documentation/accounting/psi.rst: "some" = share of
# wall time at least one task stalled on memory, "full" = all non-idle tasks).
GUARD_SOME_AVG10 = 20.0      # step down one rung
GUARD_FULL_AVG10 = 5.0       # emergency: step to the lowest rung (stop if already there)
GUARD_CALM_SOME_AVG60 = 5.0  # calm: may step back up
GUARD_CALM_SAMPLES = 60      # consecutive calm samples (5 min at 5 s) before stepping up
GUARD_EMERGENCY_SAMPLES = 3  # emergency samples before a VRAM-KV server is stopped
GUARD_COOLDOWN_SAMPLES = 12  # samples held after a step (1 min at 5 s): the restart reloads the
                             # weights and PSI avg10 needs ~10 s to decay

DTYPE_BYTES = {"F64": 8, "I64": 8, "U64": 8, "F32": 4, "I32": 4, "U32": 4, "F16": 2, "BF16": 2,
               "I16": 2, "U16": 2, "I8": 1, "U8": 1, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1,
               "F8_E8M0": 1, "F4": 0.5, "F6_E2M3": 0.75, "F6_E3M2": 0.75}
KV_DTYPE_BYTES = {"auto": None, "bf16": 2, "fp16": 2, "f16": 2, "fp8": 1, "q8_0": 34 / 32, "q4_0": 18 / 32}
# KV cache types each engine takes and the fit sets a flag for (vLLM --kv-cache-dtype,
# llama.cpp -ctk/-ctv; OVMS's KV_CACHE_PRECISION is not set by the fit).
KV_DTYPES_OF = {"vllm": ("fp8", "fp16", "bf16"), "llama.cpp": ("f16", "fp16", "bf16", "q8_0", "q4_0")}
# Tokens a sliding-window layer's cache holds per sequence (swa_tokens). vLLM: the window.
# llama.cpp v0.8.0: GGML_PAD(n_swa + n_ubatch, 256) cells per stream (src/llama-kv-cache-iswa.cpp).
# OVMS: OpenVINO GenAI 2026.4 has only KV_CACHE and LINEAR_ATTENTION_CACHE types
# (src/cpp/src/continuous_batching/cache/cache_type.hpp), and a KV block spans every
# attention layer (kv_cache_manager.hpp), so sliding-window layers hold the whole context.
SWA_CACHE = {"vllm": "window", "llama.cpp": "llama.cpp", "ovms": "full"}

# Tensor classes by name: the parts a placement moves separately.
CLASSES = (
    ("mtp", re.compile(r"(^|[._])mtp([._]|$)|nextn")),
    ("vision", re.compile(r"visual|vision|embed_vision|audio|mm_proj|mmproj|multi_modal")),
    ("ngram", re.compile(r"ngram|ple_embedding|per_layer_token_embd|embed_tokens_per_layer")),
    ("experts", re.compile(r"\.experts\.|_exps\.|\.experts$")),
    ("embed", re.compile(r"embed_tokens|token_embd|wte\.|tok_embeddings|text_embeddings")),
    ("lm_head", re.compile(r"lm_head|^output\.weight$")),
)
LOOKUP_CLASSES = ("embed", "ngram")  # gathered by token id: cheap from DRAM


def gib(n):
    return n / GIB


def g1(n):
    return "%.1f GiB" % gib(n)


def tensor_class(name):
    for cls, rx in CLASSES:
        if rx.search(name):
            return cls
    return "other"


# ---- probe ----------------------------------------------------------------------
def _read(root, path, default=None):
    try:
        with open(os.path.join(root, path.lstrip("/")), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def _int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def meminfo(root):
    out = {}
    for line in (_read(root, "/proc/meminfo") or "").splitlines():
        m = re.match(r"^(\w+):\s+(\d+)(?:\s+kB)?$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2)) * (1024 if line.rstrip().endswith("kB") else 1)
    return out


def psi(root, path="/proc/pressure/memory"):
    """{"some": {"avg10":..,"avg60":..,"avg300":..,"total":..}, "full": {...}} or None."""
    text = _read(root, path)
    if text is None:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        vals = {}
        for kv in parts[1:]:
            k, _, v = kv.partition("=")
            try:
                vals[k] = float(v) if k != "total" else int(v)
            except ValueError:
                pass
        out[parts[0]] = vals
    return out or None


def cgroup_limit(root):
    """(limit bytes or None, current bytes or None, path) of this process's cgroup v2 chain."""
    rel = None
    for line in (_read(root, "/proc/self/cgroup") or "").splitlines():
        if line.startswith("0::"):
            rel = line[3:].strip()
    if rel is None:
        return None, None, None
    limit, cur, at = None, None, rel
    path = rel
    while True:
        base = "/sys/fs/cgroup" + ("" if path in ("", "/") else path)
        mx = (_read(root, base + "/memory.max") or "").strip()
        if mx and mx != "max" and mx.isdigit():
            if limit is None or int(mx) < limit:
                limit, at = int(mx), path or "/"
                c = _int(_read(root, base + "/memory.current"))
                cur = c
        if path in ("", "/"):
            break
        path = os.path.dirname(path)
    return limit, cur, at


def cpu_isa(root):
    flags, model = set(), None
    for line in (_read(root, "/proc/cpuinfo") or "").splitlines():
        if line.startswith("flags") and not flags:
            flags = set(line.split(":", 1)[1].split())
        elif line.startswith("model name") and model is None:
            model = line.split(":", 1)[1].strip()
    cpus = len(re.findall(r"^processor\s*:", _read(root, "/proc/cpuinfo") or "", re.M))
    return {"model": model, "cpus": cpus,
            "amx": {"amx_tile", "amx_bf16"} <= flags or {"amx_tile", "amx_int8"} <= flags,
            "avx512": "avx512f" in flags, "avx2": "avx2" in flags,
            "flags": sorted(f for f in flags if f.startswith(("amx", "avx512f", "avx512_bf16", "avx_vnni", "avx2")))}


def numa(root):
    nodes = []
    for d in sorted(glob.glob(os.path.join(root, "sys/devices/system/node/node[0-9]*")),
                    key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)))):
        mi = {}
        for line in (_read(root, d[len(root):] + "/meminfo") or "").splitlines():
            m = re.match(r"^Node \d+ (\w+):\s+(\d+) kB", line)
            if m:
                mi[m.group(1)] = int(m.group(2)) * 1024
        nodes.append({"node": int(re.sub(r"\D", "", os.path.basename(d))),
                      "total": mi.get("MemTotal"), "free": mi.get("MemFree"),
                      "cpulist": (_read(root, d[len(root):] + "/cpulist") or "").strip() or None})
    return nodes


def hugepages(root):
    thp = (_read(root, "/sys/kernel/mm/transparent_hugepage/enabled") or "").strip()
    m = re.search(r"\[(\w+)\]", thp)
    pools = {}
    for d in glob.glob(os.path.join(root, "sys/kernel/mm/hugepages/hugepages-*kB")):
        size = int(re.search(r"(\d+)kB", d).group(1)) * 1024
        rel = d[len(root):]
        pools[str(size)] = {"total": _int(_read(root, rel + "/nr_hugepages")) or 0,
                            "free": _int(_read(root, rel + "/free_hugepages")) or 0}
    return {"thp": m.group(1) if m else None, "pools": pools}


def dmi17(raw):
    """(configured MT/s, data width bits) of one SMBIOS type 17 record, or None.
    Offsets per SMBIOS DSP0134 7.18, as the launcher's dmi17 reads them."""
    b = bytearray(raw)
    if len(b) < 34 or b[0] != 17 or b[1] < 34 or len(b) < b[1]:
        return None
    size = b[12] | b[13] << 8
    if size in (0, 65535):
        return None
    cfg = b[32] | b[33] << 8
    if cfg == 65535 and b[1] >= 92:
        cfg = b[88] | b[89] << 8 | b[90] << 16 | b[91] << 24
    dw = b[10] | b[11] << 8
    return (cfg, dw) if cfg and dw and dw != 65535 else None


def bandwidth(root, stream_file=None):
    """{"gbs", "source", "label"}: a cached STREAM triad run, else the SMBIOS peak."""
    if stream_file:
        try:
            with open(stream_file, encoding="utf-8") as f:
                s = json.load(f)
            if float(s.get("triad_gbs", 0)) > 0:
                return {"gbs": float(s["triad_gbs"]), "source": stream_file, "label": "measured (STREAM triad)"}
        except (OSError, ValueError, TypeError):
            pass
    recs = []
    for e in sorted(glob.glob(os.path.join(root, "sys/firmware/dmi/entries/17-*/raw"))):
        try:
            with open(e, "rb") as f:
                r = dmi17(f.read())
        except OSError:
            r = None
        if r:
            recs.append(r)
    if recs:
        mts = min(r[0] for r in recs)
        width = sum(r[1] for r in recs) // 8
        return {"gbs": round(mts * width / 1000.0, 1), "source": "/sys/firmware/dmi/entries/17-*/raw",
                "label": "theoretical (%d DIMMs x %d MT/s x their data width, one DIMM per channel assumed)"
                         % (len(recs), mts)}
    return {"gbs": None, "source": None, "label": "unknown (no STREAM run cached; SMBIOS needs root)"}


def _fdinfo_vram(root):
    """{pci addr: {client key: (pid, comm, bytes)}} from DRM fdinfo, deduplicated per drm-client-id."""
    out = {}
    for fd in glob.glob(os.path.join(root, "proc/[0-9]*/fdinfo/*")):
        text = _read(root, fd[len(root):]) or ""
        if "drm-driver:" not in text:
            continue
        kv = dict(re.findall(r"^([\w-]+):\s*(.+)$", text, re.M))
        if kv.get("drm-driver", "").strip() not in ("xe", "i915"):
            continue
        pdev = kv.get("drm-pdev", "").strip()
        cid = kv.get("drm-client-id", "").strip()
        total = 0
        for k, v in kv.items():
            if re.match(r"^drm-(resident|total)-(vram\d+|local\d*)$", k) and k.startswith("drm-resident"):
                total += _bytes(v)
        if not total:
            for k, v in kv.items():
                if re.match(r"^drm-total-(vram\d+|local\d*)$", k):
                    total += _bytes(v)
        pid = re.search(r"/proc/(\d+)/fdinfo/", "/" + fd[len(root):].lstrip("/")).group(1)
        comm = (_read(root, "/proc/%s/comm" % pid) or "?").strip()
        out.setdefault(pdev, {})[cid or pid + fd] = (int(pid), comm, total)
    return out


def _bytes(v):
    m = re.match(r"^\s*(\d+)\s*(KiB|MiB|GiB|B)?\s*$", v)
    if not m:
        return 0
    return int(m.group(1)) * {"KiB": 1024, "MiB": MIB, "GiB": GIB, "B": 1, None: 1}[m.group(2)]


def _iowr(nr, size):
    """DRM_IOWR(DRM_COMMAND_BASE + nr, size) from include/uapi/drm/drm.h and asm-generic/ioctl.h."""
    return (3 << 30) | (size << 16) | (ord("d") << 8) | (0x40 + nr)


def drm_vram_query(node, driver):
    """(total, used) bytes of a card's device-local memory regions, from the driver's
    memory-region query on its render node; None when the query fails.
    xe: DRM_IOCTL_XE_DEVICE_QUERY, DRM_XE_DEVICE_QUERY_MEM_REGIONS, struct drm_xe_mem_region
    (class VRAM = 1, total_size, used); include/uapi/drm/xe_drm.h.
    i915: DRM_IOCTL_I915_QUERY, DRM_I915_QUERY_MEMORY_REGIONS, struct
    drm_i915_memory_region_info (class DEVICE = 1, probed_size, unallocated_size, which
    equals probed_size without CAP_PERFMON); include/uapi/drm/i915_drm.h."""
    import ctypes
    import fcntl
    try:
        fd = os.open(node, os.O_RDWR)
    except OSError:
        return None
    try:
        if driver == "xe":
            req = _iowr(0x00, 40)
            q = bytearray(struct.pack("<QIIQ2Q", 0, 1, 0, 0, 0, 0))
            fcntl.ioctl(fd, req, q)
            size = struct.unpack_from("<I", q, 12)[0]
            buf = ctypes.create_string_buffer(size)
            q = bytearray(struct.pack("<QIIQ2Q", 0, 1, size, ctypes.addressof(buf), 0, 0))
            fcntl.ioctl(fd, req, q)
            n, hdr, rec, f_cls, f_tot, f_used = struct.unpack_from("<I", buf.raw)[0], 8, 88, 0, 8, 16
        elif driver == "i915":
            req = _iowr(0x39, 16)
            item = ctypes.create_string_buffer(struct.pack("<QiIQ", 4, 0, 0, 0), 24)
            q = bytearray(struct.pack("<IIQ", 1, 0, ctypes.addressof(item)))
            fcntl.ioctl(fd, req, q)
            length = struct.unpack_from("<i", item.raw, 8)[0]
            if length <= 0:
                return None
            buf = ctypes.create_string_buffer(length)
            ctypes.memmove(item, struct.pack("<QiIQ", 4, length, 0, ctypes.addressof(buf)), 24)
            fcntl.ioctl(fd, req, q)
            n, hdr, rec, f_cls, f_tot, f_used = struct.unpack_from("<I", buf.raw)[0], 16, 88, 0, 8, 16
        else:
            return None
        total = used = 0
        for i in range(n):
            off = hdr + i * rec
            (cls,) = struct.unpack_from("<H", buf.raw, off + f_cls)
            (tot,) = struct.unpack_from("<Q", buf.raw, off + f_tot)
            (val,) = struct.unpack_from("<Q", buf.raw, off + f_used)
            if cls == 1:
                total += tot
                used += val if driver == "xe" else tot - val
        return total, used
    except OSError:
        return None
    finally:
        os.close(fd)


def gpus(root, vram_query=None):
    """Intel GPUs with device-local memory. Total and in use from the driver's
    memory-region query (xe has no sysfs file for either); in use is also summed from
    DRM fdinfo (drm-resident-vram0 / drm-resident-local0 per client), which names
    who holds it. vram_query(render node path, driver) -> (total, used) | None."""
    if vram_query is None:
        vram_query = drm_vram_query if root in ("/", "") else (lambda node, drv: None)
    used = _fdinfo_vram(root)
    out = []
    for card in sorted(glob.glob(os.path.join(root, "sys/class/drm/card[0-9]*"))):
        if "-" in os.path.basename(card):
            continue
        rel = card[len(root):]
        if (_read(root, rel + "/device/vendor") or "").strip() != "0x8086":
            continue
        drv = os.path.basename(os.path.realpath(os.path.join(card, "device/driver"))) if os.path.exists(
            os.path.join(card, "device/driver")) else None
        if drv not in ("xe", "i915"):
            continue
        nodes = sorted(glob.glob(os.path.join(card, "device/drm/renderD*")))
        q = vram_query("/dev/dri/" + os.path.basename(nodes[0]), drv) if nodes else None
        if not q or not q[0]:
            continue  # integrated (shares host RAM), or the query is not readable
        bdf = os.path.basename(os.path.realpath(os.path.join(card, "device")))
        clients = used.get(bdf, {})
        users = sorted(({"pid": p, "comm": c, "bytes": b} for p, c, b in clients.values() if b),
                       key=lambda u: -u["bytes"])
        out.append({"card": os.path.basename(card), "bdf": bdf, "driver": drv, "vram_total": q[0],
                    "vram_used": max(q[1], sum(u["bytes"] for u in users)), "users": users})
    return out


def probe(root="/", stream_file=None, vram_query=None, node_limit=None):
    """host/v1. node_limit: the memory limit of the container the cluster node runs in
    (docker's HostConfig.Memory for a k3d node), which caps what its pods can hold."""
    mi = meminfo(root)
    lim, cur, cpath = cgroup_limit(root)
    if node_limit and (lim is None or node_limit < lim):
        lim, cur, cpath = node_limit, 0, "k3d node container"
    return {
        "schema": HOST_SCHEMA,
        "cpu": cpu_isa(root),
        "ram": {"total": mi.get("MemTotal"), "available": mi.get("MemAvailable"),
                "swap_total": mi.get("SwapTotal", 0), "swap_free": mi.get("SwapFree", 0)},
        "cgroup": {"limit": lim, "current": cur, "path": cpath},
        "numa": numa(root),
        "hugepages": hugepages(root),
        "psi": psi(root),
        "bandwidth": bandwidth(root, stream_file),
        "gpus": gpus(root, vram_query),
    }


# ---- weights: per-class bytes from file headers ----------------------------------
def classify(tensors):
    """[(name, bytes)] -> {class: {"bytes", "tensors"}}."""
    cls = {}
    for name, n in tensors:
        c = cls.setdefault(tensor_class(name), {"bytes": 0, "tensors": 0})
        c["bytes"] += n
        c["tensors"] += 1
    return cls


def safetensors_tensors(read):
    """read(off, n) -> bytes. [(name, bytes)] from the header's data_offsets."""
    (n,) = struct.unpack("<Q", read(0, 8))
    hdr = json.loads(read(8, n))
    return [(k, v["data_offsets"][1] - v["data_offsets"][0]) for k, v in hdr.items() if k != "__metadata__"]


class _Buf:
    """Sequential reader over a ranged source, fetching in growing chunks."""

    def __init__(self, read, size):
        self.read, self.size, self.pos, self.buf, self.base = read, size, 0, b"", 0

    def take(self, n):
        while self.pos + n > self.base + len(self.buf):
            want = max(n, len(self.buf) or 4 * MIB, 4 * MIB)
            start = self.base + len(self.buf)
            if start >= self.size:
                raise ValueError("truncated GGUF header")
            self.buf += self.read(start, min(want, self.size - start))
        lo = self.pos - self.base
        self.pos += n
        return self.buf[lo:lo + n]


GGUF_KEYS = ("general.architecture", "general.alignment", "split.no", "split.count", "split.tensors.count")


def gguf_tensors(read, size):
    """[(name, bytes)] of one GGUF file (see gguf_header)."""
    return gguf_header(read, size)["tensors"]


def gguf_header(read, size):
    """{"tensors": [(name, bytes)], "data_start", "kv": {GGUF_KEYS present}} from a GGUF
    header: each tensor's bytes are the gap to the next tensor's offset (the last one's
    to the end of the file), so no ggml type table is needed. Layout per
    ggml/docs/gguf.md (v2/v3). A gguf-split shard is a whole GGUF holding its own share
    of the tensors, with split.no / split.count / split.tensors.count in its keys
    (llama.cpp tools/gguf-split)."""
    b = _Buf(read, size)
    if b.take(4) != b"GGUF":
        raise ValueError("not a GGUF file")
    (ver,) = struct.unpack("<I", b.take(4))
    if ver < 2:
        raise ValueError("GGUF v%d is not supported" % ver)
    n_t, n_kv = struct.unpack("<QQ", b.take(16))
    if n_t > 1 << 24 or n_kv > 1 << 24:
        raise ValueError("corrupt GGUF header: %d tensors, %d keys" % (n_t, n_kv))
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

    def string():
        (ln,) = struct.unpack("<Q", b.take(8))
        if ln > 1 << 26:
            raise ValueError("corrupt GGUF header: a %d-byte string" % ln)
        return b.take(ln).decode("utf-8", "replace")

    def value(t):
        if t == 8:
            return string()
        if t == 9:
            et, cnt = struct.unpack("<IQ", b.take(12))
            if cnt > size:
                raise ValueError("corrupt GGUF header: an array of %d items" % cnt)
            if et in fmt:
                b.take(struct.calcsize(fmt[et]) * cnt)
            else:
                for _ in range(cnt):
                    value(et)
            return None
        return struct.unpack(fmt[t], b.take(struct.calcsize(fmt[t])))[0]

    kv = {}
    for _ in range(n_kv):
        key = string()
        (t,) = struct.unpack("<I", b.take(4))
        v = value(t)
        if key in GGUF_KEYS and v is not None:
            kv[key] = v
    align = kv.get("general.alignment") if isinstance(kv.get("general.alignment"), int) else 32
    infos = []
    for _ in range(n_t):
        name = string()
        (nd,) = struct.unpack("<I", b.take(4))
        b.take(8 * nd)
        _t, off = struct.unpack("<IQ", b.take(12))
        infos.append((name, off))
    data = (b.pos + align - 1) // align * align
    infos.sort(key=lambda x: x[1])
    out = []
    for i, (name, off) in enumerate(infos):
        end = infos[i + 1][1] if i + 1 < len(infos) else size - data
        out.append((name, end - off))
    return {"tensors": out, "data_start": data, "kv": kv}


def _file_reader(path):
    def read(off, n):
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(n)
    return read


def _http_reader(url, token=None):
    def read(off, n):
        req = urllib.request.Request(url, headers={"Range": "bytes=%d-%d" % (off, off + n - 1)})
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    return read


GGUF_SPLIT = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
# Files that are not the model itself: the vision projector and the drafters
# (mtp-*, dflash-*, *-draft*, *-assistant*) a GGUF repo ships beside it.
GGUF_AUX = re.compile(r"(^|/)(mmproj|mtp-|dflash-)|mmproj|[-_.]draft|assistant", re.I)
GGUF_QUANT = re.compile(r"(?<![A-Za-z0-9])(IQ[1-4]_[A-Z]+|TQ[12]_0|Q[2-8]_K(?:_[SML]|_XL)?|Q[2-8]_[01]|MXFP4|NVFP4|BF16|F16|F32)"
                        r"(?![A-Za-z0-9])", re.I)


def gguf_groups(names):
    """{label: [files]} of the GGUF models in a repo listing: one entry per quant, a
    gguf-split model's shards together in shard order. The label is the quant in the
    file name (IQ4_NL, Q8_0, ...), else the file stem."""
    out = {}
    for n in sorted(names):
        base = n.rsplit("/", 1)[-1]
        if not base.endswith(".gguf") or GGUF_AUX.search(base):
            continue
        stem = GGUF_SPLIT.sub("", base)
        stem = stem[:-5] if stem.endswith(".gguf") else stem
        q = GGUF_QUANT.findall(stem)
        label = q[-1].upper() if q else stem
        out.setdefault(label, []).append(n)
    return {k: sorted(v) for k, v in out.items()}


def pick_group(groups, want):
    """The group --file names: a file of it, its label, or its file stem (any case)."""
    w = want.rsplit("/", 1)[-1]
    for label, files in groups.items():
        stems = {GGUF_SPLIT.sub("", f.rsplit("/", 1)[-1]).replace(".gguf", "").lower() for f in files}
        if w in (f.rsplit("/", 1)[-1] for f in files) or w.lower() == label.lower() or w.lower() in stems:
            return label
    raise ValueError("no GGUF group matches %r (groups: %s)" % (want, ", ".join(sorted(groups))))


def quant_bits(label):
    m = re.match(r"^(?:I?Q|TQ)(\d)", label, re.I)
    if m:
        return int(m.group(1))
    return {"MXFP4": 4, "NVFP4": 4, "BF16": 16, "F16": 16, "F32": 32}.get(label.upper(), 99)


def group_order(groups):
    """Labels in the order the fit tries them: the optimizer's llama.cpp precision, int4
    (console/model_opt.py want_precision), first; then fewer bits, most first; more bits
    last, fewest first. NVFP4 runs on NVIDIA only and is left out."""
    labels = [g for g in groups if g.upper() != "NVFP4"]
    return sorted(labels, key=lambda g: ((0, 0) if quant_bits(g) == 4 else (1, -quant_bits(g)) if quant_bits(g) < 4
                                         else (2, quant_bits(g)), g))


def gguf_group_weights(files, opener, source):
    """weights/v1 of one GGUF model: every shard's header read and its tensors summed.
    opener(name) -> (read, size). A gguf-split model must list split.count shards and
    split.tensors.count tensors in total, or this raises."""
    tensors, flist, want, want_t = [], [], None, None
    for n in files:
        read, size = opener(n)
        h = gguf_header(read, size)
        tensors += h["tensors"]
        flist.append({"name": n, "bytes": size})
        if "split.count" in h["kv"]:
            want = h["kv"]["split.count"]
            want_t = h["kv"].get("split.tensors.count", want_t)
    if want is not None and want != len(files):
        raise ValueError("%s: split.count %d but %d of them listed (%s)" % (source, want, len(files), ", ".join(files)))
    if want_t is not None and want_t != len(tensors):
        raise ValueError("%s: split.tensors.count %d but the shards hold %d tensors" % (source, want_t, len(tensors)))
    doc = weights_doc("gguf", flist, tensors, source)
    doc["tensor_count"] = len(tensors)
    return doc


def gguf_weights_doc(groups, opener, source, file=None, config_from=None):
    """weights/v1 of a GGUF repo: per group in "groups", the first in group_order (or the
    one --file names) at the top level. file limits the headers read to that group."""
    labels = [pick_group(groups, file)] if file else group_order(groups)
    if not labels:
        raise ValueError("%s lists no GGUF model files" % source)
    docs = {g: gguf_group_weights(groups[g], opener, "%s:%s" % (source, g)) for g in labels}
    top = dict(docs[labels[0]])
    top.update(source=source, group=labels[0], groups=docs)
    if config_from:
        top["config_from"] = config_from
    return top


def weight_files(names):
    """Serving weight files of a repo listing: safetensors shards, else the GGUF models
    (not the vision projector or the drafters), else OpenVINO IR .bin files."""
    st = [n for n in names if n.endswith(".safetensors")]
    if st:
        return "safetensors", st
    gg = [f for fs in gguf_groups(names).values() for f in fs]
    if gg:
        return "gguf", gg
    ov = [n for n in names if n.endswith(".bin") and "openvino" in n]
    if ov:
        return "openvino", ov
    return None, []


def weights_doc(fmt, files, tensors, source, config_from=None):
    """weights/v1. OpenVINO IR has no tensor index here, so its .bin files are classed
    by file name (openvino_language_model, _text_embeddings_, _vision_, _mtp_)."""
    cls = classify(tensors) if tensors else classify([(f["name"], f["bytes"]) for f in files])
    doc = {"schema": WEIGHTS_SCHEMA, "format": fmt, "source": source, "files": files,
           "classes": cls, "total_bytes": sum(c["bytes"] for c in cls.values())}
    if config_from:
        doc["config_from"] = config_from
    return doc


def weights_local(d):
    names = sorted(os.listdir(d))
    fmt, files = weight_files(names)
    if not fmt:
        raise ValueError("%s holds no .safetensors, .gguf or OpenVINO .bin weights" % d)
    if fmt == "gguf":
        return gguf_weights_doc(gguf_groups(names), lambda n: (_file_reader(os.path.join(d, n)),
                                                                os.path.getsize(os.path.join(d, n))), d)
    tensors, flist = [], []
    for n in files:
        p = os.path.join(d, n)
        size = os.path.getsize(p)
        flist.append({"name": n, "bytes": size})
        if fmt == "safetensors":
            tensors += safetensors_tensors(_file_reader(p))
    return weights_doc(fmt, flist, tensors, d)


def hf_fetch(repo, revision, out, file=None, token=None, endpoint="https://huggingface.co"):
    """config.json (and quantization_config.json when listed) plus weights.json for one
    repo revision, from the tree listing and ranged reads of each weight file's header."""
    def get(url, binary=False):
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        return data if binary else data.decode("utf-8")

    rev = revision or "main"
    # A branch or tag moves: record the commit it names, so what is served later (a GGUF
    # download, a conversion) is the revision these headers were read from.
    if not re.match(r"^[0-9a-f]{40}$", rev):
        rev = json.loads(get("%s/api/models/%s/revision/%s" % (endpoint, repo, rev)))["sha"]
    tree = json.loads(get("%s/api/models/%s/tree/%s?recursive=true" % (endpoint, repo, rev)))
    sizes = {t["path"]: t.get("lfs", {}).get("size") or t.get("size") or 0 for t in tree if t.get("type") == "file"}
    os.makedirs(out, exist_ok=True)
    for meta in ("config.json", "tokenizer_config.json", "quantization_config.json", "openvino_config.json"):
        if meta in sizes:
            with open(os.path.join(out, meta), "w", encoding="utf-8") as f:
                f.write(get("%s/%s/resolve/%s/%s" % (endpoint, repo, rev, meta)))
    config_from = None
    if "config.json" not in sizes and "README.md" in sizes:
        # A GGUF repo ships no config.json: read the architecture from the model card's
        # base_model (its front matter), the checkpoint the GGUF was converted from.
        base = card_base_model(get("%s/%s/resolve/%s/README.md" % (endpoint, repo, rev)))
        if base:
            info = json.loads(get("%s/api/models/%s" % (endpoint, base)))
            config_from = "%s@%s" % (base, info["sha"])
            for meta in ("config.json", "tokenizer_config.json"):
                try:
                    data = get("%s/%s/resolve/%s/%s" % (endpoint, base, info["sha"], meta))
                except OSError:
                    continue
                with open(os.path.join(out, meta), "w", encoding="utf-8") as f:
                    f.write(data)
    fmt, files = weight_files(sorted(sizes))
    if not fmt:
        raise ValueError("%s at %s lists no weight files" % (repo, rev))
    if fmt == "gguf":
        # Every GGUF model in the repo (each split model's every shard): the fit picks one.
        doc = gguf_weights_doc(gguf_groups(sorted(sizes)),
                               lambda n: (_http_reader("%s/%s/resolve/%s/%s" % (endpoint, repo, rev, n), token), sizes[n]),
                               "%s@%s" % (repo, rev), file, config_from)
    else:
        tensors = []
        for n in files:
            if fmt == "safetensors":
                tensors += safetensors_tensors(_http_reader("%s/%s/resolve/%s/%s" % (endpoint, repo, rev, n), token))
        doc = weights_doc(fmt, [{"name": n, "bytes": sizes[n]} for n in files], tensors,
                          "%s@%s" % (repo, rev), config_from)
    with open(os.path.join(out, "weights.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    return doc


def card_base_model(readme):
    """First base_model of a model card's YAML front matter, or None."""
    m = re.match(r"^---\n(.*?)\n---", readme, re.S)
    if not m:
        return None
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        if line.startswith("base_model:"):
            v = line.split(":", 1)[1].strip()
            if v:
                return v.strip("'\"")
            for nxt in lines[i + 1:]:
                if nxt.strip().startswith("- "):
                    return nxt.strip()[2:].strip("'\"")
                if not nxt.startswith(" "):
                    break
    return None


# ---- model profile from config.json ---------------------------------------------
def text_config(cfg):
    return cfg.get("text_config") or cfg.get("llm_config") or cfg


def model_type(cfg):
    return str(text_config(cfg).get("model_type") or cfg.get("model_type") or "").replace("_text", "")


def kv_profile(cfg, kv_bytes, engine=None):
    """Per-token KV bytes for full-attention layers, per-token-within-window bytes for
    sliding-window layers, and per-sequence state bytes for linear-attention layers.
    engine sets how many tokens a sliding-window layer holds (swa_tokens)."""
    tc = text_config(cfg)
    n = tc.get("num_hidden_layers")
    types = tc.get("layer_types") or ["full_attention"] * n
    heads = tc.get("num_attention_heads")
    kvh = tc.get("num_key_value_heads") or heads
    hd = tc.get("head_dim") or tc["hidden_size"] // heads
    # an unknown layer type counts as full attention (upper bound)
    kinds = ["swa" if t in ("sliding_attention", "sliding_window_attention", "local_attention") else
             "linear" if t in ("linear_attention", "mamba", "gated_delta_net") else "full" for t in types]
    full, swa, lin = kinds.count("full"), kinds.count("swa"), kinds.count("linear")
    gkvh = tc.get("num_global_key_value_heads") or kvh
    ghd = tc.get("global_head_dim") or hd
    lines = []
    per_full = 2 * gkvh * ghd * kv_bytes
    lines.append("full attention: %d layers x 2 (K, V) x %d kv heads x %d head dim x %s B = %d B/token"
                 % (full, gkvh, ghd, _num(kv_bytes), full * per_full))
    if tc.get("attention_k_eq_v"):
        lines.append("  attention_k_eq_v: global layers share K and V; counted as two tensors (upper bound)")
    idx = 0
    if tc.get("indexer_head_dim") and tc.get("indexer_kv_heads"):
        idx = tc["indexer_kv_heads"] * tc["indexer_head_dim"] * kv_bytes
        lines.append("sparse-attention indexer keys: %d layers x %d head x %d dim x %s B = %d B/token"
                     % (full, tc["indexer_kv_heads"], tc["indexer_head_dim"], _num(kv_bytes), full * idx))
    window = tc.get("sliding_window") or 0
    per_swa = 2 * kvh * hd * kv_bytes
    swa_cache = SWA_CACHE.get(engine, "window")
    if swa:
        lines.append("sliding window: %d layers x 2 x %d kv heads x %d head dim x %s B = %d B/token, %s"
                     % (swa, kvh, hd, _num(kv_bytes), swa * per_swa, {
                         "full": "for the whole context: OpenVINO GenAI 2026.4 has no sliding-window cache type"
                                 " (continuous_batching/cache/cache_type.hpp), every attention layer gets every token",
                         "llama.cpp": "for GGML_PAD(%d window + %d ubatch, 256) = %d tokens per sequence at most"
                                      " (src/llama-kv-cache-iswa.cpp)" % (window, LLAMA_UBATCH,
                                                                          _pad(window + LLAMA_UBATCH, 256)),
                         "window": "at most %d tokens" % window}[swa_cache]))
    state = 0
    if lin:
        vh, kd = tc.get("linear_num_value_heads", 0), tc.get("linear_key_head_dim", 0)
        vd, kh = tc.get("linear_value_head_dim", 0), tc.get("linear_num_key_heads", 0)
        ck = tc.get("linear_conv_kernel_dim", 4)
        ssm_b = 4 if str(tc.get("mamba_ssm_dtype", "float32")) == "float32" else 2
        ssm = vh * kd * vd * ssm_b
        ch = 2 * kh * kd + vh * vd
        conv = (ck - 1) * ch * 2
        state = lin * (ssm + conv)
        lines.append("linear attention state: %d layers x (%d v heads x %d x %d x %d B + conv (%d-1) x %d ch x 2 B)"
                     " = %s per sequence, any length" % (lin, vh, kd, vd, ssm_b, ck, ch, _mb(state)))
    return {"full_layers": full, "full_per_token": full * (per_full + idx), "swa_layers": swa,
            "swa_per_token": swa * per_swa, "window": window, "swa_cache": swa_cache, "linear_layers": lin,
            "state_per_seq": state, "kv_heads": gkvh, "kinds": kinds, "lines": lines}


def _pad(n, to):
    """GGML_PAD: n rounded up to a multiple of to."""
    return (n + to - 1) // to * to


def _num(x):
    return ("%g" % x) if isinstance(x, float) else str(x)


def _mb(n):
    return "%.1f MiB" % (n / MIB)


def ctx_cap(cfg, tokenizer_cfg):
    """(max context, why): config max_position_embeddings, lowered by the tokenizer's
    model_max_length when that is set and smaller (OpenVINO/Qwen3.8-27B-int4-ov: 131072)."""
    tc = text_config(cfg)
    cap, why = tc.get("max_position_embeddings") or 4096, "max_position_embeddings"
    tml = (tokenizer_cfg or {}).get("model_max_length")
    if isinstance(tml, (int, float)) and 0 < tml < cap:
        cap, why = int(tml), "tokenizer_config model_max_length"
    return int(cap), why


def model_profile(cfg, weights, tokenizer_cfg=None):
    tc = text_config(cfg)
    cls = weights["classes"]
    b = {k: cls.get(k, {}).get("bytes", 0) for k in ("experts", "embed", "ngram", "lm_head", "vision", "mtp", "other")}
    # The MTP head loads only for speculative decoding; a GGUF loads every tensor it holds.
    serve = sum(v for k, v in b.items() if k != "mtp" or weights["format"] == "gguf")
    mtp_blocks = (tc.get("mtp_num_hidden_layers") or tc.get("num_nextn_predict_layers") or 1) if b["mtp"] else 0
    ne = tc.get("num_experts") or tc.get("n_routed_experts") or tc.get("num_local_experts") or 0
    k = tc.get("num_experts_per_tok") or tc.get("top_k_experts") or tc.get("num_experts_per_token") or 0
    dtype = str(tc.get("dtype") or tc.get("torch_dtype") or cfg.get("torch_dtype") or "bfloat16")
    cap, capwhy = ctx_cap(cfg, tokenizer_cfg)
    q = cfg.get("quantization_config") or {}
    return {
        "arch": model_type(cfg),
        "layers": tc.get("num_hidden_layers"),
        "hidden": tc.get("hidden_size"),
        "heads": tc.get("num_attention_heads"),
        "vocab": tc.get("vocab_size"),
        "intermediate": max(tc.get("intermediate_size") or 0, (tc.get("moe_intermediate_size") or 0) * max(k, 1)),
        "max_ctx": cap, "ctx_why": capwhy,
        "experts": ne, "experts_per_tok": k, "moe": bool(ne and b["experts"]),
        # MTP (nextn) blocks a GGUF holds after its trunk (llama.cpp's converter keeps them all
        # or none): llama.cpp counts them in its layers (block_count = trunk + nextn_predict_layers).
        # mtp_blocks: the MTP blocks the weights have, which a GGUF conversion keeps (llama_profile).
        "nextn": mtp_blocks if weights["format"] == "gguf" else 0, "mtp_blocks": mtp_blocks,
        "bytes": b, "serve_bytes": serve, "lookup_bytes": b["embed"] + b["ngram"], "format": weights["format"],
        "dtype_bytes": 2 if dtype in ("bfloat16", "float16") else 4,
        # compressed-tensors kv_cache_scheme: vLLM switches its KV cache to that type (fp8) on its own.
        "kv_scheme_bits": (q.get("kv_cache_scheme") or {}).get("num_bits"),
    }


def llama_profile(m):
    """The model llama.cpp serves: a GGUF as it is; other weights as the GGUF the agent's
    conversion writes from them. convert_hf_to_gguf.py runs without --no-mtp, so that GGUF
    keeps the MTP blocks after the trunk (llama.cpp v0.8.0 conversion/qwen.py _QwenMtpMixin
    adds mtp_num_hidden_layers to block_count) and is served like a GGUF that holds them."""
    if m["format"] == "gguf" or not m["mtp_blocks"]:
        return m
    return dict(m, nextn=m["mtp_blocks"], serve_bytes=m["serve_bytes"] + m["bytes"]["mtp"])


# ---- engines ---------------------------------------------------------------------------
# Which engine serves which architecture on Intel GPUs or the CPU, from each engine's
# own release notes and recipes. An architecture not listed is not refused (the engine
# may load it), only marked unchecked in --explain.
ENGINE_ARCH = {
    "vllm": {
        "gemma4": (True, "recipes.vllm.ai/Google/gemma-4-26B-A4B-it: XPU recipe validated on 4x Arc Pro B60/B70"),
        "qwen3_5": (True, "recipes.vllm.ai/Qwen/Qwen3.8-27B: XPU validated on 4x Arc Pro B60/B70, --enforce-eager"),
        "qwen4_exp": (False, "vllm/models/qwen4_exp: 'Qwen4Exp currently supports CUDA and ROCm only'"),
    },
    "ovms": {
        "gemma4": (True, "OVMS 2026.4.0 release notes: Gemma-4-26B-a4B on CPU and GPU"),
        "qwen3_5": (True, "OpenVINO 2026.4 release notes: Qwen3.8 27B on CPU and GPU"),
        "qwen4_exp": (False, "qwen4_exp is in no OpenVINO release's supported list"),
    },
    "llama.cpp": {
        "gemma4": (True, "llama.cpp src/llama-arch.cpp: gemma4"),
        "qwen3_5": (True, "llama.cpp src/llama-arch.cpp: qwen35"),
        "qwen4_exp": (True, "llama.cpp src/llama-arch.cpp: qwen4exp (PR #27742)"),
    },
}
FORMAT_ENGINE = {"safetensors": "vllm", "openvino": "ovms", "gguf": "llama.cpp"}


def engines_for(fmt, arch):
    """[(engine, note)]: the engine that loads this format; when that engine cannot run
    the architecture, llama.cpp instead, after a GGUF conversion (sized from these tensors)."""
    native = FORMAT_ENGINE.get(fmt)
    if not native:
        return []
    ok, why = ENGINE_ARCH.get(native, {}).get(arch, (True, "%s: architecture %s not checked" % (native, arch or "?")))
    if ok:
        return [(native, why)]
    out = [(None, why)]
    if native != "llama.cpp" and ENGINE_ARCH["llama.cpp"].get(arch, (False,))[0]:
        out.append(("llama.cpp", "%s; %s weights need a GGUF conversion (llama.cpp convert_hf_to_gguf.py), "
                                 "sized here from the %s tensors" % (ENGINE_ARCH["llama.cpp"][arch][1], fmt, fmt)))
    return out


# GGUF conversion of safetensors weights for llama.cpp, as llama.cpp v0.8.0 (b11960)
# convert_hf_to_gguf.py reads them (conversion/base.py dequant_model). Unquantized weights
# keep their 16-bit type. FP8 (quant_method fp8, base.py:470; compressed-tensors
# float-quantized 8-bit floats, base.py:527) is dequantized and stored as Q8_0 with
# --fp8-as-q8 (base.py:909 tensor_force_quant): the 1 B per weight the fit counts. The
# other methods it reads (bitnet, gptq, compressed-tensors int and packed, modelopt) are
# dequantized to 16 bits, a GGUF two to four times the bytes the fit sized; any other
# raises "Quant method is not yet supported" (base.py:575).
def convert_plan(cfg, fmt):
    """{"args": convert_hf_to_gguf.py arguments, "error": why not}."""
    if fmt != "safetensors":
        return {"args": None, "error": "%s weights have no GGUF conversion (convert_hf_to_gguf.py reads safetensors)" % fmt}
    q = cfg.get("quantization_config") or {}
    method = q.get("quant_method")
    if not method:
        tc = text_config(cfg)
        dt = tc.get("dtype") or tc.get("torch_dtype") or cfg.get("dtype") or cfg.get("torch_dtype") or "bfloat16"
        return {"args": ["--outtype", "f16" if dt in ("float16", "half") else "bf16"], "error": None}
    groups = list((q.get("config_groups") or {}).values())
    w = (groups[0].get("weights") or {}) if len(groups) == 1 else {}
    if method == "fp8" or (method == "compressed-tensors" and q.get("format") == "float-quantized"
                           and w.get("type") == "float" and w.get("num_bits") == 8):
        return {"args": ["--outtype", "bf16", "--fp8-as-q8"], "error": None}
    if method in ("bitnet", "gptq", "compressed-tensors", "modelopt", "modelopt_mixed"):
        return {"args": None, "error": "llama.cpp v0.8.0's convert_hf_to_gguf.py dequantizes quant_method %s to 16 bits "
                                       "(conversion/base.py dequant_model), a GGUF larger than the weights this fit "
                                       "sized; a bf16 or FP8 checkpoint of this model converts" % method}
    return {"args": None, "error": "llama.cpp v0.8.0's convert_hf_to_gguf.py does not read quant_method %s "
                                   "(conversion/base.py:575); a bf16 or FP8 checkpoint of this model converts" % method}


# ---- fit ---------------------------------------------------------------------------
def host_budget(host, reserve_pct=HOST_RESERVE_PCT, reserve_floor=HOST_RESERVE_FLOOR):
    ram = host["ram"]
    if not ram.get("total") or ram.get("available") is None:
        raise ValueError("host RAM unknown: /proc/meminfo has no MemTotal or MemAvailable")
    reserve = max(int(ram["total"] * reserve_pct), reserve_floor)
    avail = ram["available"]
    lines = ["host RAM: MemTotal %s, MemAvailable %s (others already hold %s)"
             % (g1(ram["total"]), g1(avail), g1(ram["total"] - avail))]
    cg = host.get("cgroup") or {}
    if cg.get("limit"):
        room = cg["limit"] - (cg.get("current") or 0)
        lines.append("cgroup %s: memory.max %s, memory.current %s, room %s"
                     % (cg.get("path"), g1(cg["limit"]), g1(cg.get("current") or 0), g1(room)))
        avail = min(avail, room)
    lines.append("host reserve = max(%d%% x MemTotal, %s) = %s" % (reserve_pct * 100, g1(reserve_floor), g1(reserve)))
    free = max(avail - reserve, 0)
    lines.append("host budget = %s - %s = %s" % (g1(avail), g1(reserve), g1(free)))
    sw = (ram.get("swap_total") or 0) - (ram.get("swap_free") or 0)
    if sw >= GIB:
        lines.append("swap in use %s: the host is already under pressure" % g1(sw))
    return free, reserve, lines


def card_busy(g):
    """A card whose other clients hold BUSY_CARD_PCT of its VRAM or more."""
    return g["vram_used"] >= g["vram_total"] * BUSY_CARD_PCT


def busy_cards(host):
    """[(card, bdf, who)] of the busy cards in a host/v1 document."""
    return [(g["card"], g["bdf"], ", ".join("%s[%d] %s" % (u["comm"], u["pid"], g1(u["bytes"])) for u in g["users"][:3])
             or "%s in use" % g1(g["vram_used"])) for g in host.get("gpus") or [] if card_busy(g)]


def gpu_budget(host, reserve_pct=GPU_RESERVE_PCT, reserve_floor=GPU_RESERVE_FLOOR):
    cards, lines = [], []
    for g in host.get("gpus") or []:
        reserve = max(int(g["vram_total"] * reserve_pct), reserve_floor)
        free = g["vram_total"] - g["vram_used"]
        busy = card_busy(g)
        who = ", ".join("%s[%d] %s" % (u["comm"], u["pid"], g1(u["bytes"])) for u in g["users"][:3])
        lines.append("%s (%s): VRAM %s, in use %s%s; reserve max(%d%%, %s) = %s; budget %s%s"
                     % (g["card"], g["bdf"], g1(g["vram_total"]), g1(g["vram_used"]), " by " + who if who else "",
                        reserve_pct * 100, g1(reserve_floor), g1(reserve), g1(max(free - reserve, 0)),
                        "; BUSY (others hold >= %d%%), not used" % (BUSY_CARD_PCT * 100) if busy else ""))
        if not busy:
            cards.append({"card": g["card"], "total": g["vram_total"], "budget": max(free - reserve, 0)})
    cards.sort(key=lambda c: -c["budget"])
    return cards, lines


def act_bytes(m, tokens, seqs):
    """Activation peak estimate: the widest matmul's input and output for one batch
    (2 tensors x 2 B), plus float32 logits for every sequence sampled in a step."""
    width = max(m["intermediate"] * 2, m["hidden"] * 4)
    return tokens * width * 2 * 2 + seqs * m["vocab"] * 4


def swa_tokens(kv, ctx):
    """Tokens one sequence's sliding-window layers hold at context ctx. vLLM: the window.
    llama.cpp: its SWA cache of GGML_PAD(min(n_ctx_seq, n_swa + n_ubatch), 256) cells per
    stream (one stream per slot with -np set). OVMS: the whole context."""
    w = kv["window"] or ctx
    mode = kv.get("swa_cache", "window")
    if mode == "full":
        return ctx
    if mode == "llama.cpp":
        return min(_pad(ctx, 256), _pad(w + LLAMA_UBATCH, 256))
    return min(ctx, w)


def kv_parts(kv, ctx):
    """(full attention, sliding window, linear state) KV bytes of one sequence at context ctx."""
    return kv["full_per_token"] * ctx, kv["swa_per_token"] * swa_tokens(kv, ctx), kv["state_per_seq"]


def per_seq(kv, ctx, scale=1.0):
    f, w, st = kv_parts(kv, ctx)
    return int((f + w) * scale + st * scale)


# ---- llama.cpp over several GPUs ----------------------------------------------------------
def llama_layer_devices(ts, n_layer):
    """Device of each repeating layer and of the output layer (the last entry), as llama.cpp
    v0.8.0 assigns them with -ngl past the layer count (src/llama-model.cpp load_tensors):
    upper_bound(cumsum(ts) / sum(ts), il / (n_layer + 1)). Integer layer counts summing to
    n_layer + 1 give each device exactly its count, the output layer on the last one."""
    total, cum, splits = float(sum(ts)), 0.0, []
    for t in ts:
        cum += t
        splits.append(cum / total)
    return [bisect.bisect_right(splits, il / (n_layer + 1)) for il in range(n_layer + 1)]


def llama_output_copy(m):
    """Bytes of the token embedding llama.cpp loads a second time as the output tensor when
    the model ties its embeddings (no output.weight / lm_head): TENSOR_DUPLICATED, placed on
    the output layer's device (src/llama-model-loader.cpp). On the CPU it maps the same file
    pages; on a GPU it is a copy."""
    return m["bytes"]["embed"] if not m["bytes"]["lm_head"] else 0


def llama_layer_bytes(m, kv, ctx, seqs, n_cpu_moe):
    """([weights], [KV]) on the GPU per repeating layer, the output layer last: the class
    totals spread evenly over the trunk's layers, the experts of the first n_cpu_moe layers in
    host RAM (--n-cpu-moe), and each layer's own KV (its device holds it) for seqs sequences.
    A GGUF's MTP (nextn) blocks follow the trunk as repeating layers (llama.cpp v0.8.0 counts
    them in n_layer_all, src/llama-model.cpp) with their share of the MTP tensors and no KV:
    the cache covers il < n_layer(), the trunk. The weights add up to the bytes to serve less
    the lookup tables, plus the output copy of tied embeddings. The output layer holds lm_head
    (or that copy); the vision projector counts with layer 0, on the first card:
    tools/mtmd/clip.cpp loads it on the first GPU."""
    n, nx = max(m["layers"], 1), m["nextn"]
    b = m["bytes"]
    exp, other = b["experts"] / n, b["other"] / n
    f, w, st = kv_parts(kv, ctx)
    per = {"full": f / max(kv["full_layers"], 1), "swa": w / max(kv["swa_layers"], 1),
           "linear": st / max(kv["linear_layers"], 1)}
    kinds = kv.get("kinds") or []
    wl = [other + (exp if il >= n_cpu_moe else 0) for il in range(n)] + [b["mtp"] / max(nx, 1)] * nx
    wl[0] += b["vision"]
    kl = [per[kinds[il]] * seqs if il < len(kinds) else 0 for il in range(n)] + [0] * nx
    out = (m["serve_bytes"] - m["lookup_bytes"] - b["experts"] - b["other"] - b["vision"] - (b["mtp"] if nx else 0)
           + llama_output_copy(m))
    return wl + [out], kl + [0]


def llama_split(layer_bytes, k, room):
    """-ts for k cards as integer layer counts (the last card's count includes the output
    layer) that keep every card's layers within room bytes, its largest load as small as it
    can be; None when no split does."""
    last = len(layer_bytes) - 1

    def fill(cap):
        ts, i = [], 0
        for _ in range(k - 1):
            start, used = i, 0
            while i < last and used + layer_bytes[i] <= cap:
                used += layer_bytes[i]
                i += 1
            ts.append(i - start)
        if sum(layer_bytes[i:]) > cap:
            return None
        return ts + [len(layer_bytes) - i]

    if fill(room) is None:
        return None
    lo, hi = max(layer_bytes), room
    while hi - lo > MIB:
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if fill(mid) else (mid, hi)
    return fill(hi)


def llama_cards(ts, layer_bytes):
    """Each card's bytes for ts: the layers llama_layer_devices puts on it."""
    out = [0] * len(ts)
    for il, dev in enumerate(llama_layer_devices(ts, len(layer_bytes) - 1)):
        out[dev] += layer_bytes[il]
    return out


def _llama_search(m, kv, ladder, relaxed, want_seqs, k, room, n_cpus):
    """(ctx, seqs, n_cpu_moe, ts) for llama.cpp on k cards of room bytes each: the most
    context at which TARGET_SEQS (or want_seqs) sequences fit, then the fewest layers whose
    experts leave the GPU (from n_cpus), then as many sequences as fit (capped at MAX_SEQS).
    relaxed: at the ladder's last rung one sequence is enough. None when nothing fits."""
    for ctx in ladder:
        need = want_seqs or (1 if relaxed and ctx == ladder[-1] else TARGET_SEQS)
        for n_cpu in n_cpus:
            def split(n):
                wl, kl = llama_layer_bytes(m, kv, ctx, n, n_cpu)
                return llama_split([a + b for a, b in zip(wl, kl)], k, room)
            if not split(need):
                continue
            n = want_seqs
            if not n:
                lo, hi = need, MAX_SEQS
                if split(hi):
                    lo = hi
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if split(mid) else (lo, mid)
                n = lo
            return ctx, n, n_cpu, split(n)
    return None


def _llama_card_lines(use, ts, wl, kl, ov, m):
    """One explain line per card of a llama.cpp split."""
    lines, first = [], 0
    for c, t, w, kvb in zip(use, ts, llama_cards(ts, wl), llama_cards(ts, kl)):
        lines.append("  %s (-ts %d): layers %s, weights %s + KV %s + overhead %s = %s, budget %s"
                     % (c["card"], t, "%d-%d" % (first, first + t - 1) if t else "none", g1(w), g1(kvb), g1(ov),
                        g1(w + kvb + ov), g1(c["budget"])))
        first += t
    if m["nextn"]:
        a, z = m["layers"], m["layers"] + m["nextn"] - 1
        lines.append("  (%s the GGUF's MTP %s: nextn tensors, no KV)"
                     % ("layer %d is" % a if a == z else "layers %d-%d are" % (a, z), "block" if a == z else "blocks"))
    lines.append("  (layer %d is the output layer: %s)" % (len(wl) - 1, "lm_head" if not llama_output_copy(m) else
                                                            "a %s copy of the tied token embedding" % g1(llama_output_copy(m))))
    return lines


def ckpt_seq(kv, ctx):
    """Host bytes of one llama-server slot's context checkpoints at context ctx: kv["ckpts"]
    of them, each the slot's SWA cache (GGML_PAD(min(n_ctx_seq, n_swa + n_ubatch), 256)
    cells, src/llama-kv-cache-iswa.cpp) and recurrent state. A model with neither makes none."""
    return kv.get("ckpts", 0) * (kv["swa_per_token"] * swa_tokens(kv, ctx) + kv["state_per_seq"])


def ctx_ladder(max_ctx, want=None):
    if want:
        return [min(want, max_ctx)]
    out, c = [], max_ctx
    while c >= CTX_FLOOR:
        out.append(c)
        c //= 2
    return out or [max_ctx]


def size_kv(kv, budget, ladder, want_seqs, scale=1.0, relaxed=False, host_ckpt=False):
    """(ctx, seqs): the most context at which TARGET_SEQS (or want_seqs) whole sequences fit
    in budget, then as many sequences as fit at that context (capped at MAX_SEQS).
    relaxed: at the ladder's last rung one sequence is enough. host_ckpt: the budget also
    holds each sequence's context checkpoints (KV in host RAM)."""
    need = want_seqs or TARGET_SEQS
    for ctx in ladder:
        s = per_seq(kv, ctx, scale) + (ckpt_seq(kv, ctx) if host_ckpt else 0)
        n = int(budget // s) if s else MAX_SEQS
        if n >= need or (relaxed and ctx == ladder[-1] and n >= 1 and not want_seqs):
            return ctx, min(want_seqs or n, MAX_SEQS)
    return None, 0


def _tp_sizes(m, n):
    return [t for t in (1, 2, 4, 8) if t <= n and m["heads"] and m["heads"] % t == 0]


def fit(host, cfg, weights, engine=None, ctx=None, seqs=None, gpus=None, kv_dtype="auto", overhead=None,
        host_reserve_pct=HOST_RESERVE_PCT, host_reserve_floor=HOST_RESERVE_FLOOR, name=None, tokenizer_cfg=None,
        device=None):
    """The fit/v1 document. Pure: reads its arguments only. device "cpu" or "gpu" limits
    the placements to that side (the device the cluster already chose)."""
    m = model_profile(cfg, weights, tokenizer_cfg)
    bad = None
    if m["serve_bytes"] <= 0:
        bad = "weights read as 0 bytes (%s): the weight headers were not read, or a split model's shards are missing" \
              % (weights.get("source") or "?")
    elif m["format"] != "openvino" and m["experts"] and not m["bytes"]["experts"]:
        bad = "no expert tensors in the weights of a %d-expert MoE (%s): incomplete or misread weight headers" \
              % (m["experts"], weights.get("source") or "?")
    if bad:
        return {"schema": FIT_SCHEMA, "model": name, "format": m["format"], "arch": m["arch"], "fits": False,
                "degraded": False, "host_reserve": 0, "explain": ["REFUSED: " + bad], "reason": bad,
                "summary": "fit: %s REFUSED: %s" % (name, bad)}
    ex = ["model %s: %s (%s) weights, %s to serve" % (name or "?", m["format"], m["arch"] or "?", g1(m["serve_bytes"]))]
    for k, v in m["bytes"].items():
        if v:
            ex.append("  %-8s %s%s" % (k, g1(v), {"mtp": " (not loaded: speculative decoding off)" if m["format"] != "gguf"
                                                     else " (loaded with the GGUF)",
                                                     "ngram": " (lookup table)", "embed": " (lookup table)"}.get(k, "")))
    if m["moe"]:
        ex.append("  MoE: %d experts, %d active per token: %s of expert weights read per token"
                  % (m["experts"], m["experts_per_tok"], g1(m["bytes"]["experts"] * m["experts_per_tok"] / m["experts"])))
    ex.append("context cap: %d (%s)" % (m["max_ctx"], m["ctx_why"]))
    hfree, hres, hl = host_budget(host, host_reserve_pct, host_reserve_floor)
    cards, gl = gpu_budget(host)
    if device == "cpu":
        cards = []
    ex += hl + (["GPUs:"] + ["  " + s for s in gl] if gl else ["GPUs: none with device-local memory"])
    cpu = host["cpu"]
    isa = "AMX" if cpu["amx"] else "AVX-512" if cpu["avx512"] else "AVX2" if cpu["avx2"] else "no AVX2"
    bw = host["bandwidth"]
    ex.append("CPU: %s, %s; DRAM bandwidth %s, %s" % (cpu.get("model") or "?", isa,
              "%g GB/s" % bw["gbs"] if bw.get("gbs") else "unknown", bw["label"]))
    full = ctx_ladder(m["max_ctx"], ctx)
    if ctx or seqs:
        passes = [("as asked", full, False)]
    else:
        good = min(m["max_ctx"], CTX_GOOD)
        passes = [("target: %d seqs at >= %d tokens" % (TARGET_SEQS, good), [c for c in full if c >= good], False),
                  ("fallback: down to 1 seq at %d tokens" % full[-1], full, True)]
    cands = engines_for(m["format"], m["arch"])
    if engine:
        asked = [c for c in cands if c[0] == engine]
        if not asked:
            why = "; ".join(n for _, n in cands) or "no engine serves %s weights" % m["format"]
            ex.append("engine %s: not a candidate for %s (%s) weights: %s" % (engine, m["format"], m["arch"] or "?", why))
            return {"schema": FIT_SCHEMA, "model": name, "format": m["format"], "arch": m["arch"], "fits": False,
                    "degraded": False, "host_reserve": 0, "explain": ex,
                    "reason": "%s cannot serve these %s weights (%s)" % (engine, m["format"], why),
                    "summary": "fit: %s does not fit this host: %s cannot serve these %s weights (%s)"
                               % (name, engine, m["format"], why)}
        cands = asked
    kvs, ms = {}, {}
    for eng, note in cands:
        ex.append("engine %s: %s" % (eng or FORMAT_ENGINE.get(m["format"], "?"), note))
        if not eng:
            continue
        ms[eng] = llama_profile(m) if eng == "llama.cpp" else m
        if ms[eng] is not m:
            ex.append("  the GGUF conversion keeps the MTP head (convert_hf_to_gguf.py without --no-mtp): "
                      "%d block(s) after the trunk, %s more to serve, %s"
                      % (ms[eng]["nextn"], g1(m["bytes"]["mtp"]), g1(ms[eng]["serve_bytes"])))
        if kv_dtype != "auto" and kv_dtype not in KV_DTYPES_OF.get(eng, ()):
            raise ValueError("--kv-dtype %s is not a KV cache type of %s (%s)"
                             % (kv_dtype, eng, ", ".join(KV_DTYPES_OF.get(eng, ())) or "auto only"))
        kvb = KV_DTYPE_BYTES.get(kv_dtype) or (1 if eng == "vllm" and m["kv_scheme_bits"] == 8 else m["dtype_bytes"])
        kvs[eng] = kv_profile(cfg, kvb, eng)
        kvs[eng]["dtype"] = kv_dtype if kv_dtype != "auto" else ("fp8" if kvb == 1 else "auto")
        kvs[eng]["ckpts"] = LLAMA_CTX_CHECKPOINTS if eng == "llama.cpp" else 0
        ex.append("  KV cache (%s B per element%s):" % (_num(kvb), ", the checkpoint's kv_cache_scheme"
                                                       if kvb == 1 and kv_dtype == "auto" else ""))
        ex += ["    " + s for s in kvs[eng]["lines"]]
        if eng == "llama.cpp":
            ex.append("    host prompt cache off (--cache-ram 0); context checkpoints (-ctxcp %d) in host RAM: "
                      "%d x the slot's SWA cache and recurrent state = %s per slot at %d tokens"
                      % (LLAMA_CTX_CHECKPOINTS, LLAMA_CTX_CHECKPOINTS, _mb(ckpt_seq(kvs[eng], full[0])), full[0]))
    tried, picked, degraded = [], None, False
    for i, (label, ladder, relaxed) in enumerate(passes):
        ex.append("placements, %s:" % label)
        for eng, note in cands:
            if not eng or not ladder:
                continue
            # vLLM's CPU build is a placement only when asked for (--device cpu: the EAT
            # overlay); the agent's vLLM images are the XPU and Gaudi builds.
            cpu_ok = device == "cpu" if eng == "vllm" else device != "gpu"
            for res in _placements(eng, ms[eng], kvs[eng], ladder, relaxed, seqs, gpus, cards, hfree, host, overhead,
                                   cpu_ok):
                res["note"] = note
                tried.append(res)
                ex.append("  %s/%s: %s" % (res["engine"], res["placement"],
                                           "FITS" if res["fits"] else "no: " + res["why"]))
                ex += ["    " + s for s in res["math"]]
                if res["fits"]:
                    picked, degraded = res, i > 0
                    break
            if picked:
                break
        if picked:
            break
    doc = {"schema": FIT_SCHEMA, "model": name, "format": m["format"], "arch": m["arch"], "fits": bool(picked),
           "degraded": degraded, "host_reserve": hres, "explain": ex}
    if not picked:
        if tried:
            why = tried[-1]["why"]
        elif any(e for e, _ in cands):
            # No placement was tried: no card left after the busy ones, and the CPU not allowed.
            why = "no Intel GPU with device-local memory is free"
        else:
            why = cands[0][1] if cands else "no engine serves %s weights" % m["format"]
        doc.update(reason=why, summary="fit: %s does not fit this host: %s" % (name, why))
        return doc
    rungs = guard_rungs(picked["ctx"], picked["seqs"])
    kvp = kvs[picked["engine"]]
    rung_kv, rung_limit = [], []
    for rs, rc in rungs:
        kb = per_seq(kvp, rc, picked["kv_scale"]) * rs
        if _whole_gib_pool(picked["engine"], picked["placement"]):
            kb = int(math.ceil(kb / GIB)) * GIB
        hb = (picked["host_bytes"] - picked["kv_bytes"] - picked["ckpt_bytes"] + kb + ckpt_seq(kvp, rc) * rs
              if picked["placement"] == "cpu" else picked["host_bytes"])
        rung_kv.append(int(kb))
        rung_limit.append("%dGi" % int(math.ceil(hb * (1 + LIMIT_SLACK) / GIB)))
    doc.update({k: picked[k] for k in ("engine", "placement", "ctx", "seqs", "tp", "gpus", "gpu_bytes", "host_bytes",
                                       "flags", "dram_tok_s", "note", "kv_bytes", "ckpt_bytes", "card_bytes")})
    doc["source"] = weights.get("source")
    doc["weight_bytes"] = weights.get("total_bytes") or m["serve_bytes"]
    if m["format"] == "gguf":
        doc["files"] = [f["name"] for f in weights.get("files") or []]
    if picked["engine"] == "llama.cpp" and m["format"] != "gguf":
        doc["convert"] = convert_plan(cfg, m["format"])
        ex.append("GGUF conversion: " + (" ".join(doc["convert"]["args"]) if doc["convert"]["args"]
                                         else "not possible: " + doc["convert"]["error"]))
    lim = int(math.ceil(picked["host_bytes"] * (1 + LIMIT_SLACK) / GIB))
    doc["k8s"] = {"memory_request": "%dGi" % lim, "memory_limit": "%dGi" % lim}
    doc["guard"] = {"rungs": rungs, "placement": picked["placement"], "some_avg10": GUARD_SOME_AVG10,
                    "full_avg10": GUARD_FULL_AVG10, "calm_some_avg60": GUARD_CALM_SOME_AVG60,
                    "calm_samples": GUARD_CALM_SAMPLES, "emergency_samples": GUARD_EMERGENCY_SAMPLES,
                    "cooldown_samples": GUARD_COOLDOWN_SAMPLES, "rung_kv_bytes": rung_kv,
                    "rung_memory_limit": rung_limit,
                    "mem_available_floor": hres // 2, "host_bytes": picked["host_bytes"]}
    where = {"gpu": "1 GPU", "gpu-tp": "%d GPUs, tensor parallel" % picked["gpus"],
             "gpu-split": "%d GPUs, layer split" % picked["gpus"],
             "moe-offload": "%d GPU(s) + experts and lookup tables in DRAM (%s)" % (picked["gpus"], isa),
             "cpu": "CPU (%s)" % isa}[picked["placement"]]
    doc["summary"] = ("fit: %s on %s via %s, context %d x %d seqs%s,%s host %s (pod limit %s), host reserve %s"
                      % (name, where, picked["engine"], picked["ctx"], picked["seqs"],
                         " (below the %d x %d target)" % (min(m["max_ctx"], CTX_GOOD), TARGET_SEQS) if degraded else "",
                         " GPU %s/card," % g1(picked["gpu_bytes"]) if picked["gpu_bytes"] else "",
                         g1(picked["host_bytes"]), doc["k8s"]["memory_limit"], g1(hres)))
    ex.append("k8s: memory request = limit = ceil(%s x %.2f) = %s" % (g1(picked["host_bytes"]), 1 + LIMIT_SLACK,
                                                                     doc["k8s"]["memory_limit"]))
    ex.append("guard rungs (seqs x context): " + ", ".join("%dx%d" % (s, c) for s, c in rungs))
    ex.append("engine flags: " + " ".join(picked["flags"]))
    return doc


def _overhead(eng, m, seqs, override):
    if override is not None:
        return int(override), "%s (K3D_GPU_FIT_OVERHEAD_GIB, measured)" % g1(override)
    tokens = VLLM_BATCHED_TOKENS if eng == "vllm" else LLAMA_UBATCH
    a = act_bytes(m, tokens, seqs)
    return GPU_RUNTIME + a, ("runtime %s + activations %d tok x %d wide x 2 x 2 B + logits %d seqs x %d vocab x 4 B"
                             " = %s (estimate)" % (g1(GPU_RUNTIME), tokens, max(m["intermediate"] * 2, m["hidden"] * 4),
                                                   seqs, m["vocab"], g1(GPU_RUNTIME + a)))


def _res(eng, place, fits, why="", math_lines=(), **kw):
    r = {"engine": eng, "placement": place, "fits": fits, "why": why, "math": list(math_lines),
         "ctx": None, "seqs": 0, "tp": 1, "gpus": 0, "gpu_bytes": 0, "host_bytes": 0, "flags": [], "dram_tok_s": None,
         "kv_bytes": 0, "kv_scale": 1.0, "ckpt_bytes": 0, "card_bytes": []}
    r.update(kw)
    return r


def _whole_gib_pool(eng, place):
    """KV pools set in whole GiB: OVMS --cache_size, vLLM's CPU VLLM_CPU_KVCACHE_SPACE."""
    return eng == "ovms" or (eng == "vllm" and place == "cpu")


def _kv_room(eng, place, left):
    """The KV room sequences are sized in: whole GiB for a whole-GiB pool, so the pool
    rounded up to hold them stays inside left."""
    return left // GIB * GIB if _whole_gib_pool(eng, place) else left


def _kv_pool(eng, place, kvbytes):
    """The KV bytes the engine sets aside for kvbytes of sequences."""
    return int(math.ceil(kvbytes / GIB)) * GIB if _whole_gib_pool(eng, place) else kvbytes


def _dram_rate(bw, nbytes):
    return bw * 1e9 / nbytes if bw and nbytes > 0 else None


def _placements(eng, m, kv, ladder, relaxed, want_seqs, want_gpus, cards, hfree, host, overhead, cpu_ok=True):
    """Placement results for one engine, in preference order: one GPU, several GPUs,
    MoE experts in DRAM (llama.cpp), CPU only."""
    cpu = host["cpu"]
    bw = host["bandwidth"].get("gbs")
    nseq0 = want_seqs or TARGET_SEQS
    ov, ovl = _overhead(eng, m, nseq0, overhead)
    # The pod limit is the host bytes plus LIMIT_SLACK, rounded up to a whole GiB.
    hfree = max(int(hfree / (1 + LIMIT_SLACK)) - GIB, 0)
    # llama.cpp keeps the input layer (token_embd, per_layer_token_embd: the n-gram
    # table) on the CPU: src/llama-model.cpp "always keep it on the CPU".
    on_host = m["lookup_bytes"] if eng == "llama.cpp" else 0
    # OpenVINO's GPU plugin compiles from a host copy of the IR, so the pod's host side
    # counts the weights too (an upper bound: the copy is not kept after compile).
    compile_host = m["serve_bytes"] if eng == "ovms" else 0
    gpu_w_all = m["serve_bytes"] - on_host
    hostrt = HOST_RUNTIME[eng] + (VLLM_SHM if eng == "vllm" else 0)  # times the rank count for vLLM, below
    if cards and eng == "ovms" and want_gpus and want_gpus > 1:
        # The agent's OVMS pod holds one gpu.intel.com/xe; multi-GPU tensor parallel on Arc
        # is not documented (model_server docs/parameters.md).
        yield _res(eng, "gpu", False, "OVMS serves one GPU: %d asked" % want_gpus, gpus=want_gpus)
    elif cards:
        if eng == "vllm":
            counts = _tp_sizes(m, len(cards))
        elif eng == "llama.cpp":
            counts = list(range(1, len(cards) + 1))
        else:
            counts = [1]  # OVMS: multi-GPU tensor parallel on Arc is not documented (model_server docs/parameters.md)
        if want_gpus:
            counts = [c for c in counts if c == want_gpus] or [want_gpus]
        for k in counts:
            place = "gpu" if k == 1 else ("gpu-tp" if eng == "vllm" else "gpu-split")
            if k > len(cards):
                yield _res(eng, place, False, "%d GPUs asked, %d not busy" % (k, len(cards)), gpus=k)
                continue
            use = cards[:k]
            if eng == "llama.cpp" and k > 1:
                yield _llama_multi(m, kv, ladder, relaxed, want_seqs, use, ov, ovl, [0], on_host, hfree, bw)
                continue
            if eng == "vllm":
                # Every rank holds 1/k of the weights; KV heads split across ranks, or one
                # per rank (replicated) with fewer KV heads than ranks.
                budget = min(c["budget"] for c in use)
                w = gpu_w_all / k
                left = budget - w - ov
                scale = max(kv["kv_heads"] // k, 1) / kv["kv_heads"] if kv["kv_heads"] else 1.0 / k
                lines = ["per card: budget %s - weights %s / %d - overhead %s = %s for KV"
                         % (g1(budget), g1(gpu_w_all), k, ovl, g1(max(left, 0)))]
            else:
                # One card (llama.cpp on several is sized card by card in _llama_multi).
                budget = sum(c["budget"] for c in use)
                w = gpu_w_all + (llama_output_copy(m) if eng == "llama.cpp" else 0)
                left = budget - w - ov * k
                scale = 1.0
                lines = ["%s: budget %s - weights %s - overhead %d x %s = %s for KV"
                         % ("+".join(c["card"] for c in use), g1(budget), g1(w), k, ovl, g1(max(left, 0)))]
                if eng == "llama.cpp" and llama_output_copy(m):
                    lines.append("weights include the output tensor: a %s copy of the tied token embedding"
                                 % g1(llama_output_copy(m)))
            if on_host:
                lines.append("lookup tables %s stay in host RAM" % g1(on_host))
            if left <= 0:
                yield _res(eng, place, False, "weights + overhead %s exceed the GPU budget %s"
                           % (g1(w + ov * (1 if eng == "vllm" else k)), g1(budget)), lines, gpus=k, tp=k if eng == "vllm" else 1)
                continue
            c, n = size_kv(kv, _kv_room(eng, place, left), ladder, want_seqs, scale, relaxed)
            if not c:
                yield _res(eng, place, False, "KV room %s holds no %d-token sequence" % (g1(left), ladder[-1]),
                           lines, gpus=k)
                continue
            kvused = _kv_pool(eng, place, per_seq(kv, c, scale) * n)
            lines.append("KV: %d seqs x %s at %d tokens = %s%s" % (n, _mb(per_seq(kv, c, scale)), c, g1(kvused),
                                                                 " per card" if eng == "vllm" else ""))
            # vLLM runs one worker process per tensor-parallel rank, each with its own runtime.
            ck = ckpt_seq(kv, c) * n
            if ck:
                lines.append("context checkpoints: %d seqs x %s = %s in host RAM" % (n, _mb(ckpt_seq(kv, c)), g1(ck)))
            hb = int(hostrt + HOST_RUNTIME[eng] * (k - 1) * (eng == "vllm") + on_host + compile_host + ck)
            if hb > hfree:
                yield _res(eng, place, False, "host side %s exceeds the host budget %s" % (g1(hb), g1(hfree)), lines, gpus=k)
                continue
            gpu_use = int(w + ov + kvused) if eng == "vllm" else int((w + ov * k + kvused) / k)
            yield _res(eng, place, True, "", lines, ctx=c, seqs=n, tp=k if eng == "vllm" else 1, gpus=k,
                       gpu_bytes=gpu_use, host_bytes=hb, kv_bytes=int(kvused), kv_scale=scale, ckpt_bytes=ck,
                       card_bytes=[gpu_use] * k,
                       flags=_flags(eng, place, c, n, k, gpu_use, use, kvused, m, 0, kv))
    # MoE experts to DRAM (llama.cpp --n-cpu-moe). Every card is held to the smallest budget
    # of the cards used, so one more card can leave less room per card: the free cards by
    # budget, 1, 2, ... of them (or --gpus), and the plan with the most context, then the
    # fewest CPU-expert layers, then the most sequences, then the fewest cards.
    if eng == "llama.cpp" and cards and m["moe"]:
        ks = [want_gpus] if want_gpus else list(range(1, len(cards) + 1))
        if want_gpus and want_gpus > len(cards):
            yield _res(eng, "moe-offload", False, "%d GPUs asked, %d not busy" % (want_gpus, len(cards)), gpus=want_gpus)
        elif not (cpu["amx"] or cpu["avx512"]):
            yield _res(eng, "moe-offload", False, "the CPU has neither AMX nor AVX-512 for expert compute", gpus=ks[-1])
        elif not bw:
            yield _res(eng, "moe-offload", False, "DRAM bandwidth unknown (no STREAM run cached, SMBIOS unreadable)",
                       gpus=ks[-1])
        else:
            tries = [_llama_multi(m, kv, ladder, relaxed, want_seqs, cards[:k], ov, ovl, range(1, m["layers"] + 1),
                                  on_host, hfree, bw) for k in ks]
            ok = [r for r in tries if r["fits"]]
            if not ok:
                yield tries[-1]
            else:
                best = max(ok, key=lambda r: (r["ctx"], -r["n_cpu_moe"], r["seqs"], -r["gpus"]))
                if len(tries) > 1:
                    best["math"].append("cards tried, by budget: " + "; ".join(
                        "%d: %d x %d, experts of %d layers in DRAM" % (r["gpus"], r["ctx"], r["seqs"], r["n_cpu_moe"])
                        if r["fits"] else "%d: no" % r["gpus"] for r in tries))
                yield best
    # CPU only: weights, KV and activations all in DRAM (vLLM: its CPU build, the KV
    # pool sized by VLLM_CPU_KVCACHE_SPACE, as EAT's vLLM chart sets it).
    if cpu_ok:
        act = act_bytes(m, LLAMA_UBATCH, nseq0)
        left = hfree - m["serve_bytes"] - act - HOST_RUNTIME[eng]
        active = m["serve_bytes"] - m["lookup_bytes"]
        if m["moe"]:
            active -= m["bytes"]["experts"] * (1 - m["experts_per_tok"] / m["experts"])
        toks = _dram_rate(bw, active)
        lines = ["host budget / %.2f slack %s - weights %s - activations %s - runtime %s = %s for KV%s"
                 % (1 + LIMIT_SLACK, g1(hfree), g1(m["serve_bytes"]), g1(act), g1(HOST_RUNTIME[eng]), g1(max(left, 0)),
                    " and context checkpoints" if kv.get("ckpts") else "")]
        if toks:
            lines.append("DRAM decode bound: %g GB/s / %s read per token = %.1f tok/s" % (bw, g1(active), toks))
        if left <= 0:
            yield _res(eng, "cpu", False, "weights %s + runtime exceed the host budget %s"
                       % (g1(m["serve_bytes"]), g1(hfree)), lines)
            return
        if toks is not None and toks < MIN_DRAM_TOK_S:
            yield _res(eng, "cpu", False, "DRAM bandwidth gives %.1f tok/s, under the %g floor" % (toks, MIN_DRAM_TOK_S),
                       lines)
            return
        c, n = size_kv(kv, _kv_room(eng, "cpu", left), ladder, want_seqs, 1.0, relaxed, host_ckpt=True)
        if not c:
            yield _res(eng, "cpu", False, "KV room %s holds no %d-token sequence" % (g1(left), ladder[-1]), lines)
            return
        kvused = _kv_pool(eng, "cpu", per_seq(kv, c) * n)
        lines.append("KV: %d seqs x %s at %d tokens = %s" % (n, _mb(per_seq(kv, c)), c, g1(kvused)))
        ck = ckpt_seq(kv, c) * n
        if ck:
            lines.append("context checkpoints: %d seqs x %s = %s" % (n, _mb(ckpt_seq(kv, c)), g1(ck)))
        hb = int(m["serve_bytes"] + act + HOST_RUNTIME[eng] + kvused + ck)
        yield _res(eng, "cpu", True, "", lines, ctx=c, seqs=n, host_bytes=hb, kv_bytes=int(kvused), ckpt_bytes=ck,
                   dram_tok_s=round(toks, 1) if toks else None,
                   flags=_flags(eng, "cpu", c, n, 1, 0, [], kvused, m, 0, kv))


def _llama_multi(m, kv, ladder, relaxed, want_seqs, use, ov, ovl, n_cpus, on_host, hfree, bw):
    """llama.cpp on the cards in use, each holding its own layers, their KV and its own
    overhead within the smallest budget among them (the device plugin, not the fit, orders
    the cards inside the pod): a layer split (n_cpus [0]) or experts in DRAM (n_cpus 1..)."""
    k = len(use)
    place = "gpu-split" if list(n_cpus) == [0] else "moe-offload"
    budget = min(c["budget"] for c in use)
    room = budget - ov
    lines = ["%s: layer il on card upper_bound(cumsum(-ts) / sum, il / (layers + 1)) (src/llama-model.cpp); "
             "each card: budget %s (the smallest of them) - overhead %s = %s for its layers"
             % ("+".join(c["card"] for c in use), g1(budget), ovl, g1(max(room, 0)))]
    if on_host:
        lines.append("lookup tables %s stay in host RAM" % g1(on_host))
    found = _llama_search(m, kv, ladder, relaxed, want_seqs, k, room, n_cpus) if room > 0 else None
    if not found:
        why = ("weights + KV for %d-token sequences do not fit %d card(s) of %s each, layer by layer"
               % (ladder[-1], k, g1(budget)))
        return _res("llama.cpp", place, False, why, lines, gpus=k)
    c, n, n_cpu, ts = found
    wl, kl = llama_layer_bytes(m, kv, c, n, n_cpu)
    cards_b = [int(b + ov) for b in llama_cards(ts, [a + b for a, b in zip(wl, kl)])]
    lines += _llama_card_lines(use, ts, wl, kl, ov, m)
    if m["bytes"]["vision"]:
        lines.append("  (%s holds the vision projector, %s: tools/mtmd/clip.cpp loads it on the first GPU)"
                     % (use[0]["card"], g1(m["bytes"]["vision"])))
    kvused = per_seq(kv, c) * n
    lines.append("KV: %d seqs x %s at %d tokens = %s over the cards" % (n, _mb(per_seq(kv, c)), c, g1(kvused)))
    # llama-server's context checkpoints stay in host RAM whichever card holds the layers.
    ck = ckpt_seq(kv, c) * n
    hb = int(HOST_RUNTIME["llama.cpp"] + on_host + ck)
    toks = None
    if n_cpu:
        exp_layer = m["bytes"]["experts"] / max(m["layers"], 1)
        dram_w = exp_layer * n_cpu
        toks = _dram_rate(bw, dram_w * m["experts_per_tok"] / m["experts"])
        lines.append("experts of the first %d of %d layers in DRAM: %s" % (n_cpu, m["layers"], g1(dram_w)))
        lines.append("DRAM decode bound: %g GB/s / (%s x %d/%d read per token) = %.0f tok/s (floor %g)"
                     % (bw, g1(dram_w), m["experts_per_tok"], m["experts"], toks, MIN_DRAM_TOK_S))
        hb += int(dram_w)
        lines.append("host: experts %s + lookup tables %s + runtime %s + context checkpoints %d x %s = %s"
                     % (g1(dram_w), g1(on_host), g1(HOST_RUNTIME["llama.cpp"]), n, _mb(ckpt_seq(kv, c)), g1(hb)))
        if toks < MIN_DRAM_TOK_S:
            return _res("llama.cpp", place, False, "DRAM bandwidth gives %.1f tok/s, under the %g floor"
                        % (toks, MIN_DRAM_TOK_S), lines, gpus=k)
    elif ck:
        lines.append("context checkpoints: %d seqs x %s = %s in host RAM" % (n, _mb(ckpt_seq(kv, c)), g1(ck)))
    if hb > hfree:
        return _res("llama.cpp", place, False, "host side %s exceeds the host budget %s" % (g1(hb), g1(hfree)),
                    lines, gpus=k)
    return _res("llama.cpp", place, True, "", lines, ctx=c, seqs=n, gpus=k, gpu_bytes=max(cards_b), card_bytes=cards_b,
                host_bytes=hb, dram_tok_s=round(toks, 1) if toks else None, kv_bytes=int(kvused), ckpt_bytes=ck,
                n_cpu_moe=n_cpu, flags=_flags("llama.cpp", place, c, n, k, max(cards_b), use, kvused, m, n_cpu, kv, ts))


def _flags(eng, place, ctx, seqs, k, gpu_use, cards, kvused, m, n_cpu_moe, kv=None, ts=None):
    """Engine memory flags from the fit. Spellings from each engine's own argument parser:
    vLLM v0.32.0 vllm/engine/arg_utils.py, OVMS v2026.4.1 src/graph_export/graph_cli_parser.cpp,
    llama.cpp v0.8.0 (b11960) common/arg.cpp."""
    kv = kv or {}
    if eng == "vllm" and place == "cpu":
        return ["--max-model-len=%d" % ctx, "--max-num-seqs=%d" % seqs,
                "VLLM_CPU_KVCACHE_SPACE=%d" % max(int(math.ceil(kvused / GIB)), 1)]
    if eng == "vllm":
        total = min(c["total"] for c in cards)
        util = math.floor(gpu_use / total * 100) / 100
        out = ["--tensor-parallel-size=%d" % k, "--max-model-len=%d" % ctx, "--max-num-seqs=%d" % seqs,
               "--gpu-memory-utilization=%.2f" % max(util, 0.05)]
        if kv.get("dtype") in ("fp8", "fp16", "bf16"):
            # the KV element size the fit counted (vLLM --kv-cache-dtype: fp8, float16, bfloat16)
            out.append("--kv-cache-dtype=%s" % {"fp8": "fp8", "fp16": "float16", "bf16": "bfloat16"}[kv["dtype"]])
        return out
    if eng == "ovms":
        return ["--max_num_seqs=%d" % seqs, "--cache_size=%d" % max(int(kvused // GIB), 1)]
    # -c is the total context; with -np set explicitly each slot gets c/np (kv-unified is
    # on only when the slot count is auto).
    out = ["-c", str(ctx * seqs), "-np", str(seqs), "-ngl", "0" if place == "cpu" else "999"]
    if ts and k > 1:
        out += ["-ts", ",".join(str(t) for t in ts)]  # layers per card (llama_layer_devices)
    if place == "moe-offload":
        out += ["--cpu-moe"] if n_cpu_moe >= m["layers"] else ["--n-cpu-moe", str(n_cpu_moe)]
    # -cram/--cache-ram, -ctxcp/--ctx-checkpoints: the host caches the fit counts (LLAMA_CTX_CHECKPOINTS)
    out += ["--cache-ram", "0", "-ctxcp", str(LLAMA_CTX_CHECKPOINTS)]
    t = {"fp16": "f16"}.get(kv.get("dtype"), kv.get("dtype"))
    if t in ("f16", "bf16", "q8_0", "q4_0"):
        out += ["-ctk", t, "-ctv", t]  # -ctk/--cache-type-k, -ctv/--cache-type-v
    return out


def guard_rungs(ctx, seqs):
    """Ladder the guard walks down: halve sequences to 1, then halve context to the floor."""
    rungs, s, c = [(seqs, ctx)], seqs, ctx
    while s > 1:
        s = max(s // 2, 1)
        rungs.append((s, c))
    while c // 2 >= CTX_FLOOR:
        c //= 2
        rungs.append((1, c))
    return rungs


# ---- guard ----------------------------------------------------------------------------
def guard_step(guard, sample, state):
    """One decision from one sample. Pure.

    guard: the fit's guard block. sample: {"psi": psi(), "mem_available": bytes}.
    state: {"rung", "calm", "hot"} from the previous step ({} at start).
    Returns (action, new state, reason); action is hold | down | up | stop.

    A server whose KV cache lives in host RAM (placement cpu) gives memory back by
    stepping down the rung ladder (fewer sequences, then shorter context), one rung per
    sample under pressure, straight to the lowest rung in an emergency, back up one
    rung after calm_samples calm samples. A server whose KV cache is in VRAM frees no
    host RAM that way: its pod limit already bounds it, so pressure is only reported,
    and an emergency that lasts emergency_samples samples stops it. Either way, an
    emergency with nothing left to shed stops the server."""
    last = len(guard["rungs"]) - 1
    rung = min(max(int(state.get("rung", 0)), 0), last)
    calm, hot, cool = state.get("calm", 0), state.get("hot", 0), state.get("cool", 0)
    host_kv = guard.get("placement") == "cpu"
    # A VRAM-KV server is worth stopping only when it holds a real share of host RAM.
    sheddable = host_kv or guard.get("host_bytes", 0) >= guard["mem_available_floor"]
    cd = guard.get("cooldown_samples", 0)
    p = sample.get("psi") or {}
    some10 = (p.get("some") or {}).get("avg10", 0.0)
    some60 = (p.get("some") or {}).get("avg60", 0.0)
    full10 = (p.get("full") or {}).get("avg10", 0.0)
    avail = sample.get("mem_available")
    low = avail is not None and avail < guard["mem_available_floor"]
    emergency = full10 >= guard["full_avg10"] or low
    if cool > 0:
        # A step restarts the server: its own reload pressure is not acted on. An emergency
        # that persists emergency_samples samples through the cooldown still stops it.
        hot = hot + 1 if emergency else 0
        if emergency and sheddable and rung == last and hot >= guard["emergency_samples"]:
            return "stop", {"rung": rung, "calm": 0, "hot": hot, "stopped": True}, \
                "emergency for %d samples through the cooldown, at the lowest rung: stop the server" % hot
        return "hold", {"rung": rung, "calm": 0, "hot": hot, "cool": cool - 1}, \
            "cooling down after a step (%d sample(s) left)" % (cool - 1)
    if emergency:
        why = ("PSI full avg10 %.1f >= %.1f" % (full10, guard["full_avg10"]) if full10 >= guard["full_avg10"]
               else "MemAvailable %s < floor %s" % (g1(avail), g1(guard["mem_available_floor"])))
        hot += 1
        if host_kv and rung < last:
            return "down", {"rung": last, "calm": 0, "hot": hot, "cool": cd}, why + ": emergency, straight to the lowest rung"
        if not sheddable:
            return "hold", {"rung": rung, "calm": 0, "hot": hot}, \
                why + "; this server holds %s of host RAM, its pod limit bounds it" % g1(guard.get("host_bytes", 0))
        if hot >= guard["emergency_samples"] or host_kv:
            return "stop", {"rung": rung, "calm": 0, "hot": hot, "stopped": True}, \
                why + " for %d sample(s) with nothing left to shed: stop the server" % hot
        return "hold", {"rung": rung, "calm": 0, "hot": hot}, \
            why + " (%d/%d before stop; this server's KV is in VRAM)" % (hot, guard["emergency_samples"])
    if some10 >= guard["some_avg10"]:
        if not host_kv:
            return "hold", {"rung": rung, "calm": 0, "hot": 0}, \
                "PSI some avg10 %.1f: KV is in VRAM, the pod limit bounds this server" % some10
        if rung >= last:
            return "hold", {"rung": rung, "calm": 0, "hot": 0}, "PSI some avg10 %.1f, already at the lowest rung" % some10
        return "down", {"rung": rung + 1, "calm": 0, "hot": 0, "cool": cd}, \
            "PSI some avg10 %.1f >= %.1f: one rung down" % (some10, guard["some_avg10"])
    if some60 < guard["calm_some_avg60"]:
        calm += 1
        if rung > 0 and calm >= guard["calm_samples"]:
            return "up", {"rung": rung - 1, "calm": 0, "hot": 0, "cool": cd}, "calm for %d samples: one rung up" % calm
        return "hold", {"rung": rung, "calm": calm, "hot": 0}, "calm (%d/%d)" % (calm, guard["calm_samples"])
    return "hold", {"rung": rung, "calm": 0, "hot": 0}, "PSI some avg60 %.1f: holding" % some60


def knobs(doc, rung=0):
    """KEY=VALUE lines the launcher renders an agent from: rung 0 is the fit itself, a
    higher rung the guard's smaller fit (sequences, context, and for a KV cache in host
    RAM or an OVMS static pool, the KV size and pod limit with it)."""
    g = doc["guard"]
    rung = min(max(rung, 0), len(g["rungs"]) - 1)
    seqs, ctx = g["rungs"][rung]
    kv = g["rung_kv_bytes"][rung]
    fl = {f.split("=", 1)[0]: f.split("=", 1)[1] for f in doc["flags"] if "=" in f}
    # llama-server's flags at this rung: -c is the total context (context x slots), -np the slots.
    # Not -hf <repo>:<quant>, the fit's hint for running llama-server by hand: the agent
    # downloads the GGUF itself, pinned to the fit's commit, from "files" (the picked model's
    # shards). -hf beside that would fetch the repo's default branch as well and load it.
    lf = list(doc["flags"]) if doc["engine"] == "llama.cpp" else []
    while "-hf" in lf:
        i = lf.index("-hf")
        del lf[i:i + 2]
    for i in range(len(lf) - 1):
        if lf[i] == "-c":
            lf[i + 1] = str(ctx * seqs)
        elif lf[i] == "-np":
            lf[i + 1] = str(seqs)
    conv = doc.get("convert") or {}
    out = {"engine": doc["engine"], "placement": doc["placement"], "ctx": ctx, "seqs": seqs, "tp": doc["tp"],
           "gpus": doc.get("gpus") or 0, "util": fl.get("--gpu-memory-utilization", ""),
           "kv_dtype": fl.get("--kv-cache-dtype", ""), "llama_flags": " ".join(lf),
           "source": doc.get("source") or "", "files": ",".join(doc.get("files") or []),
           "weight_bytes": doc.get("weight_bytes") or 0,
           "convert": " ".join(conv.get("args") or []), "convert_error": conv.get("error") or "",
           "cache_gib": max(int(kv // GIB), 1), "memory": g["rung_memory_limit"][rung],
           "annotation": "%s %dx%d" % (doc["placement"], seqs, ctx), "summary": doc["summary"]}
    return "".join("%s=%s\n" % (k, out[k]) for k in sorted(out))


# ---- CLI --------------------------------------------------------------------------------
def load_model_dir(d):
    """(config, weights/v1, tokenizer_config or None) of a model directory: fit.py fetch
    output, or a local checkout whose weight headers are read in place."""
    with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    wpath = os.path.join(d, "weights.json")
    if os.path.exists(wpath):
        with open(wpath, encoding="utf-8") as f:
            w = json.load(f)
    else:
        w = weights_local(d)
    tok = None
    tpath = os.path.join(d, "tokenizer_config.json")
    if os.path.exists(tpath):
        with open(tpath, encoding="utf-8") as f:
            tok = json.load(f)
    return cfg, w, tok


def fit_groups(host, cfg, w, name, tok, file=None, **kw):
    """fit() of a GGUF repo's models: the one --file names, else each in group_order until
    one fits the target (4 seqs at the good context), else the first that fits at all."""
    groups = w.get("groups") or {}
    if not groups:
        if file:
            raise ValueError("--file %s: %s is not a GGUF repo with several models" % (file, name))
        return fit(host, cfg, w, name=name, tokenizer_cfg=tok, **kw)
    listing = {g: [f["name"] for f in doc["files"]] for g, doc in groups.items()}
    order = [pick_group(listing, file)] if file else group_order(listing)
    tried, first_fit = [], None
    for g in order:
        d = fit(host, cfg, groups[g], name="%s:%s" % (name, g), tokenizer_cfg=tok, **kw)
        d["gguf_group"] = g
        d["gguf_files"] = listing[g]
        if d["fits"]:
            # llama-server -hf <user>/<model>[:quant] fetches every shard of that quant (common/arg.cpp)
            d["flags"] = ["-hf", "%s:%s" % (name.split("@")[0], g)] + d["flags"]
            d["explain"] = [("engine flags: " + " ".join(d["flags"])) if x.startswith("engine flags:") else x
                            for x in d["explain"]]
        tried.append("GGUF %s (%s, %s): %s" % (g, g1(groups[g]["total_bytes"]), "--file" if file else "auto",
                                              "fits" + (" below the target" if d.get("degraded") else "")
                                              if d["fits"] else "no: " + d.get("reason", "")))
        if d["fits"] and not d.get("degraded"):
            d["explain"] = ["GGUF models in the repo, tried in order: " + ", ".join(order)] + tried + d["explain"]
            return d
        if d["fits"] and first_fit is None:
            first_fit = d
        last = d
    best = first_fit or last
    best["explain"] = ["GGUF models in the repo, tried in order: " + ", ".join(order)] + tried + best["explain"]
    return best


def plan_with_variants(host, model_dir, variants, file=None, **kw):
    """fit() for one model directory (a GGUF repo: its best-fitting model, or the one
    file names); when it does not fit, also for each variant directory, naming the
    smallest variant (by bytes to serve) that does."""
    cfg, w, tok = load_model_dir(model_dir)
    name = kw.pop("name", None) or (w.get("source") or os.path.basename(model_dir.rstrip("/"))).split(":")[0]
    doc = fit_groups(host, cfg, w, name, tok, file=file, **kw)
    if (doc["fits"] and not doc["degraded"]) or not variants:
        return doc
    ok = []
    for v in variants:
        if os.path.abspath(v) == os.path.abspath(model_dir):
            continue
        vc, vw, vt = load_model_dir(v)
        vkw = dict(kw, engine=None)
        d = fit_groups(host, vc, vw, (vw.get("source") or os.path.basename(v.rstrip("/"))).split(":")[0], vt, **vkw)
        if d["fits"] and not d["degraded"]:
            ok.append((model_profile(vc, vw, vt)["serve_bytes"], d["model"], d))
    if ok:
        ok.sort(key=lambda x: (x[0], x[1]))
        best = ok[0][2]
        doc["smallest_fit"] = best["model"]
        doc["summary"] += "; smallest variant that fits the target: " + best["summary"][len("fit: "):]
        doc["explain"].append("smallest variant that fits the target: " + best["summary"])
    elif not doc["fits"]:
        doc["summary"] += "; no listed variant fits either"
    return doc


def main(argv):
    ap = argparse.ArgumentParser(prog="fit.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--sysroot", default="/")
    p.add_argument("--stream")
    p.add_argument("--node-limit", type=int)
    f = sub.add_parser("fetch")
    f.add_argument("repo")
    f.add_argument("--revision")
    f.add_argument("--file")
    f.add_argument("--out", required=True)
    w = sub.add_parser("weights")
    w.add_argument("dir")
    pl = sub.add_parser("plan")
    pl.add_argument("--host", required=True)
    pl.add_argument("--model", required=True)
    pl.add_argument("--name")
    pl.add_argument("--variants", nargs="*", default=[])
    pl.add_argument("--engine", choices=("vllm", "ovms", "llama.cpp"))
    pl.add_argument("--ctx", type=int)
    pl.add_argument("--seqs", type=int)
    pl.add_argument("--gpus", type=int)
    pl.add_argument("--kv-dtype", default="auto", choices=sorted(KV_DTYPE_BYTES))
    pl.add_argument("--device", choices=("cpu", "gpu"))
    pl.add_argument("--file", help="the GGUF model (a file, shard or quant label) of a repo with several")
    kn = sub.add_parser("knobs")
    kn.add_argument("--fit", required=True)
    kn.add_argument("--rung", type=int, default=0)
    pl.add_argument("--mode", choices=("text", "explain", "json"), default="text")
    bc = sub.add_parser("busy-cards")
    bc.add_argument("--host", required=True)
    g = sub.add_parser("guard-step")
    g.add_argument("--fit", required=True)
    g.add_argument("--state", required=True)
    g.add_argument("--sysroot", default="/")
    a = ap.parse_args(argv[1:])
    try:
        if a.cmd == "probe":
            doc = probe(a.sysroot, a.stream, node_limit=a.node_limit)
        elif a.cmd == "fetch":
            doc = hf_fetch(a.repo, a.revision, a.out, a.file, os.environ.get("HF_TOKEN"))
        elif a.cmd == "weights":
            doc = weights_local(a.dir)
        elif a.cmd == "plan":
            with open(a.host, encoding="utf-8") as fh:
                host = json.load(fh)
            ov = os.environ.get("K3D_GPU_FIT_OVERHEAD_GIB")
            doc = plan_with_variants(host, a.model, a.variants, name=a.name, engine=a.engine, ctx=a.ctx,
                                     seqs=a.seqs, gpus=a.gpus, kv_dtype=a.kv_dtype, device=a.device, file=a.file,
                                     overhead=float(ov) * GIB if ov else None)
            if a.mode != "json":
                lines = doc["explain"] + [doc["summary"]] if a.mode == "explain" else [doc["summary"]]
                sys.stdout.write("\n".join(lines) + "\n")
                return 0 if doc["fits"] else 3
        elif a.cmd == "busy-cards":
            with open(a.host, encoding="utf-8") as fh:
                sys.stdout.write("".join("%s\t%s\t%s\n" % b for b in busy_cards(json.load(fh))))
            return 0
        elif a.cmd == "knobs":
            with open(a.fit, encoding="utf-8") as fh:
                sys.stdout.write(knobs(json.load(fh), a.rung))
            return 0
        else:
            with open(a.fit, encoding="utf-8") as fh:
                fdoc = json.load(fh)
            try:
                with open(a.state, encoding="utf-8") as fh:
                    st = json.load(fh)
            except (OSError, ValueError):
                st = {}
            mi = meminfo(a.sysroot)
            action, st, why = guard_step(fdoc["guard"], {"psi": psi(a.sysroot), "mem_available": mi.get("MemAvailable")}, st)
            with open(a.state + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(st, fh)
            os.replace(a.state + ".tmp", a.state)
            s, c = fdoc["guard"]["rungs"][st["rung"]]
            doc = {"action": action, "rung": st["rung"], "seqs": s, "ctx": c, "reason": why}
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, struct.error) as e:
        sys.stderr.write("fit: %s: %s\n" % (type(e).__name__, e))
        return 2
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0 if doc.get("fits", True) else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))

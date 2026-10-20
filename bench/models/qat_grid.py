#!/usr/bin/env python3
"""qat_grid.py: how close an OpenVINO INT4 IR's weights sit to a QAT checkpoint's grid, and a fix.

A Q4_0 QAT checkpoint (e.g. google/gemma-4-26B-A4B-it-qat-q4_0-unquantized) stores bf16 weights
that already lie on a symmetric 4-bit grid: every 32-weight block along the input dimension holds
integer multiples k * d of one step d, k in [-8, 7] (model-cards/REFERENCE.md 3.2). An INT4_SYM
group-32 export keeps that benefit only if each block's scale is that step; an absmax-based scale
(absmax / 8) re-rounds every value. Crescent amendment M1.

  qat_grid.py check IR.xml QAT_DIR [--out JSON] [--limit N]
      for every INT4 weight in the IR whose name matches a QAT tensor of the same size, the
      rel-RMS error of the IR's dequantized weight against the QAT weight, summarised per kind
      (attention, experts, other); writes JSON
  qat_grid.py snap IR.xml QAT_DIR OUT.xml
      rewrites each matched INT4 weight (its int4 values and its scale) from the QAT weight with
      scale = the block's grid step, and saves the IR to OUT.xml (other constants unchanged)

The IR side needs the openvino Python package (the export venv); the checkpoint side reads
safetensors with numpy only. Weight layout: OpenVINO's compressed weight is [out, groups, 32]
(or [experts, out, groups, 32]) with a scale [.., groups, 1]; the QAT tensor is compared in the
same block order, input dimension last, transposed when that is the layout that matches.
"""
import argparse
import json
import os
import struct
import sys

import numpy as np

GROUP = 32


# ---- grid arithmetic (numpy only; tested) -------------------------------------------------

def blocks(w, group=GROUP):
    """[..., in] -> [n, group] blocks along the input (last) dimension."""
    if w.shape[-1] % group:
        raise ValueError(f"last dim {w.shape[-1]} is not a multiple of {group}")
    return w.reshape(-1, group)


def grid_step(b):
    """Per-block grid step d. A QAT block holds k * d with k in [-8, 7], so its largest |value| is
    m * d for some m in 1..8: d is the candidate amax / m whose block fits integer multiples best
    (ties go to the larger d). 1 for an all-zero block."""
    b = np.asarray(b, dtype=np.float64)
    amax = np.abs(b).max(axis=1)
    best_d = np.ones(len(b))
    best_e = np.full(len(b), np.inf)
    for m in range(1, 9):
        d = np.where(amax > 0, amax / m, 1.0)
        r = b / d[:, None]
        e = np.abs(r - np.rint(r)).mean(axis=1)
        better = e < best_e - 1e-9
        best_d[better], best_e[better] = d[better], e[better]
    return best_d


def snap_blocks(b):
    """(q int8 in [-8, 7], scale fp16) with scale = the grid step rounded to fp16."""
    d = grid_step(b).astype(np.float16).astype(np.float64)
    d[d == 0] = np.float64(np.float16(np.finfo(np.float16).tiny))
    q = np.clip(np.rint(b / d[:, None]), -8, 7).astype(np.int8)
    return q, d.astype(np.float16)


def rel_rms(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    den = np.sqrt((b ** 2).mean())
    return float(np.sqrt(((a - b) ** 2).mean()) / den) if den else 0.0


def kind(name):
    n = name.lower()
    if "expert" in n:
        return "experts"
    if any(k in n for k in ("q_proj", "k_proj", "v_proj", "o_proj", "self_attn")):
        return "attention"
    return "other"


# ---- safetensors (numpy only) ---------------------------------------------------------------

def st_index(qat_dir):
    """{tensor name: (file, header entry)} over every *.safetensors file."""
    idx = {}
    for f in sorted(os.listdir(qat_dir)):
        if not f.endswith(".safetensors"):
            continue
        p = os.path.join(qat_dir, f)
        with open(p, "rb") as fh:
            n = struct.unpack("<Q", fh.read(8))[0]
            hdr = json.loads(fh.read(n))
        for k, v in hdr.items():
            if k != "__metadata__":
                idx[k] = (p, v, 8 + n)
    return idx


def st_read(entry):
    p, v, base = entry
    a, b = v["data_offsets"]
    with open(p, "rb") as fh:
        fh.seek(base + a)
        raw = fh.read(b - a)
    if v["dtype"] == "BF16":
        x = (np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16).view(np.float32)
    elif v["dtype"] == "F16":
        x = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    elif v["dtype"] == "F32":
        x = np.frombuffer(raw, dtype=np.float32)
    else:
        raise ValueError(f"dtype {v['dtype']}")
    return x.reshape(v["shape"])


def match_name(ir_name, names):
    """The checkpoint tensor whose name is the longest suffix match of an IR constant name."""
    s = ir_name
    for pre in ("self.", "__module.", "model."):
        if s.startswith(pre):
            s = s[len(pre):]
    s = s.split(":")[0]
    best = None
    for n in names:
        if n.endswith(s) or s.endswith(n) or n.replace("model.", "", 1).endswith(s):
            if best is None or len(n) < len(best):
                best = n
    return best


def as_blocks_like(qat, numel_ir):
    """The QAT tensor in the IR's block order: input dim last as stored, else transposed."""
    if qat.size != numel_ir:
        return None
    yield_list = [qat]
    if qat.ndim >= 2:
        yield_list.append(np.swapaxes(qat, -1, -2))
    return yield_list


# ---- OpenVINO side ---------------------------------------------------------------------------

def compressed_weights(model):
    """[(name, int4 Constant node, scale Constant node, Multiply node)] for NNCF's pattern
    Constant(i4/u4) -> Convert -> [Subtract zp] -> Multiply(scale)."""
    out = []
    for op in model.get_ordered_ops():
        if op.get_type_name() != "Multiply":
            continue
        ins = [op.input_value(i).get_node() for i in range(2)]
        conv = next((x for x in ins if x.get_type_name() == "Convert"), None)
        sc = next((x for x in ins if x.get_type_name() == "Constant"), None)
        if conv is None or sc is None:
            continue
        src = conv.input_value(0).get_node()
        if src.get_type_name() != "Constant" or src.get_element_type().get_type_name() not in ("i4", "u4"):
            continue
        out.append((src.get_friendly_name(), src, sc, op))
    return out


def dequant(op):
    """The Multiply's output as a numpy array (a constant subgraph, folded on the CPU)."""
    import openvino as ov
    m = ov.Model([op.output(0)], [], "w")
    return ov.Core().compile_model(m, "CPU")({})[0]


def cmd_check(a):
    import openvino as ov
    model = ov.Core().read_model(a.ir)
    idx = st_index(a.qat)
    rows, skipped = [], 0
    for name, _c, _s, op in compressed_weights(model)[: a.limit or None]:
        qn = match_name(name, idx)
        if not qn:
            skipped += 1
            continue
        w_ir = np.asarray(dequant(op), dtype=np.float32)
        qat = st_read(idx[qn])
        cands = as_blocks_like(qat, w_ir.size)
        if not cands:
            skipped += 1
            continue
        err = min(rel_rms(w_ir.reshape(-1), c.reshape(-1)) for c in cands)
        rows.append({"ir": name, "qat": qn, "kind": kind(qn), "rel_rms": round(err, 5)})
    summary = {}
    for k in ("attention", "experts", "other"):
        es = sorted(r["rel_rms"] for r in rows if r["kind"] == k)
        if es:
            summary[k] = {"n": len(es), "median": es[len(es) // 2], "max": es[-1]}
    res = {"ir": a.ir, "qat": a.qat, "matched": len(rows), "skipped": skipped, "summary": summary, "tensors": rows}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)
    print(json.dumps({k: res[k] for k in ("matched", "skipped", "summary")}, indent=1))
    return 0


def cmd_snap(a):
    import openvino as ov
    model = ov.Core().read_model(a.ir)
    idx = st_index(a.qat)
    done, skipped = 0, 0
    for name, c, sc, op in compressed_weights(model):
        qn = match_name(name, idx)
        shape = list(c.get_output_shape(0))
        if not qn or c.get_element_type().get_type_name() != "i4":     # symmetric (i4, no zero point) only
            skipped += 1
            continue
        cands = as_blocks_like(st_read(idx[qn]), int(np.prod(shape)))
        if not cands:
            skipped += 1
            continue
        w_ir = np.asarray(dequant(op), dtype=np.float32).reshape(-1)
        best = min(cands, key=lambda x: rel_rms(w_ir, x.reshape(-1)))
        q, d = snap_blocks(blocks(best.astype(np.float64)))
        sshape = list(sc.get_output_shape(0))
        newc = ov.opset13.constant(q.reshape(shape), dtype=ov.Type.i4)
        news = ov.opset13.constant(d.reshape(sshape).astype(np.float16), dtype=sc.get_element_type())
        newc.set_friendly_name(name)
        news.set_friendly_name(sc.get_friendly_name())
        c.output(0).replace(newc.output(0))
        sc.output(0).replace(news.output(0))
        done += 1
    ov.save_model(model, a.out, compress_to_fp16=False)
    print(json.dumps({"snapped": done, "skipped": skipped, "out": a.out}))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("ir")
    c.add_argument("qat")
    c.add_argument("--out")
    c.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("snap")
    s.add_argument("ir")
    s.add_argument("qat")
    s.add_argument("out")
    a = p.parse_args(argv)
    return cmd_check(a) if a.cmd == "check" else cmd_snap(a)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""validate.py: capacity-oracle bundle rules V1-V12 (docs/moat-design.md section 3).

The same code runs in `k3d-gpu share` before anything is shown as submittable,
in this repo's CI over oracle/seed, and in the oracle repo's ingest. Results
are recomputed here from the raw samples with bench/apb/apb.py, the scorer
`k3d-gpu capacity` itself uses; a claimed number is never published.

Verdicts, worst wins: reject > quarantine > accept. Tags (throttled, drifted,
unreleased-build, ab-unpaired) ride along with accept and are shown in the
matrix.

  validate.py [--trusted] [--pin VERSION=IMAGE]... [--known DIR] BUNDLE...

Exit 0 when no bundle is rejected; 1 otherwise. Prints one JSON line per
bundle: {path, verdict, tags, reasons, recomputed}.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle as B  # noqa: E402


def _import_apb():
    for d in (os.environ.get("K3D_GPU_APB_DIR"), os.path.join(REPO, "bench", "apb")):
        if d and os.path.isfile(os.path.join(d, "apb.py")):
            sys.path.insert(0, d)
            # apb.py imports agent_trace from its parent dir.
            sys.path.insert(0, os.path.dirname(d))
            import apb
            return apb
    raise ImportError("bench/apb/apb.py not found (set K3D_GPU_APB_DIR)")


apb = _import_apb()

RANK = {"accept": 0, "quarantine": 1, "reject": 2}
LITTLE_TOL = 0.25        # V6 relative tolerance
CEILING_SLACK = 1.25     # V7: agents not busy every instant of the window
ROOFLINE = 1.15          # V8
ENERGY_TOL = 0.10        # V9
DRIFT_MAX = 1.25         # V10
MONO_DROP = 0.5          # V11
# V8 without a roofline (memory channels, speed or weight size unknown): one
# decode stream above this many tokens/s goes to review. Shared-DRAM engines
# top out near 845 GB/s per socket (Xeon 6: 12 channels x 8800 MT/s x 8 B),
# HBM accelerators near 8 TB/s; a 1 GB int4 model then reads at most ~845 and
# ~8000 tokens/s, and real single-stream decode stays well under both.
STREAM_TOK_S_MAX = {"cpu": 1000.0, "igpu": 1000.0, "npu": 1000.0, "gaudi": 8000.0, "nvidia": 8000.0}

CANARIES = B.CANARIES  # shared with project.py, which drops matching values


class Result:
    def __init__(self):
        self.verdict, self.tags, self.reasons, self.recomputed = "accept", [], [], {}

    def flag(self, verdict, rule, msg):
        if RANK[verdict] > RANK[self.verdict]:
            self.verdict = verdict
        self.reasons.append(f"{rule} {verdict}: {msg}")

    def tag(self, t):
        if t not in self.tags:
            self.tags.append(t)

    def as_dict(self):
        return {"verdict": self.verdict, "tags": self.tags, "reasons": self.reasons, "recomputed": self.recomputed}


# ---------------------------------------------------------------- helpers
def strings(x, path="$"):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield f"{path}.<key>", k
            yield from strings(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from strings(v, f"{path}[{i}]")


_VOCAB = None


def schema_vocabulary():
    """Every property name, enum value and const string of the bundle schema:
    words the projection itself writes, so they identify no machine."""
    global _VOCAB
    if _VOCAB is None:
        words = set()

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "properties" and isinstance(v, dict):
                        words.update(str(n).lower() for n in v)
                    elif k == "enum" and isinstance(v, list):
                        words.update(str(e).lower() for e in v if isinstance(e, str))
                    elif k == "const" and isinstance(v, str):
                        words.add(v.lower())
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(B.load_schema())
        _VOCAB = frozenset(words)
    return _VOCAB


BRAND_PATH = "$.hardware.cpu.model"


def _token_rx(ident):
    # Bounded by anything that is not a letter, digit or underscore.
    return re.compile(r"(?<![a-z0-9_])" + re.escape(ident) + r"(?![a-z0-9_])")


def identifier_hits(b, local_identifiers=()):
    """(reject, review) messages for this machine's identifiers in the bundle.

    Keys are skipped (the schema owns them), an identifier that is a word of
    the schema vocabulary is skipped, and an identifier matches on token
    boundaries only. A plain word found only in the vendor CPU brand string
    (`Core` for the login `core`) quarantines for review; anything else rejects."""
    vocab = schema_vocabulary()
    idents = sorted({s.strip().lower() for s in local_identifiers
                     if isinstance(s, str) and len(s.strip()) >= 4} - vocab)
    rxs = [(i, _token_rx(i)) for i in idents]
    reject, review = [], []
    for path, s in strings(b):
        if path.endswith(".<key>") or path.endswith(SKIP_SUFFIXES):
            continue
        low = s.lower()
        for ident, rx in rxs:
            if not rx.search(low):
                continue
            if path == BRAND_PATH and re.fullmatch(r"[a-z]+", ident):
                review.append(f"{path} (the CPU brand string) contains the word {ident!r}, "
                              "which is also an identifier of this machine")
            else:
                reject.append(f"{path} contains an identifier of this machine")
    return reject, review


SKIP_SUFFIXES = ("content_sha256", "comparison_key", "weights_sha256", "revision")


def canary_hits(b, local_identifiers=()):
    hits = []
    for path, s in strings(b):
        if path.endswith(SKIP_SUFFIXES):
            continue
        for name, rx in CANARIES:
            if rx.search(s):
                hits.append(f"{path} looks like {name}")
    return hits + identifier_hits(b, local_identifiers)[0]


def columns_ok(cols):
    lens = {len(v) for v in cols.values()}
    return len(lens) == 1


def rows_of(rep):
    out = []
    for i in range(len(rep["session"])):
        e2e, ttft = rep["e2e_us"][i], rep["ttft_us"][i]
        out.append({"session": rep["session"][i], "sent_at_s": rep["sent_us"][i] / 1e6,
                    "e2e_s": None if e2e is None else e2e / 1e6,
                    "ttft_s": None if ttft is None else ttft / 1e6,
                    "completion_tokens": rep["out_tok"][i], "error": "error" if rep["err"][i] else None,
                    "expect_tool": bool(rep["expect_tool"][i]), "tool_valid": bool(rep["tool_valid"][i])})
    return out


def think_mean(family, agents, reps, time_scale):
    """Mean tool wait per turn (s) the replayer was told to take: capacity seeds
    rep r of step N with 1000 * N + r, so the trace is regenerated exactly."""
    waits = []
    for r in range(1, reps + 1):
        tr = apb.build_trace(family, agents, 1000 * agents + r)
        for sess in tr["sessions"]:
            waits += [(t.get("think_s") or 0.0) if i > 0 else 0.0 for i, t in enumerate(sess["turns"])]
    return (statistics.mean(waits) if waits else 0.0) * time_scale


def max_tokens_of(family, agents, rep):
    """Largest max_tokens the replayer sent in rep `rep` of step N (same seed as think_mean)."""
    tr = apb.build_trace(family, agents, 1000 * agents + rep)
    return max(t.get("max_tokens", 128) for sess in tr["sessions"] for t in sess["turns"])


def to_us(v):
    return None if v is None or v == math.inf else int(round(v * 1e6))


# ---------------------------------------------------------------- V4
# Old releases: AGENT_IMAGE="${K3D_GPU_AGENT_IMAGE:-<image>}". Newer releases pin
# one digest per backend: AGENT_OVMS_IMAGE="<image>" and AGENT_DEFAULT_IMAGE="<image>".
AGENT_IMAGE_RE = re.compile(
    r'^\s*(?:AGENT_IMAGE="\$\{K3D_GPU_AGENT_IMAGE:-([^}$]+)\}"'
    r'|AGENT_(?:OVMS|DEFAULT)_IMAGE="([^"$]+@sha256:[0-9a-f]{64})")', re.M)


def script_pins(text):
    """Every server image a k3d-gpu script pins by digest."""
    return {a or b for a, b in AGENT_IMAGE_RE.findall(text or "")}


def release_pin(version, repo=REPO):
    """Server images pinned in scripts/k3d-gpu at release tag aur-vVERSION, from
    git; None when this clone has no such tag."""
    try:
        out = subprocess.run(["git", "-C", repo, "show", f"aur-v{version}:scripts/k3d-gpu"],
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    pins = script_pins(out.stdout) if out.returncode == 0 else set()
    return pins or None


def as_pin_set(pin):
    if pin is None:
        return None
    return {pin} if isinstance(pin, str) else set(pin)


def check_release(b, res, trusted, pins, repo):
    """V4. A release (x.y.z) must pin the bundle's server image at its tag. An
    unreleased build (git-<sha>) is only taken from the maintainers' lab and is
    tagged as such: no release tag, no build attestation to check."""
    t, maint = b["tool"], b["provenance"]["source"] == "maintainer-lab"
    if t["version"].startswith("git-"):
        if maint and trusted:
            res.tag("unreleased-build")
        else:
            res.flag("quarantine", "V4", f"tool version {t['version']} is not a release")
        return
    pin = as_pin_set((pins or {}).get(t["version"])) or release_pin(t["version"], repo)
    if t["server_image"] is None:
        res.flag("quarantine", "V4", "no server image digest")
    elif pin is None:
        res.flag("quarantine", "V4", f"release {t['version']} and the image it pins are not known here")
    elif t["server_image"] not in pin:
        res.flag("quarantine", "V4", f"server image differs from the one {t['version']} pins")


# ---------------------------------------------------------------- V5-V11 capacity
def recompute_capacity(b, res, trusted=False):
    w, c = b["workload"], b["results"]["capacity"]
    slo, stop = w["slo_s"], w["stop_slo_s"] or w["slo_s"]
    if slo is None:
        res.flag("reject", "V5", "capacity bundle without slo_s")
        return
    steps, base_tool, prev_agents = [], None, 0
    for s in c["steps"]:
        if s["agents"] <= prev_agents:
            res.flag("reject", "V5", "steps are not in increasing agent order")
            return
        prev_agents = s["agents"]
        if not all(columns_ok(r) for r in s["reps"]):
            res.flag("reject", "V1", f"N={s['agents']}: sample columns differ in length")
            return
        if w["trace"]:
            for i, r in enumerate(s["reps"], 1):
                cap_tok = max_tokens_of(w["trace"], s["agents"], i)
                if any(t is not None and t > cap_tok for t in r["out_tok"]):
                    res.flag("reject", "V5", f"N={s['agents']} rep {i}: a reply has more tokens than the "
                             f"trace's max_tokens {cap_tok}")
                    return
        reps = [(rows_of(r), {"agents": s["agents"]}) for r in s["reps"]]
        ramp = apb.score_step(reps, stop, base_tool)
        early = any(apb.early_fail(rows, meta, stop) for rows, meta in reps)
        if early and ramp["verdict"] != "pass":
            ramp["verdict"] = "fail"
        if base_tool is None:
            base_tool = ramp["tool_valid_any"]
        final = apb.score_step(reps, slo, base_tool)
        if ramp["verdict"] == "fail" and final["verdict"] != "fail":
            final["verdict"] = "fail"
        final["_slo"] = slo
        final["_reps"] = reps
        final["_claim"] = s
        steps.append(final)
    got, _interp = apb.apb(steps)
    rc = {"agents_per_box": got, "steps": [{"agents": s["agents"], "verdict": s["verdict"], "n": s["n"],
                                            "p95_us": to_us(s["p95_s"]),
                                            "window_out_tok_s": s["window_output_tok_per_s"]} for s in steps]}
    res.recomputed.update(rc)

    # V5: claims must reproduce.
    for s in steps:
        cl = s["_claim"]
        want = to_us(s["p95_s"])
        if cl["claimed_verdict"] != s["verdict"]:
            res.flag("reject", "V5", f"N={s['agents']}: claimed verdict {cl['claimed_verdict']}, samples give {s['verdict']}")
        if (want is None) != (cl["claimed_p95_us"] is None) or (
                want is not None and abs(want - cl["claimed_p95_us"]) > max(1, 0.001 * want)):
            res.flag("reject", "V5", f"N={s['agents']}: claimed p95 {cl['claimed_p95_us']} us, samples give {want} us")
    if c["agents_per_box"] != got:
        res.flag("reject", "V5", f"claimed {c['agents_per_box']} agents per box, samples give {got}")

    # V6 Little's law: N closed-loop agents, each turn = reply + the trace's tool
    # wait, so window throughput ~ N x mean output / (mean reply + mean wait).
    # V7 ceiling: at the SLO a turn lasts at most slo + wait, so no box serves
    # more than peak throughput x (slo + wait) / mean output agents.
    scale = 1.0 if w["time_scale"] is None else w["time_scale"]
    peak, apb_step = 0.0, None
    for s in steps:
        ok = [r for rr, meta in s["_reps"] for r in apb.window_requests(rr, meta["agents"])[0]
              if not r["error"] and r["e2e_s"] is not None]
        x = s["window_output_tok_per_s"]
        s["_think"] = think_mean(w["trace"], s["agents"], len(s["_reps"]), scale) if w["trace"] else 0.0
        s["_out_mean"] = statistics.mean([r["completion_tokens"] or 0 for r in ok]) if ok else None
        if x:
            peak = max(peak, x)
        if ok and x and s["_out_mean"]:
            cycle = statistics.mean([r["e2e_s"] for r in ok]) + s["_think"]
            little = s["agents"] * s["_out_mean"] / cycle if cycle > 0 else None
            if little and abs(x - little) / little > LITTLE_TOL:
                res.flag("quarantine", "V6", f"N={s['agents']}: window {x:.1f} tok/s vs Little's law {little:.1f}")
        if s["agents"] == got:
            apb_step = s
    if apb_step is not None and apb_step["_out_mean"] and peak > 0:
        ceiling = math.ceil(CEILING_SLACK * peak * (slo + apb_step["_think"]) / apb_step["_out_mean"])
        res.recomputed["apb_ceiling"] = ceiling
        if got > ceiling:
            res.flag("reject", "V7", f"{got} agents per box exceeds the throughput ceiling {ceiling}")

    # V9 energy sanity.
    pl1, pl2 = b["power"]["pl1_w"], b["power"]["pl2_w"]
    cap_w = max([v for v in (pl2, 2 * pl1 if pl1 else None) if v] or [0]) or None
    for s in steps:
        cl = s["_claim"]
        pw, jt = cl["pkg_w_mean"], cl["j_per_turn_pkg"]
        turns = sum(len(r["session"]) for r in cl["reps"])
        if cl["energy_turns"] is not None and cl["energy_turns"] != turns:
            res.flag("quarantine", "V9", f"N={s['agents']}: energy counts {cl['energy_turns']} turns, samples hold {turns}")
        if pw is not None and (pw <= 0 or (cap_w and pw > cap_w)):
            res.flag("quarantine", "V9", f"N={s['agents']}: package {pw:.1f} W outside (0, {cap_w}]")
        if None not in (pw, jt, cl["energy_seconds"]) and cl["energy_turns"]:
            want = pw * cl["energy_seconds"] / cl["energy_turns"]
            if want > 0 and abs(jt - want) / want > ENERGY_TOL:
                res.flag("quarantine", "V9", f"N={s['agents']}: {jt:.1f} J/turn vs {want:.1f} from power x time / turns")

    # V10 box state. A maintainer-lab run on a busy box is kept with the
    # not-quiet tag: its ledger row records the load it ran under.
    if not c["quiet_check_ok"]:
        if trusted and b["provenance"]["source"] == "maintainer-lab":
            res.tag("not-quiet")
        else:
            res.flag("quarantine", "V10", "box was not quiet when the run started")
    # The binding step is the smallest N that broke the SLO (the search may
    # visit N out of order), else the largest N run.
    failed = [s for s in steps if s["verdict"] != "pass"]
    bind = (min(failed, key=lambda s: s["agents"]) if failed
            else max(steps, key=lambda s: s["agents"]) if steps else None)
    if bind is not None and (bind["_claim"]["throttle_events"] or 0) > 0:
        res.tag("throttled")
    if c["drift_ratio"] is not None and c["drift_ratio"] > DRIFT_MAX:
        res.tag("drifted")

    # V11 monotone sanity: p95 at 2N must not fall below half of p95 at N.
    p95 = {s["agents"]: s["p95_s"] for s in steps if s["p95_s"] not in (None, math.inf)}
    for n, v in p95.items():
        if 2 * n in p95 and v > 0 and p95[2 * n] < MONO_DROP * v:
            res.flag("quarantine", "V11", f"p95 at N={2 * n} is under half of N={n}: spliced runs?")


# ---------------------------------------------------------------- single stream
def single_stream_metrics(b, res):
    ss = b["results"]["single_stream"]
    for k in ("decode", "prefill"):
        if not columns_ok(ss[k]):
            res.flag("reject", "V1", f"{k}: sample columns differ in length")
            return
    d, p = ss["decode"], ss["prefill"]
    shape = b["workload"]["single_stream"]
    for k, cols in (("decode", d), ("prefill", p)):
        if any(t is not None and t > shape[k]["max_tokens"] for t in cols["out_tok"]):
            res.flag("reject", "V5", f"{k}: a reply has more tokens than max_tokens {shape[k]['max_tokens']}")
            return
    dtps, rounds = [], []
    for e2e, ttft, out, rnd in zip(d["e2e_us"], d["ttft_us"], d["out_tok"], d["round"]):
        if None in (e2e, ttft, out) or out < 2 or e2e <= ttft:
            res.flag("reject", "V5", "decode sample without a positive decode time")
            return
        dtps.append((out - 1) / ((e2e - ttft) / 1e6))
        rounds.append(rnd)
    ttft = [v / 1e6 if v is not None else None for v in p["ttft_us"]]
    med = _rounds_median
    # Median over rounds of each round's median, as the H6 ledger table is derived.
    res.recomputed.update({
        "decode_tok_s": med(dtps, rounds),
        "decode_ttft_s": med([v / 1e6 if v is not None else None for v in d["ttft_us"]], d["round"]),
        "prefill_ttft_s": med(ttft, p["round"]),
        "n_decode": len(dtps), "n_prefill": sum(1 for v in ttft if v),
    })


def _rounds_median(values, rounds):
    by = {}
    for v, r in zip(values, rounds):
        if v is not None and isinstance(r, int) and not isinstance(r, bool):
            by.setdefault(r, []).append(v)
    return statistics.median(statistics.median(v) for _r, v in sorted(by.items())) if by else None


def roofline(b, res):
    """V8: single-stream decode cannot beat memory bandwidth / weight bytes."""
    hw, m = b["hardware"]["memory"], b["workload"]["model"]
    spec = any(lv["id"] == "spec_decode" and lv["state"] == "on" for lv in b["levers"])
    dec = res.recomputed.get("decode_tok_s")
    if spec or dec is None:
        return
    if None in (hw["channels"], hw["configured_mts"], m["weights_bytes"]) or b["results"]["engine"] not in ("cpu", "igpu"):
        cap = STREAM_TOK_S_MAX.get(b["results"]["engine"])
        if cap is not None and dec > cap:
            res.flag("quarantine", "V8", f"decode {dec:.1f} tok/s per stream above {cap:.0f} with no roofline to check it")
        return
    bw = hw["channels"] * hw["configured_mts"] * 1e6 * 8
    limit = ROOFLINE * bw / m["weights_bytes"]
    res.recomputed["decode_roofline_tok_s"] = limit
    if dec > limit:
        res.flag("quarantine", "V8", f"decode {dec:.1f} tok/s above the bandwidth roofline {limit:.1f}")


# ---------------------------------------------------------------- entry
def validate(b, *, raw_size=None, trusted=False, known_hashes=(), local_identifiers=(), pins=None, repo=REPO):
    res = Result()
    size = raw_size if raw_size is not None else len(B.canonical(b))
    if size > B.MAX_BYTES:
        res.flag("reject", "V1", f"{size} bytes > {B.MAX_BYTES}")
        return res
    errs = B.check_schema(b, B.load_schema())
    if errs:
        for e in errs[:20]:
            res.flag("reject", "V1", e)
        return res
    kind = b["kind"]
    if (kind == "capacity") != (b["results"]["capacity"] is not None) or \
            (kind == "single_stream") != (b["results"]["single_stream"] is not None):
        res.flag("reject", "V1", f"kind {kind} does not match the results present")
        return res
    if b["comparison_key"] != B.comparison_key(b):
        res.flag("reject", "V2", "comparison_key does not match the bundle")
    if b["content_sha256"] != B.content_hash(b):
        res.flag("reject", "V2", "content_sha256 does not match the bundle")
    elif b["content_sha256"] in set(known_hashes):
        res.flag("reject", "V2", "duplicate of a bundle already in the data set")
    img = b["tool"]["server_image"]
    if img is not None and not B.public_image(img):
        res.flag("reject", "V3", f"$.tool.server_image names a private registry {B.image_registry(img)!r}")
    hits = canary_hits(b, local_identifiers)
    for h in hits[:20]:
        res.flag("reject", "V3", h)
    for h in identifier_hits(b, local_identifiers)[1][:5]:
        res.flag("quarantine", "V3", h)
    if b["provenance"]["source"] == "maintainer-lab" and not trusted:
        res.flag("reject", "V4", "maintainer-lab provenance is only accepted from the maintainers' lab")
    check_release(b, res, trusted, pins, repo)
    if kind == "capacity":
        recompute_capacity(b, res, trusted)
    else:
        single_stream_metrics(b, res)
        roofline(b, res)
    if b["ab"] is not None:
        res.tag("ab-unpaired")  # matrix.py pairs arms; a lone arm credits no uplift (V12)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate.py", description="Validate capacity-oracle bundles (V1-V12).")
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--trusted", action="store_true", help="accept maintainer-lab provenance (seed, lab runner)")
    ap.add_argument("--pin", action="append", default=[], help="VERSION=IMAGE known release pin")
    ap.add_argument("--known", help="dir of accepted bundles: their content hashes count as duplicates")
    ap.add_argument("--strict", action="store_true",
                    help="data/oracle check: fail on quarantine too, and the file must be named <content_sha256>.json")
    a = ap.parse_args(argv)
    pins = dict(p.split("=", 1) for p in a.pin if "=" in p)
    known = set()
    if a.known:
        for p in glob.glob(os.path.join(a.known, "**", "*.json"), recursive=True):
            try:
                with open(p) as f:
                    known.add(json.load(f).get("content_sha256"))
            except (OSError, ValueError):
                pass
    bad = 0
    for path in a.bundles:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            b = json.loads(raw)
        except ValueError as e:
            out = {"verdict": "reject", "tags": [], "reasons": [f"V1 reject: not JSON: {e}"], "recomputed": {}}
        else:
            known_others = known - {b.get("content_sha256")} if a.known and os.path.abspath(path).startswith(
                os.path.abspath(a.known)) else known
            out = validate(b, raw_size=len(raw), trusted=a.trusted, known_hashes=known_others, pins=pins).as_dict()
            if a.strict and isinstance(b, dict) and os.path.basename(path) != f"{b.get('content_sha256')}.json":
                out["verdict"] = "reject"
                out["reasons"].append("V2 reject: file name is not <content_sha256>.json")
        bad += out["verdict"] == "reject" or (a.strict and out["verdict"] != "accept")
        print(json.dumps({"path": path, **out}, sort_keys=True))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

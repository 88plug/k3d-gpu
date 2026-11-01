"""Solo holds and block pairs (EXPERIMENTS.md "H53 pre-registration").

When the two arms cannot be live at once (H53: AG takes one Arc Pro B70, B2 all four), each hold
runs one arm alone over the task set, in a task order drawn from the row's registered seed, with
rule L3's settle gap and pre-window before every attempt (pairs.attempt). Holds run in the
pre-registered arm order A, B, B, A, ...: block k pairs hold 2k-1 with hold 2k, and a pair is one
task id with a complete attempt from each hold of its block. The pairs are then scored by
pairs.analyze exactly as within-hold pairs are (rule L3), with two differences: the pairs
cross holds, and there is no idle second server, so the idle-arm check does not apply
(analyze(solo=True)). No H53 hold runs before its row is in EXPERIMENTS.md (pairs.design), and
each hold must run the arm the registered sequence gives its number (pairs.expected_arm).
Python 3 standard library only.
"""
import os
import random

import pairs as PR

SOLO_GUARD_S = PR.TASK_CAP_S + 120.0     # an attempt starts only with its cap plus resets left


def task_order(n, seed):
    """The run order of n task indices for a solo hold: a shuffle drawn from Random(f"{seed}:tasks"),
    the same for every hold of the row (so both holds of a block run the tasks in one order)."""
    idx = list(range(n))
    random.Random(f"{seed}:tasks").shuffle(idx)
    return idx


def done_in_hold(out, hold):
    """Task indices this hold already recorded in solo.jsonl (a resumed hold skips them)."""
    return {r["j"] for r in PR.read_jsonl(os.path.join(out, "solo.jsonl")) if r["hold"] == hold}


def run_solo(ab, arms, arm, task_list, out, deadline, probe, mcp_url, reset, hold, seed, cap_s=PR.TASK_CAP_S,
             guard_s=SOLO_GUARD_S, settle_s=30.0, warmup=None, clock=None, opener=None, log=lambda m: None,
             quiesce_s=0.0, warmup_timeout_s=600.0, pre=None):
    """One solo hold of `arm`: its warm-up, then the tasks in task_order until the list ends or
    the hold leaves less than guard_s. Every attempt goes to attempts.jsonl (j = the task's index
    in task_list) and one row per attempt to solo.jsonl. Returns the number of complete attempts."""
    import time
    import urllib.request
    clock = clock or time.time
    opener = opener or urllib.request.urlopen
    os.makedirs(out, exist_ok=True)
    if warmup:
        w0 = clock()
        try:
            ans, _, err = arms[arm]["agent"].ask(warmup, timeout=warmup_timeout_s)
        except (OSError, ValueError) as e:
            ans, err = None, f"{type(e).__name__}: {e}"
        PR.append(os.path.join(out, "warmups.jsonl"), {"hold": hold, "arm": arm, "seconds": round(clock() - w0, 3),
                                                       "error": err, "answer": (ans or "")[:300]})
        if err:
            raise PR.HoldAborted(f"warm-up of {arm} failed: {err}")
    seen = done_in_hold(out, hold)
    n = 0
    for j in task_order(len(task_list), seed):
        if j in seen:
            continue
        if not PR.can_start(clock(), deadline, guard_s):
            log(f"task {j}: {deadline - clock():.0f} s left < {guard_s:.0f} s, hold ends")
            break
        t = task_list[j]
        row, needs_reset = PR.attempt(ab, arms, arm, t, j, hold, probe, out, mcp_url, cap_s, settle_s, 0.0, clock,
                                      opener, quiesce_s, pre)
        void = row["reason"] if row["outcome"] == "void" else None
        if needs_reset and reset:
            try:
                ab.reset_sidecar(reset, mcp_url, t, opener)
            except ab.ResetFailed as e:
                PR.append(os.path.join(out, "solo.jsonl"), {"hold": hold, "arm": arm, "j": j, "task": t["id"],
                                                            "status": "void", "void_reason": f"reset failed: {e}"})
                raise PR.HoldAborted(f"reset after {arm} failed: {e}")
        if clock() > deadline:
            void = void or "attempt ran past the hold deadline"
        PR.append(os.path.join(out, "solo.jsonl"), {"hold": hold, "arm": arm, "j": j, "task": t["id"],
                                                    "status": "void" if void else "complete", "void_reason": void})
        log(f"hold {hold} {arm} {t['id']}: {row['outcome']} {row['duration_s']:.1f}s" + (f" VOID ({void})" if void else ""))
        n += 0 if void else 1
    return n


def block_pairs(attempts, solo_rows, a, b):
    """Synthetic within-pair records for pairs.analyze: (attempts, pairs). Block k is hold
    numbers (2k-1, 2k), whose two holds must be one of each arm; a block missing either hold has
    no pairs. A pair is a task index j with a solo row in both holds of the block; its key is
    1000 * k + j, its task id "<task>#b<k>" (a task once per block) and its order the arms of
    holds 2k-1, 2k. Both rows complete: a complete pair; either void: a void pair; run in one
    hold only (the other ended first): no pair. idle_cpu stays as recorded (None: no second
    server is live in a solo hold)."""
    arm_of = {}
    for r in solo_rows:
        if arm_of.setdefault(r["hold"], r["arm"]) != r["arm"]:
            raise ValueError(f"hold {r['hold']} has rows of {arm_of[r['hold']]} and {r['arm']}")
    by = {(r["hold"], r["j"]): r for r in attempts}
    status = {(r["hold"], r["j"]): r for r in solo_rows}
    out_at, out_pairs = [], []
    for k in range(1, max(arm_of, default=0) // 2 + 1):
        h1, h2 = 2 * k - 1, 2 * k
        if h1 not in arm_of or h2 not in arm_of:
            continue                                      # a block missing a hold: no pairs
        if {arm_of[h1], arm_of[h2]} != {a, b}:
            raise ValueError(f"block {k}: holds {h1}, {h2} run {arm_of[h1]}, {arm_of[h2]}, not {a} and {b}")
        js = sorted({j for (h, j) in status if h in (h1, h2)})
        for j in js:
            key = 1000 * k + j
            s1, s2 = status.get((h1, j)), status.get((h2, j))
            if s1 is None or s2 is None:
                continue                                  # not run in one hold of the block: no pair
            ok = s1["status"] == "complete" and s2["status"] == "complete"
            task = f"{s1['task']}#b{k}"                   # a task id once per block
            out_pairs.append({"j": key, "task": task, "hold": k, "status": "complete" if ok else "void",
                              "order": [arm_of[h1], arm_of[h2]],
                              "void_reason": None if ok else (s1.get("void_reason") or s2.get("void_reason"))})
            if ok:
                for h in (h1, h2):
                    out_at.append(dict(by[(h, j)], j=key, task=task, solo_hold=h))
    return out_at, out_pairs


# Block-level inference (EXPERIMENTS.md "H53 pre-registration"): the unit is the block, not the
# pair. Two-sided 95% t quantiles by degrees of freedom.
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}
MIN_BLOCK_PAIRS = 8         # a block with fewer complete pairs is reported, not counted
MIN_BLOCKS = 3


MAX_BLOCKS = 4              # holds 7 and 8 are the last block (no hold runs after eight)


def alive_blocks(solo_rows, ran, min_pairs=MIN_BLOCK_PAIRS, max_blocks=MAX_BLOCKS):
    """H53 amendment R1's futility rule: the blocks (1..max_blocks) that can still count, given
    solo.jsonl's rows and `ran`, the hold numbers that have run (a hold that delivered nothing has
    run but has no rows). A block is out when a hold of it has run with fewer than min_pairs
    complete attempts (its complete pairs can be no more), or when both of its holds have run and
    it has fewer than min_pairs complete pairs. It reads only which blocks may count, never d."""
    status = {(r["hold"], r["j"]): r["status"] for r in solo_rows}
    done = {}
    for (h, _), s in status.items():
        done[h] = done.get(h, 0) + (s == "complete")
    alive = []
    for k in range(1, max_blocks + 1):
        h1, h2 = 2 * k - 1, 2 * k
        if any(h in ran and done.get(h, 0) < min_pairs for h in (h1, h2)):
            continue
        if h1 in ran and h2 in ran:
            js = {j for (h, j) in status if h == h1}
            if sum(status.get((h1, j)) == "complete" and status.get((h2, j)) == "complete" for j in js) < min_pairs:
                continue
        alive.append(k)
    return alive


def block_verdict(res, a, b, threshold=0.30, min_rate=0.6):
    """The H53 verdict from pairs.analyze's result over block pairs (key 1000 * k + j). Per block
    k: d_k = mean over its matched pairs of ok(a) - ok(b). Over the n blocks with at least
    MIN_BLOCK_PAIRS pairs: mean d, its t-interval (df n - 1), and the exact sign-flip p over
    blocks. SUPPORTED: no gate, n >= MIN_BLOCKS, CI lower bound >= threshold and a's
    within-budget rate >= min_rate. REFUTED: no gate, n >= MIN_BLOCKS and CI upper bound <
    threshold. Else INCONCLUSIVE."""
    import itertools
    import math
    by = {}
    for r in res["per_pair"]:
        if r["matched"]:
            ok = int(r[a]["outcome"] == "pass_budget") - int(r[b]["outcome"] == "pass_budget")
            by.setdefault(r["j"] // 1000, []).append(ok)
    blocks = {k: {"pairs": len(v), "d": round(sum(v) / len(v), 4), "counted": len(v) >= MIN_BLOCK_PAIRS}
              for k, v in sorted(by.items())}
    d = [blocks[k]["d"] for k in blocks if blocks[k]["counted"]]
    n = len(d)
    out = {"unit": "block", "blocks": blocks, "n_blocks": n, "mean_d": None, "ci95": [None, None], "sign_flip_p": None,
           "min_block_pairs": MIN_BLOCK_PAIRS, "min_blocks": MIN_BLOCKS, "threshold": threshold}
    if n >= 2:
        m = sum(d) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
        h = T975.get(n - 1, 1.96) * sd / math.sqrt(n)
        obs = abs(m)
        flips = [abs(sum(s * x for s, x in zip(sg, d)) / n) for sg in itertools.product((1, -1), repeat=n)]
        out.update(mean_d=round(m, 4), ci95=[round(m - h, 4), round(m + h, 4)],
                   sign_flip_p=round(sum(f >= obs - 1e-12 for f in flips) / len(flips), 4))
    rate = res["within_budget_success"][a]["rate"]
    lo, hi = out["ci95"]
    if res["gates"] or n < MIN_BLOCKS:
        v = "INCONCLUSIVE"
    elif lo >= threshold and rate is not None and rate >= min_rate:
        v = "SUPPORTED"
    elif hi < threshold:
        v = "REFUTED"
    else:
        v = "INCONCLUSIVE"
    out["verdict"] = v
    return out

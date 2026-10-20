"""Solo holds and block pairs (EXPERIMENTS.md "H53 pre-registration").

When the two arms cannot be live at once (H53: AG takes one Arc Pro B70, B2 all four), each hold
runs one arm alone over the task set, in a task order drawn from the row's registered seed, with
rule L3's settle gap and pre-window before every attempt (pairs.attempt). Holds run in the
pre-registered arm order A, B, B, A, ...: block k pairs hold 2k-1 with hold 2k, and a pair is one
task id with a complete attempt from each hold of its block. The pairs are then scored by
pairs.analyze exactly as within-hold pairs are (rule L3), with two differences stated in the
pre-registration: the pairs cross holds, and there is no idle second server, so the idle-arm
check reads 0. Python 3 standard library only.
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
    """Synthetic within-pair records for pairs.analyze: (attempts, pairs). Holds are taken in
    order 1, 2, 3, ...; block k = (hold 2k-1, hold 2k), whose two holds must be one of each arm.
    A pair is a task index j with a solo row in both holds of the block; its key is 1000 * k + j
    and its task id "<task>#b<k>" (a task once per block). Both rows complete: a complete pair;
    either void: a void pair; run in one hold only (the other ended first): no pair. idle_cpu is
    0.0: no second server is live in a solo hold."""
    holds = sorted({r["hold"] for r in solo_rows})
    arm_of = {}
    for r in solo_rows:
        arm_of.setdefault(r["hold"], r["arm"])
    by = {(r["hold"], r["j"]): r for r in attempts}
    status = {(r["hold"], r["j"]): r for r in solo_rows}
    out_at, out_pairs = [], []
    for k in range(len(holds) // 2):
        h1, h2 = holds[2 * k], holds[2 * k + 1]
        if {arm_of[h1], arm_of[h2]} != {a, b}:
            raise ValueError(f"block {k + 1}: holds {h1}, {h2} run {arm_of[h1]}, {arm_of[h2]}, not {a} and {b}")
        js = sorted({j for (h, j) in status if h in (h1, h2)})
        for j in js:
            key = 1000 * (k + 1) + j
            s1, s2 = status.get((h1, j)), status.get((h2, j))
            if s1 is None or s2 is None:
                continue                                  # not run in one hold of the block: no pair
            ok = s1["status"] == "complete" and s2["status"] == "complete"
            task = f"{s1['task']}#b{k + 1}"               # a task id once per block
            out_pairs.append({"j": key, "task": task, "hold": k + 1, "status": "complete" if ok else "void",
                              "void_reason": None if ok else (s1.get("void_reason") or s2.get("void_reason"))})
            if ok:
                for h in (h1, h2):
                    out_at.append(dict(by[(h, j)], j=key, task=task, idle_cpu=0.0, solo_hold=h))
    return out_at, out_pairs

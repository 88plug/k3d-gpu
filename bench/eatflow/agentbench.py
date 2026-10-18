#!/usr/bin/env python3
"""k3d-gpu agent benchmark: real agent flows, graded, timed, on a busy box.

Drives Intel's EAT blueprint agents (banking-assistant, coding-agent-maf) through
their OpenAI Responses endpoint, grades each task deterministically, times every
LLM turn at one recording proxy, holds the background load fixed with
bench/load/load.py, meters RAPL energy, and writes results that resume after a
crash. BFCL v4 on the fixed S100 subset covers the portable public suite.

Subcommands (k3d-gpu bench agent <subcommand> ...):
  plan      print the pre-registered block plan of a hypothesis (no side effects)
  proxy     run the recording proxy (in a pod, or on the host)
  manifest  print a ConfigMap + Deployment + Service running the proxy in-cluster
  run       one block: a task set against one arm at one load level
  control   load-only control window (energy baseline for J per success)
  apb       agents-per-box ramp N = 1, 2, 4, 8 on B15-RO, graded
  bfcl      BFCL S100 through the proxy (board or server-fc mode)
  regrade   apply the current graders to recorded blocks (originals kept)
  report    report.json + report.md from run directories
  tasks     list a task set

Python 3 standard library only. See docs/agent-benchmarks.md.
"""
import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "load"))
import bfcl  # noqa: E402
import energy  # noqa: E402
import proxy  # noqa: E402
import stats  # noqa: E402
import tasks as T  # noqa: E402

LOCK_PATH = os.path.join(HERE, "agent.lock")
INF = float("inf")
SLO_S = 10.0
RAMP = (1, 2, 4, 8)
ARM_BUDGET_S = 1200       # measurement per arm per hypothesis (design section 5)
BRINGUP_CAP_S = 900       # bring-up cap; over it the arm is "not deliverable"
TASK_TIMEOUT_S = 600


def lock():
    with open(LOCK_PATH) as f:
        return json.load(f)


def log(msg):
    print(f"agentbench: {msg}", file=sys.stderr, flush=True)


def _load_mod():
    try:
        import load  # noqa: F401
        return load
    except ImportError:
        return None


# ---- agent endpoint (OpenAI Responses API, Agent Framework DevUI) ---------------------

class Agent:
    def __init__(self, url, token=None, entity=None, opener=urllib.request.urlopen):
        self.url = url.rstrip("/")
        self.token = token
        self.opener = opener
        self.entity = entity

    def _req(self, path, body=None, timeout=60):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=h,
                                     method="GET" if body is None else "POST")
        with self.opener(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def resolve_entity(self):
        if not self.entity:
            self.entity = self._req("/v1/entities")["entities"][0]["id"]
        return self.entity

    def ask(self, prompt, timeout):
        """(answer text, response status, error or None)."""
        body = {"input": prompt, "metadata": {"entity_id": self.resolve_entity()}, "stream": False}
        try:
            r = self._req("/v1/responses", body, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError) as e:
            if isinstance(e, urllib.error.HTTPError):
                e.close()
            return None, None, f"{type(e).__name__}: {e}"
        return answer_text(r), r.get("status"), None


def answer_text(resp):
    out = []
    for it in resp.get("output", []) or []:
        if it.get("type") == "message":
            for c in it.get("content", []) or []:
                if c.get("type") in ("output_text", "text"):
                    out.append(c.get("text", ""))
    return "\n".join(out).strip()


def graded_answer(answer, recs):
    """The text to grade, and its source ("model" or "devui").

    DevUI's answer is Agent Framework's Message.text, which joins text items with " " when
    other content items sit between the stream deltas: '$4,250.75' then reads
    '$ 4 , 250 . 75'. When the model's own text (the task's turns at the proxy, in order)
    equals the DevUI answer up to whitespace, that text is graded instead."""
    model = "\n".join(r["content"] for r in sorted(recs, key=lambda r: r.get("ts_send") or 0)
                      if r.get("content")).strip()
    if model and answer and model != answer and "".join(model.split()) == "".join(answer.split()):
        return model, "model"
    return answer, "devui"


# ---- banking MCP end state ---------------------------------------------------------

def mcp_call(url, name, args, opener=urllib.request.urlopen, timeout=30):
    """tools/call over MCP Streamable HTTP; returns the tool's JSON result."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with opener(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    msg = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
    if msg is None:
        msg = json.loads(raw)
    res = msg.get("result") or {}
    if res.get("structuredContent") is not None:
        sc = res["structuredContent"]
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    for c in res.get("content") or []:
        if c.get("type") == "text":
            return json.loads(c["text"])
    raise ValueError(f"MCP {name}: no result in {raw[:200]}")


def mcp_state(url, task, opener=urllib.request.urlopen):
    accts, loans = T.end_state_accounts(task)
    st = {"balances": {}, "loans": {}}
    for a in accts:
        st["balances"][a] = mcp_call(url, "get_account_balance", {"account_id": a}, opener).get("available_balance")
    for lid in loans:
        st["loans"][lid] = mcp_call(url, "get_loan_summary", {"loan_id": lid}, opener).get("payoff_amount")
    return st


def seed_state(task):
    accts, loans = T.end_state_accounts(task)
    return {"balances": {a: T.AVAILABLE[a] for a in accts}, "loans": {lid: T.LOANS[lid][2] for lid in loans}}


# ---- proxy-record attribution ------------------------------------------------------

def attribute(records, prompt, t0, t1, slack=0.0):
    """Proxy records of one task: its prompt is the last (or first) user message, and the
    proxy received the request inside the task window."""
    return [r for r in records if prompt in (r.get("last_user"), r.get("first_user"))
            and t0 - slack <= (r.get("ts_send") or 0) <= t1 + slack]


def is_final(rec):
    """The turn that produced the agent's answer: no tool call, no error."""
    return not rec.get("tool_calls") and not rec.get("error") and (rec.get("status") or 0) < 400


def wait_records(proxy_log, prompt, t0, t1, need_final, max_wait, poll=0.1, clock=time.time):
    """Poll the proxy log until the task's final turn is in it (a `kubectl logs -f` copy
    lags behind the proxy), or max_wait seconds pass. Returns the attributed records."""
    end = clock() + max_wait
    while True:
        recs = attribute(proxy.read_log(proxy_log), prompt, t0, t1)
        if not need_final or any(is_final(r) for r in recs) or clock() >= end:
            return recs
        time.sleep(poll)


def flat_calls(recs):
    return [c for r in recs for c in (r.get("tool_calls") or [])]


def evidence_text(recs):
    """Every distinct tool result the agent was sent during the task."""
    seen = []
    for r in recs:
        for t in r.get("tool_results") or []:
            if t not in seen:
                seen.append(t)
    return "\n".join(seen)


def memory_users(recs):
    """Largest user-message count among the task's requests (None: proxy predates it)."""
    n = [r["user_msgs"] for r in recs if r.get("user_msgs") is not None]
    return max(n) if n else None


# ---- results file (resumable) ------------------------------------------------------

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class Results:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.rows = read_jsonl(path)

    def done(self, arm, level, n):
        """Task ids already measured (censored ones are measured again on resume)."""
        return {r["task"] for r in self.rows if r["arm"] == arm and r["level"] == level
                and r["n"] == n and r["status"] != "censored"}

    def add(self, row, turns=()):
        """Append a task row, and its LLM turns (proxy records) to turns.jsonl beside it."""
        with self.lock:
            with open(os.path.join(os.path.dirname(self.path), "turns.jsonl"), "a") as f:
                for r in turns:
                    f.write(json.dumps(dict(r, task=row["task"], arm=row["arm"], level=row["level"],
                                            n=row["n"], step=row.get("step"))) + "\n")
            with open(self.path, "a") as f:
                f.write(json.dumps(row) + "\n")
            self.rows.append(row)


# ---- load + energy around a block ----------------------------------------------------

class LoadSampler(threading.Thread):
    """bench/load/load.py windows, tagged with the arm, appended to load.jsonl."""

    def __init__(self, path, tag, level, seconds=30.0, window=None):
        super().__init__(daemon=True)
        lm = _load_mod()
        self.window = window or (lambda s: lm.window(s, lm.load_cpus(), tag=tag, level=level))
        self.path, self.seconds = path, seconds
        self.stop_ev = threading.Event()

    def run(self):
        while not self.stop_ev.is_set():
            w = self.window(self.seconds)
            with open(self.path, "a") as f:
                f.write(json.dumps(w) + "\n")

    def stop(self):
        self.stop_ev.set()


def host_facts():
    def rd(p):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            return ""
    model = next((ln.split(":", 1)[1].strip() for ln in rd("/proc/cpuinfo").splitlines()
                  if ln.startswith("model name")), None)
    return {"cpu": model, "kernel": platform.release(), "loadavg": rd("/proc/loadavg").strip(),
            "python": platform.python_version()}


def write_facts(out, args, extra=None):
    facts = {"argv": sys.argv, "args": {k: v for k, v in vars(args).items() if k not in ("token", "func")},
             "host": host_facts(), "lock": lock(), "ts": time.time()}
    facts.update(extra or {})
    with open(os.path.join(out, "facts.json"), "w") as f:
        json.dump(facts, f, indent=1, default=str)


# ---- one block ---------------------------------------------------------------------

WARMUP = {"bank": "My user id is U1001. Which account ids do I have? Answer with the ids only.",
          "code": "Run Python in the sandbox to print the value of 7 * 6."}


class BlockAborted(Exception):
    """A block stopped before its end: the run is not valid and the CLI exits non-zero."""

    def __init__(self, reason, rows):
        super().__init__(reason)
        self.reason, self.rows = reason, rows


class ResetFailed(Exception):
    pass


def warmup_prompt(task_list):
    return WARMUP["code"] if task_list and task_list[0]["id"].startswith("C") else WARMUP["bank"]


def run_block(agent, task_list, arm, level, n, out, budget_s, proxy_log, mcp_url=None, reset=None,
              cycle=False, task_timeout=TASK_TIMEOUT_S, clock=time.time, settle_s=30.0,
              opener=urllib.request.urlopen, sampler=None, meter=None, step=None, warmup=None,
              reset_tries=60):
    """Closed-loop block: n workers take tasks until the set is done (or, with cycle,
    until the budget ends). Each finished task is graded and appended to
    results.jsonl. Returns the rows of this block.

    warmup: one unscored prompt sent before the window opens (cold start: compile, cache
    fill); its answer must come back. settle_s: the longest wait for a task's final LLM
    turn to reach the proxy log. In cycle mode every sent prompt carries a nonce prefix
    so no two requests repeat. After a mutating task, or any task whose end state reads
    back off the seed, --reset runs, the seed is read back, and the warm-up prompt must
    answer again before the next task. Any failure there, or a request that carries more
    than one user message (agent memory on), aborts the block: BlockAborted."""
    os.makedirs(out, exist_ok=True)
    res = Results(os.path.join(out, "results.jsonl"))
    skip = set() if cycle else res.done(arm, level, n) & {t["id"] for t in task_list}
    queue = [t for t in task_list if t["id"] not in skip]
    if skip:
        log(f"resume: {len(skip)} task(s) already measured, {len(queue)} left")
    qlock = threading.Lock()
    inflight = set()
    rows = []
    rep = {"i": 0, "seq": 0}
    abort = {"reason": None}
    abort_ev = threading.Event()
    blk = {"arm": arm, "level": level, "n": n, "step": step, "warmup": None}

    def finish(block_s, e):
        blk.update(seconds=round(block_s, 3), tasks=len(rows), energy=e, aborted=abort["reason"])
        with open(os.path.join(out, "blocks.jsonl"), "a") as f:
            f.write(json.dumps(blk) + "\n")
        if abort["reason"]:
            raise BlockAborted(abort["reason"], rows)

    def stop(reason):
        with qlock:
            if abort["reason"] is None:
                abort["reason"] = reason
        abort_ev.set()

    if warmup:
        w0 = clock()
        ans, _, err = agent.ask(warmup, timeout=task_timeout)
        blk["warmup"] = {"prompt": warmup, "seconds": round(clock() - w0, 3), "error": err,
                         "answer": (ans or "")[:300], "t_end": clock()}
        if err:
            stop(f"warm-up failed: {err}")
            finish(0.0, None)
    deadline = clock() + budget_s

    def next_task():
        with qlock:
            if cycle:
                for _ in range(len(task_list)):
                    t = task_list[rep["i"] % len(task_list)]
                    rep["i"] += 1
                    if t["id"] not in inflight:
                        inflight.add(t["id"])
                        rep["seq"] += 1
                        return t, rep["i"] // len(task_list), f"[ref {step or 'b'}-{rep['seq']}] "
                return None, 0, ""
            while queue:
                t = queue.pop(0)
                inflight.add(t["id"])
                return t, 0, ""
            return None, 0, ""

    def one(t, cyc, nonce):
        prompt = nonce + t["prompt"]
        left = deadline - clock()
        t0 = clock()
        limit = min(task_timeout, left)
        answer, status, err = agent.ask(prompt, timeout=limit)
        t1 = clock()
        dur = t1 - t0
        if err and "timed out" in err.lower() and limit < task_timeout:
            state, kind = "censored", "censored"
        elif err and "timed out" in err.lower():
            state, kind = "timeout", "fail"
        elif err:
            state, kind = "error", "fail"
        else:
            state, kind = "done", None
        recs = (wait_records(proxy_log, prompt, t0, t1, kind is None, settle_s, clock=clock)
                if proxy_log else [])
        users = memory_users(recs)
        if users is not None and users > 1:
            stop(f"agent memory is on: an LLM request of {t['id']} carried {users} user messages "
                 "(deploy the blueprint with ENABLE_MEMORY=false)")
        calls = flat_calls(recs)
        end = None
        if mcp_url and t.get("end_state"):
            try:
                end = mcp_state(mcp_url, t, opener)
            except (OSError, ValueError, urllib.error.URLError) as e:
                end, err = None, f"end-state read failed: {e}"
        graded, source = graded_answer(answer, recs)
        if kind is None:
            ok, reason = T.grader_for(t)(t, graded, calls, end,
                                         evidence=evidence_text(recs) if proxy_log else None)
            kind = "pass" if ok else "fail"
        else:
            ok, reason = False, err or state
        strict = ok and bool(calls) and all(c.get("valid") for c in calls)
        row = {"task": t["id"], "arm": arm, "level": level, "n": n, "cycle": cyc, "step": step,
               "nonce": nonce or None, "status": kind, "passed": kind == "pass", "strict": strict,
               "reason": reason, "t_start": t0, "t_end": t1, "duration_s": round(dur, 3),
               "censored": kind == "censored", "response_status": status, "answer": (answer or "")[:2000],
               "answer_source": source,
               "error": err, "llm_requests": len(recs), "user_msgs_max": users,
               "truncated": any(r.get("finish_reason") == "length" for r in recs), "end_state": end,
               "calls": [{k: c.get(k) for k in ("name", "valid", "reason")} for c in calls],
               "first_turn_valid": (any(c.get("valid") for c in recs[0].get("tool_calls") or [])
                                    if recs and t.get("tools") else None)}
        res.add(row, recs)
        rows.append(row)
        moved = end is not None and end != seed_state(t)
        if reset and (t.get("mutating") or moved) and not abort_ev.is_set():
            reset_sidecar(reset, mcp_url, t, opener, tries=reset_tries)
            probe = warmup or warmup_prompt([t])
            _, _, perr = agent.ask(probe, timeout=task_timeout)
            if perr:
                raise ResetFailed(f"agent did not answer after --reset: {perr}")

    def worker():
        while not abort_ev.is_set():
            if deadline - clock() <= 0:
                return
            t, cyc, nonce = next_task()
            if t is None:
                return
            try:
                one(t, cyc, nonce)
            except ResetFailed as e:
                stop(f"reset after {t['id']} failed: {e}")
            except Exception as e:  # noqa: BLE001 - any worker crash invalidates the block
                stop(f"worker crashed on {t['id']}: {type(e).__name__}: {e}")
            finally:
                with qlock:
                    inflight.discard(t["id"])

    if sampler:
        sampler.start()
    if meter:
        meter.start()
    t_block = clock()
    ws = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    block_s = clock() - t_block
    e = meter.stop() if meter else None
    if sampler:
        sampler.stop()
    finish(block_s, e)
    return rows


def reset_sidecar(cmd, mcp_url, task, opener=urllib.request.urlopen, tries=60, timeout=300):
    """Run the reset command (it must exit 0 within timeout), then wait until the MCP
    server reads back the seed. Raises ResetFailed."""
    try:
        p = subprocess.run(shlex.split(cmd), check=False, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ResetFailed(f"--reset did not finish in {timeout} s")
    except OSError as e:
        raise ResetFailed(f"--reset could not run: {e}")
    if p.returncode != 0:
        tail = (p.stderr or b"").decode(errors="replace").strip()[-300:]
        raise ResetFailed(f"--reset exit {p.returncode}: {tail}")
    if not mcp_url:
        return
    want = seed_state(task)
    for i in range(tries):
        try:
            if mcp_state(mcp_url, task, opener) == want:
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        if i + 1 < tries:
            time.sleep(2)
    raise ResetFailed("MCP server did not return to the seed state after --reset")


# ---- regrade -------------------------------------------------------------------------

def regrade(run_dir):
    """Apply the current graders to a finished block's recorded answers and tool calls.

    The first run keeps its rows in results.raw.jsonl; results.jsonl is rewritten, and a
    row whose verdict changes carries "regraded": {"from": old reason}. Rows that failed
    before grading (error, timeout, censored) and mutating rows without a recorded end
    state keep their verdict. Returns the number of changed rows."""
    path = os.path.join(run_dir, "results.jsonl")
    raw = os.path.join(run_dir, "results.raw.jsonl")
    if not os.path.exists(raw):
        shutil.copyfile(path, raw)
    rows = read_jsonl(raw)
    turns = read_jsonl(os.path.join(run_dir, "turns.jsonl"))
    by_id = {t["id"]: t for ts in T.TASKSETS.values() for t in ts}
    changed = 0
    for r in rows:
        t = by_id.get(r["task"])
        if t is None or r["status"] not in ("pass", "fail") or r.get("error"):
            continue
        state = r.get("end_state")
        if t.get("mutating") and state is None:
            continue
        recs = [x for x in turns if (x.get("task"), x.get("arm"), x.get("level"), x.get("n"), x.get("step"))
                == (r["task"], r["arm"], r["level"], r["n"], r.get("step"))
                and r["t_start"] <= (x.get("ts_send") or 0) <= r["t_end"]]
        calls = flat_calls(recs)
        graded, r["answer_source"] = graded_answer(r["answer"], recs)
        ok, reason = T.grader_for(t)(t, graded, calls, state, evidence=evidence_text(recs))
        if ok != r["passed"]:
            changed += 1
            r["regraded"] = {"from": r["reason"]}
            r.update(passed=ok, status="pass" if ok else "fail", reason=reason,
                     strict=ok and bool(calls) and all(c.get("valid") for c in calls))
    with open(path, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    return changed


# ---- apb gate ----------------------------------------------------------------------

def window_requests(recs, t_from):
    return [r for r in recs if (r.get("ts_send") or 0) >= t_from and r.get("error") != "capped"]


def latencies(recs):
    return [INF if (r.get("error") or r.get("e2e_s") is None or r["e2e_s"] > proxy.TIMEOUT_S) else r["e2e_s"]
            for r in recs]


def apb_gate(step_rows, step_recs, base_success, slo=SLO_S):
    """PROTOCOL.md section 6 plus the success gate: (pass, details)."""
    lat = latencies(step_recs)
    p95 = stats.nearest_rank(lat, 95)
    done = [r for r in step_rows if not r["censored"]]
    succ = sum(r["passed"] for r in done) / len(done) if done else None
    err = sum(1 for x in lat if x == INF) / len(lat) if lat else None
    checks = {
        "n_requests": len(lat), "p95_s": p95, "success": succ, "error_rate": err,
        "p95_ok": p95 is not None and p95 <= slo,
        "success_ok": succ is not None and base_success is not None and succ >= base_success - 0.10,
        "errors_ok": err is not None and err <= 0.01,
        "n_ok": len(lat) >= 60,
    }
    ok = checks["p95_ok"] and checks["success_ok"] and checks["errors_ok"] and checks["n_ok"]
    return ok, checks


def agents_per_box(steps):
    """Largest N such that every step at or below N passed; 0 when N=1 failed."""
    best = 0
    for s in sorted(steps, key=lambda s: s["n"]):
        if not s["pass"]:
            break
        best = s["n"]
    return best


# ---- report ------------------------------------------------------------------------

def _f(v, nd=2):
    if v is None:
        return "-"
    if v == INF:
        return "inf"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def summarize_rows(rows, recs):
    done = [r for r in rows if not r["censored"]]
    k = sum(r["passed"] for r in done)
    strict = sum(r["strict"] for r in done)
    lo, hi = stats.wilson(k, len(done))
    calls = [c for r in done for c in r["calls"]]
    valid = sum(1 for c in calls if c.get("valid"))
    ftv = [r["first_turn_valid"] for r in done if r.get("first_turn_valid") is not None]
    lat = latencies(recs)
    ttft = [r["ttft_s"] for r in recs if r.get("ttft_s") is not None and not r.get("error")]
    tool_tok = sorted(r["completion_tokens"] for r in recs if r.get("tool_calls") and r.get("completion_tokens"))
    cached = [r["cached_tokens"] / r["prompt_tokens"] for r in recs
              if r.get("cached_tokens") is not None and r.get("prompt_tokens")]
    dur = [r["duration_s"] for r in done]
    cens = [r["duration_s"] for r in rows if r["censored"]]
    return {
        "tasks": len(done), "censored": len(cens), "censored_lower_bounds_s": cens,
        "success": k / len(done) if done else None, "success_wilson95": [lo, hi], "successes": k,
        "strict_success": strict / len(done) if done else None,
        "tool_calls": len(calls), "tool_call_validity": valid / len(calls) if calls else None,
        "tool_valid_any": sum(ftv) / len(ftv) if ftv else None,
        "llm_requests": len(recs),
        "turn_p50_s": stats.nearest_rank(lat, 50), "turn_p95_s": stats.nearest_rank(lat, 95),
        "turn_p95_ci95": list(stats.bootstrap_ci(lat, lambda x: stats.nearest_rank(x, 95))),
        "ttft_p95_s": stats.nearest_rank(ttft, 95),
        "task_median_s": stats.median(dur), "task_p95_s": stats.nearest_rank(dur, 95),
        "tool_turn_completion_tokens_median": stats.median(tool_tok),
        "cached_fraction": sum(cached) / len(cached) if cached else None,
        "error_rate": (sum(1 for x in lat if x == INF) / len(lat)) if lat else None,
        "sampling": unique([r.get("sampling") for r in recs]),
    }


def unique(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def collect(run_dirs):
    rows, recs, blocks, loads, controls, bfcls, apbs = [], [], [], [], [], [], []
    for d in run_dirs:
        rows += read_jsonl(os.path.join(d, "results.jsonl"))
        recs += proxy.read_log(os.path.join(d, "turns.jsonl"))
        blocks += read_jsonl(os.path.join(d, "blocks.jsonl"))
        loads += read_jsonl(os.path.join(d, "load.jsonl"))
        controls += read_jsonl(os.path.join(d, "control.jsonl"))
        p = os.path.join(d, "bfcl.json")
        if os.path.exists(p):
            with open(p) as f:
                bfcls.append(json.load(f))
        p = os.path.join(d, "apb.json")
        if os.path.exists(p):
            with open(p) as f:
                apbs.append(json.load(f))
    return rows, recs, blocks, loads, controls, bfcls, apbs


def build_report(run_dirs, ref=None):
    rows, recs, blocks, loads, controls, bfcls, apbs = collect(run_dirs)
    groups = {}
    for r in rows:
        groups.setdefault((r["arm"], r["level"], r["n"]), []).append(r)
    out = {"schema": "k3d-gpu.agent-bench-report/v1", "generated": time.time(), "runs": run_dirs,
           "groups": [], "pairs": [], "load": {}, "bfcl": [], "apb": apbs}
    for (arm, level, n), rs in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
        mine = [x for x in recs if (x.get("arm"), x.get("level"), x.get("n")) == (arm, level, n)]
        s = summarize_rows(rs, mine)
        bl = [b for b in blocks if b["arm"] == arm and b["level"] == level and b["n"] == n and b.get("energy")]
        ctl = [c for c in controls if c["arm"] == arm and c["level"] == level]
        if bl:
            e = {"seconds": sum(b["energy"]["seconds"] for b in bl),
                 "energy_j": (None if any(b["energy"].get("energy_j") is None for b in bl)
                              else sum(b["energy"]["energy_j"] for b in bl))}
            jps, why = energy.j_per_success(e, ctl[-1]["energy"] if ctl else None, s["successes"])
            s["j_per_success"], s["j_per_success_note"] = jps, why
        s.update(arm=arm, level=level, n=n)
        out["groups"].append(s)
    # paired success difference vs the reference arm, per level and n, on shared task ids
    if ref:
        for (arm, level, n), rs in groups.items():
            if arm == ref or (ref, level, n) not in groups:
                continue
            a = {r["task"]: int(r["passed"]) for r in rs if not r["censored"]}
            b = {r["task"]: int(r["passed"]) for r in groups[(ref, level, n)] if not r["censored"]}
            shared = sorted(set(a) & set(b))
            pd = stats.paired_diff(a, b)
            ta = {r["task"]: r["duration_s"] for r in rs if not r["censored"]}
            tb = {r["task"]: r["duration_s"] for r in groups[(ref, level, n)] if not r["censored"]}
            pr = stats.paired_ratio(ta, tb)
            out["pairs"].append({
                "arm": arm, "ref": ref, "level": level, "n": n, "shared_ids": len(shared),
                "arm_completed": len(a), "ref_completed": len(b),
                "arm_success_shared": sum(a[i] for i in shared) / len(shared) if shared else None,
                "ref_success_shared": sum(b[i] for i in shared) / len(shared) if shared else None,
                "success_diff": pd, "time_ratio_median": pr["ratio"], "time_ratio_ci95": pr["ci95"],
                "time_ratio_n": pr["n"]})
    lm = _load_mod()
    for level in sorted({w.get("level") for w in loads if not str(w.get("tag") or "").endswith("-control")}, key=str):
        ws = [w for w in loads if w.get("level") == level and not str(w.get("tag") or "").endswith("-control")]
        out["load"][str(level)] = lm.matched(ws) if lm else {"verdict": "load.py unavailable"}
    by_mode = {}
    for b in bfcls:
        by_mode.setdefault(b["mode"], {})[b["arm"]] = b
    for mode, arms in sorted(by_mode.items()):
        refb = arms.get(ref) if ref else None
        for arm, b in sorted(arms.items()):
            entry = {"mode": mode, "arm": arm, "stopped": b["stopped"], **bfcl.summarize(b["passed"])}
            if refb and arm != ref:
                pd = stats.paired_diff(b["passed"], refb["passed"])
                entry["vs_ref"] = dict(pd, ref=ref, n_ok=pd["n"] >= 60)
            out["bfcl"].append(entry)
    return out


def render_md(rep):
    L = ["# Agent benchmark report", "",
         "Generated by `k3d-gpu bench agent report` from: " + ", ".join(f"`{d}`" for d in rep["runs"]), ""]
    if rep["groups"]:
        L += ["## Agent flows", "",
              "| Arm | Load | N | Tasks | Censored | Success (Wilson 95%) | Strict | Call validity | "
              "tool_valid_any | Turn p50 s | Turn p95 s | TTFT p95 s | Task median s | Task p95 s | J/success |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for g in rep["groups"]:
            lo, hi = g["success_wilson95"]
            L.append(f"| {g['arm']} | {g['level']} | {g['n']} | {g['tasks']} | {g['censored']} | "
                     f"{_f(g['success'])} ({g['successes']}/{g['tasks']}), {_f(lo)}-{_f(hi)} | "
                     f"{_f(g['strict_success'])} | "
                     f"{_f(g['tool_call_validity'])} | {_f(g['tool_valid_any'])} | {_f(g['turn_p50_s'])} | "
                     f"{_f(g['turn_p95_s'])} | {_f(g['ttft_p95_s'])} | {_f(g['task_median_s'])} | "
                     f"{_f(g['task_p95_s'])} | {_f(g.get('j_per_success'), 1)} |")
        L.append("")
    if rep["pairs"]:
        L += ["## Paired vs reference (shared completed task ids only)", "",
              "| Arm | Ref | Load | N | Completed (arm/ref) | Shared ids | Success on shared (arm/ref) | "
              "Success diff (95% CI) | Paired time ratio (95% CI) |", "|---|---|---|---|---|---|---|---|---|"]
        for p in rep["pairs"]:
            d = p["success_diff"]
            L.append(f"| {p['arm']} | {p['ref']} | {p['level']} | {p['n']} | {p['arm_completed']}/{p['ref_completed']} | "
                     f"{p['shared_ids']} | {_f(p['arm_success_shared'])}/{_f(p['ref_success_shared'])} | "
                     f"{_f(d['diff'])} ({_f(d['ci95'][0])}..{_f(d['ci95'][1])}) | {_f(p['time_ratio_median'])} "
                     f"({_f(p['time_ratio_ci95'][0])}..{_f(p['time_ratio_ci95'][1])}) |")
        L.append("")
    if rep["bfcl"]:
        L += ["## BFCL S100", "", "| Mode | Arm | Ids | Accuracy % | Board-weighted expectation % | "
              "vs ref (paired diff, one-sided 95% LB) | Stopped |", "|---|---|---|---|---|---|---|"]
        for b in rep["bfcl"]:
            v = b.get("vs_ref")
            vs = (f"{_f(100 * v['diff'], 1)} pts (LB {_f(100 * v['lb95_one_sided'], 1)}, n={v['n']}"
                  f"{'' if v['n_ok'] else ', n<60: INCONCLUSIVE'})") if v and v["diff"] is not None else "-"
            L.append(f"| {b['mode']} | {b['arm']} | {b['n']} | {_f(b['acc'], 1)} | {_f(b['expected'], 1)} | {vs} | "
                     f"{b['stopped']} |")
        L.append("")
    if rep["apb"]:
        L += ["## Agents per box", "", "| Arm | Load | APB | Steps (N: p95 s / success / errors / requests) |",
              "|---|---|---|---|"]
        for a in rep["apb"]:
            st = "; ".join(f"{s['n']}: {_f(s['checks']['p95_s'])} / {_f(s['checks']['success'])} / "
                           f"{_f(s['checks']['error_rate'], 3)} / {s['checks']['n_requests']}"
                           f"{'' if s['pass'] else ' FAIL'}" for s in a["steps"])
            L.append(f"| {a['arm']} | {a['level']} | {a['apb']} | {st} |")
        L.append("")
    if rep["load"]:
        L += ["## Background load", ""]
        for level, m in rep["load"].items():
            L.append(f"- `{level}`: {m.get('verdict')}")
        L.append("")
    L += ["Censored tasks were cut by the block budget; their durations are lower bounds, not failures.", ""]
    return "\n".join(L)


# ---- plan --------------------------------------------------------------------------

def plan(hyp, budget_s=ARM_BUDGET_S):
    """The pre-registered block list of a hypothesis (design section 5).

    order: single    one block per arm
           ABBA      per level, each arm's budget in two halves: A B ... B A
           alternate per arm, none/heavy/none/heavy at budget/4 each (H44)
           ramp      per arm, N = 1, 2, 4, 8 at budget/4 each (H45)
           control   per arm, one 120 s load-only control window (H46)"""
    hs = lock()["hypotheses"]
    h = hs.get(hyp)
    if not h:
        raise SystemExit(f"unknown hypothesis {hyp} (known: {', '.join(sorted(hs))})")
    arms, order, blocks = h["arms"], h["order"], []
    if order == "single":
        blocks = [{"arm": a, "level": lv, "seconds": budget_s} for lv in h["levels"] for a in arms]
    elif order == "ABBA":
        for lv in h["levels"]:
            blocks += [{"arm": a, "level": lv, "seconds": budget_s / 2} for a in arms + arms[::-1]]
    elif order == "alternate":
        for a in arms:
            blocks += [{"arm": a, "level": lv, "seconds": budget_s / 4} for lv in h["levels"] * 2]
    elif order == "ramp":
        for a in arms:
            blocks += [{"arm": a, "level": lv, "n": n, "seconds": budget_s / len(RAMP)}
                       for lv in h["levels"] for n in RAMP]
    elif order == "control":
        blocks = [{"arm": a, "level": lv, "seconds": 120.0, "control": True} for lv in h["levels"] for a in arms]
    else:
        raise SystemExit(f"{hyp}: unknown order {order!r}")
    for b in blocks:
        b["workload"] = h["workload"]
    return {"hypothesis": hyp, **h, "blocks": blocks,
            "measurement_s": sum(b["seconds"] for b in blocks), "bringup_cap_s": BRINGUP_CAP_S}


# ---- CLI ---------------------------------------------------------------------------

def cmd_tasks(a):
    for t in T.taskset(a.set):
        print(f"{t['id']}\t{t['prompt']}")
    return 0


def cmd_plan(a):
    print(json.dumps(plan(a.hypothesis, a.budget_s), indent=1))
    return 0


def sampling_set(spec):
    """A sampling profile name from agent.lock ("agent_local", "agent_remote") or a JSON object."""
    if not spec:
        return None
    profiles = lock().get("sampling", {})
    if spec in profiles:
        return {k: v for k, v in profiles[spec].items() if not k.startswith("_")}
    try:
        d = json.loads(spec)
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise SystemExit(f"agentbench: --sampling {spec!r} is neither a profile ({', '.join(profiles)}) "
                         "nor a JSON object")
    return d


def cmd_proxy(a):
    key = proxy.read_key_file(a.key_file, a.key_var) if a.key_file else None
    if a.log != "-":
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
    try:
        px = proxy.Proxy(a.upstream, "/dev/stdout" if a.log == "-" else a.log, max_requests=a.max_requests,
                         include_usage=not a.no_usage, api_key=key, sampling=sampling_set(a.sampling))
    except ValueError as e:
        raise SystemExit(f"agentbench: {e} (--max-requests)")
    srv = proxy.serve(px, a.host, a.port)
    log(f"proxy on {srv.server_address[0]}:{srv.server_address[1]} -> {a.upstream} (log {a.log}, "
        f"cap {a.max_requests}, sampling {px.sampling}, key {'from ' + a.key_file if key else 'none'})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


MANIFEST = """apiVersion: v1
kind: ConfigMap
metadata: {{name: agentbench-proxy, namespace: {ns}}}
data:
{files}
---
apiVersion: apps/v1
kind: Deployment
metadata: {{name: agentbench-proxy, namespace: {ns}, labels: {{app: agentbench-proxy}}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: agentbench-proxy}}}}
  template:
    metadata: {{labels: {{app: agentbench-proxy}}}}
    spec:
      containers:
        - name: proxy
          image: {image}
          command: ["python3", "-u", "/app/eatflow/agentbench.py", "proxy", "--host", "0.0.0.0",
                    "--port", "8080", "--upstream", "{upstream}", "--log", "-"{cap}{sampling}]
          ports: [{{containerPort: 8080}}]
          volumeMounts:
            - {{name: src, mountPath: /app/eatflow/agentbench.py, subPath: agentbench.py}}
            - {{name: src, mountPath: /app/eatflow/proxy.py, subPath: proxy.py}}
            - {{name: src, mountPath: /app/eatflow/tasks.py, subPath: tasks.py}}
            - {{name: src, mountPath: /app/eatflow/stats.py, subPath: stats.py}}
            - {{name: src, mountPath: /app/eatflow/energy.py, subPath: energy.py}}
            - {{name: src, mountPath: /app/eatflow/bfcl.py, subPath: bfcl.py}}
            - {{name: src, mountPath: /app/eatflow/agent.lock, subPath: agent.lock}}
            - {{name: src, mountPath: /app/agent_trace.py, subPath: agent_trace.py}}
      volumes: [{{name: src, configMap: {{name: agentbench-proxy}}}}]
---
apiVersion: v1
kind: Service
metadata: {{name: agentbench-proxy, namespace: {ns}}}
spec:
  selector: {{app: agentbench-proxy}}
  ports: [{{port: 8080, targetPort: 8080}}]
"""


def manifest(upstream, ns, max_requests=None, sampling=None):
    """sampling: a profile name from agent.lock, or a dict (passed to the proxy as JSON)."""
    names = ["agentbench.py", "proxy.py", "tasks.py", "stats.py", "energy.py", "bfcl.py", "agent.lock"]
    paths = {n: os.path.join(HERE, n) for n in names}
    paths["agent_trace.py"] = os.path.join(HERE, "..", "agent_trace.py")
    files = []
    for n, p in paths.items():
        with open(p) as f:
            body = f.read()
        files.append(f"  {n}: |\n" + "".join(f"    {ln}\n" if ln else "\n" for ln in body.split("\n")))
    cap = f', "--max-requests", "{max_requests}"' if max_requests is not None else ""
    spec = json.dumps(sampling) if isinstance(sampling, dict) else sampling
    samp = f', "--sampling", {json.dumps(spec)}' if spec else ""
    return MANIFEST.format(ns=ns, files="".join(files), image=lock()["images"]["proxy_python"]["ref"],
                           upstream=upstream, cap=cap, sampling=samp)


def cmd_manifest(a):
    sampling_set(a.sampling)
    sys.stdout.write(manifest(a.upstream, a.namespace, a.max_requests, a.sampling))
    return 0


def _block_tools(a, tag, load_file="load.jsonl"):
    os.makedirs(a.out, exist_ok=True)
    sampler = None if a.no_load_sample else LoadSampler(os.path.join(a.out, load_file), tag, a.level, a.sample_s)
    meter = None if a.no_energy else energy.Meter(gpu=a.gpu_power)
    return sampler, meter


def _proxy_log(a):
    """The proxy log for this block: --proxy-log, or an in-process proxy with --upstream."""
    if a.upstream:
        path = os.path.join(a.out, "proxy.jsonl")
        px = proxy.Proxy(a.upstream, path, max_requests=a.max_requests, sampling=sampling_set(a.sampling))
        srv = proxy.serve(px, "127.0.0.1", a.proxy_port)
        log(f"in-process proxy on 127.0.0.1:{srv.server_address[1]} -> {a.upstream}")
        return path, srv
    return a.proxy_log, None


def cmd_run(a):
    os.makedirs(a.out, exist_ok=True)
    write_facts(a.out, a)
    plog, srv = _proxy_log(a)
    if not plog:
        raise SystemExit("agentbench: --proxy-log FILE (in-cluster proxy) or --upstream URL is required")
    agent = Agent(a.agent_url, os.environ.get("DEVUI_AUTH_TOKEN"), a.entity)
    sampler, meter = _block_tools(a, a.arm)
    ts = T.taskset(a.tasks)
    if a.ids:
        want = a.ids.split(",")
        unknown = sorted(set(want) - {t["id"] for t in ts})
        if unknown:
            raise SystemExit(f"agentbench: unknown task id(s) in {a.tasks}: {unknown}")
        ts = [t for t in ts if t["id"] in want]
    if a.limit:
        ts = ts[:a.limit]
    rc = 0
    try:
        rows = run_block(agent, ts, a.arm, a.level, a.concurrency, a.out, a.budget_s, plog, a.mcp_url, a.reset,
                         cycle=a.cycle, task_timeout=a.task_timeout_s, sampler=sampler, meter=meter,
                         warmup=None if a.no_warmup else warmup_prompt(ts), settle_s=a.settle_s)
    except BlockAborted as e:
        rows, rc = e.rows, 1
        log(f"block ABORTED: {e.reason}")
    if srv:
        srv.shutdown()
    record_turn_facts(a.out, a.arm, a.level, a.concurrency)
    k = sum(r["passed"] for r in rows)
    log(f"{a.arm} {a.level} N={a.concurrency}: {k}/{len(rows)} passed; results in {a.out}/results.jsonl")
    return rc


def record_turn_facts(out, arm, level, n):
    """Add what the proxy saw to facts.json: the sampling sets and the agent-memory check."""
    turns = [t for t in read_jsonl(os.path.join(out, "turns.jsonl"))
             if (t.get("arm"), t.get("level"), t.get("n")) == (arm, level, n)]
    users = [t.get("user_msgs") for t in turns]
    if users and all(u == 1 for u in users):
        mem = "off: every attributed LLM request carried one user message"
    elif any(u is not None and u > 1 for u in users):
        mem = "ON: a request carried more than one user message"
    else:
        mem = "unverified: no attributed request recorded a user-message count"
    p = os.path.join(out, "facts.json")
    with open(p) as f:
        facts = json.load(f)
    facts.update(sampling_seen=unique([t.get("sampling") for t in turns]), memory=mem, turns=len(turns))
    with open(p, "w") as f:
        json.dump(facts, f, indent=1, default=str)


def cmd_control(a):
    if a.no_energy:
        raise SystemExit("agentbench: control measures the energy baseline; --no-energy makes it meaningless")
    os.makedirs(a.out, exist_ok=True)
    sampler, meter = _block_tools(a, f"{a.arm}-control", "control-load.jsonl")
    if sampler:
        sampler.start()
    meter.start()
    time.sleep(a.seconds)
    e = meter.stop()
    if sampler:
        sampler.stop()
    with open(os.path.join(a.out, "control.jsonl"), "a") as f:
        f.write(json.dumps({"arm": a.arm, "level": a.level, "energy": e}) + "\n")
    log(f"control {a.arm} {a.level}: mean {e.get('mean_w')} W over {e['seconds']} s")
    return 0


def run_apb(agent, arm, level, out, step_s, proxy_log, ramp=RAMP, warmup_s=30.0,
            clock=time.time, settle_s=30.0, block=run_block, meter_factory=None, sampler_factory=None,
            warmup=None):
    steps, base = [], None
    for n in ramp:
        t0 = clock()
        rows = block(agent, T.B15_RO, arm, level, n, out, step_s, proxy_log, cycle=True, clock=clock,
                     settle_s=settle_s, step=f"apb-{n}", warmup=warmup if n == ramp[0] else None,
                     meter=meter_factory() if meter_factory else None,
                     sampler=sampler_factory() if sampler_factory else None)
        recs = window_requests(proxy.read_log(proxy_log), t0 + warmup_s)
        recs = [r for r in recs if (r.get("ts_send") or 0) <= clock()]
        if n == 1:
            done = [r for r in rows if not r["censored"]]
            base = sum(r["passed"] for r in done) / len(done) if done else None
        ok, checks = apb_gate(rows, recs, base)
        steps.append({"n": n, "pass": ok, "checks": checks})
        log(f"apb {arm} N={n}: {'pass' if ok else 'FAIL'} {checks}")
        if not ok:
            break
    res = {"arm": arm, "level": level, "slo_s": SLO_S, "apb": agents_per_box(steps), "steps": steps}
    with open(os.path.join(out, "apb.json"), "w") as f:
        json.dump(res, f, indent=1)
    return res



def cmd_apb(a):
    os.makedirs(a.out, exist_ok=True)
    write_facts(a.out, a)
    plog, srv = _proxy_log(a)
    if not plog:
        raise SystemExit("agentbench: --proxy-log FILE or --upstream URL is required")
    agent = Agent(a.agent_url, os.environ.get("DEVUI_AUTH_TOKEN"), a.entity)
    try:
        res = run_apb(agent, a.arm, a.level, a.out, a.step_s, plog, settle_s=a.settle_s,
                      meter_factory=None if a.no_energy else (lambda: energy.Meter(gpu=a.gpu_power)),
                      sampler_factory=None if a.no_load_sample else
                      (lambda: LoadSampler(os.path.join(a.out, "load.jsonl"), a.arm, a.level, a.sample_s)),
                      warmup=None if a.no_warmup else WARMUP["bank"])
    except BlockAborted as e:
        log(f"apb ABORTED: {e.reason}")
        return 1
    finally:
        if srv:
            srv.shutdown()
    print(f"agents per box ({a.arm}, {a.level}): {res['apb']}")
    return 0


def cmd_bfcl(a):
    os.makedirs(a.out, exist_ok=True)
    write_facts(a.out, a)
    if a.proxy_url:
        srv, url = None, a.proxy_url.rstrip("/")
    else:
        _, srv = _proxy_log(a)
        if not srv:
            raise SystemExit("agentbench: bfcl needs --upstream URL (the arm's LiteLLM route) or --proxy-url "
                             "(a running proxy); BFCL always goes through the proxy")
        url = "http://%s:%d" % srv.server_address[:2]
    sampler, meter = _block_tools(a, a.arm)
    if sampler:
        sampler.start()
    if meter:
        meter.start()
    r = bfcl.run(a.mode, os.path.join(a.out, "bfcl"), url, a.budget_s, served_model=a.served_model,
                 python=a.python, threads=a.threads, log=log, limit=a.limit)
    e = meter.stop() if meter else None
    if sampler:
        sampler.stop()
    if srv:
        srv.shutdown()
    res = {"arm": a.arm, "mode": a.mode, "level": a.level, "energy": e, **r}
    with open(os.path.join(a.out, "bfcl.json"), "w") as f:
        json.dump(res, f, indent=1)
    s = bfcl.summarize(r["passed"])
    print(f"bfcl {a.mode} {a.arm}: {s['n']} ids, accuracy {s['acc']}% (stopped: {r['stopped']})")
    return 0


def cmd_regrade(a):
    for d in a.runs:
        print(f"{d}: {regrade(d)} row(s) changed (originals in results.raw.jsonl)")
    return 0


def cmd_report(a):
    rep = build_report(a.runs, a.ref)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "report.json"), "w") as f:
        json.dump(rep, f, indent=1, default=lambda v: None)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write(render_md(rep))
    print(f"wrote {a.out}/report.json and {a.out}/report.md")
    return 0


def parse(argv):
    ap = argparse.ArgumentParser(prog="k3d-gpu bench agent", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tasks", help="list a task set")
    p.add_argument("set", choices=sorted(T.TASKSETS))
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("plan", help="pre-registered block plan of a hypothesis")
    p.add_argument("hypothesis")
    p.add_argument("--budget-s", type=float, default=ARM_BUDGET_S)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("proxy", help="run the recording proxy")
    p.add_argument("--upstream", required=True, help="LLM server root, e.g. http://litellm:4000")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--log", required=True, help="JSONL file, or - for stdout (kubectl logs)")
    p.add_argument("--max-requests", type=int, help="hard cap on forwarded POSTs (required with --key-file)")
    p.add_argument("--no-usage", action="store_true", help="do not add stream_options.include_usage")
    p.add_argument("--key-file", help="0600 KEY=VALUE file holding the upstream key (remote arm)")
    p.add_argument("--key-var", default="OPENAI_API_KEY", help="variable in --key-file holding the key")
    p.add_argument("--sampling", help="pinned sampling: a profile in agent.lock or a JSON object")
    p.set_defaults(func=cmd_proxy)

    p = sub.add_parser("manifest", help="in-cluster proxy manifest")
    p.add_argument("--upstream", required=True)
    p.add_argument("--namespace", default="agentbench")
    p.add_argument("--max-requests", type=int)
    p.add_argument("--sampling", default="agent_local", help="pinned sampling profile or JSON (default agent_local)")
    p.set_defaults(func=cmd_manifest)

    def block_args(p, budget=True):
        p.add_argument("--arm", required=True)
        p.add_argument("--level", required=True, choices=("none", "typical", "heavy"))
        p.add_argument("--out", required=True)
        p.add_argument("--sample-s", type=float, default=30.0, help="load sample window (s)")
        p.add_argument("--no-load-sample", action="store_true")
        p.add_argument("--no-energy", action="store_true")
        p.add_argument("--gpu-power", action="store_true", help="integrate nvidia-smi power.draw (dGPU arms)")
        if budget:
            p.add_argument("--budget-s", type=float, default=ARM_BUDGET_S)

    def llm_args(p, agent=True):
        p.add_argument("--proxy-log", help="JSONL of the in-cluster proxy (kubectl logs -f ... > FILE)")
        p.add_argument("--upstream", help="run the proxy in-process against this LLM server root")
        p.add_argument("--proxy-port", type=int, default=0)
        p.add_argument("--max-requests", type=int, help="hard LLM request cap (paid/remote arm)")
        if not agent:   # BFCL keeps its own sampling (temperature 0.001, as on the board)
            p.set_defaults(sampling=None)
            return
        p.add_argument("--sampling", default="agent_local",
                       help="sampling the in-process proxy pins (agent.lock profile or JSON)")
        p.add_argument("--no-warmup", action="store_true", help="skip the unscored warm-up prompt")
        p.add_argument("--settle-s", type=float, default=30.0,
                       help="longest wait for a task's final turn to reach the proxy log")

    p = sub.add_parser("run", help="one block")
    block_args(p)
    llm_args(p)
    p.add_argument("--agent-url", required=True, help="blueprint DevUI, e.g. http://127.0.0.1:8090 (token: DEVUI_AUTH_TOKEN)")
    p.add_argument("--entity", help="DevUI entity id (default: the first of /v1/entities)")
    p.add_argument("--tasks", required=True, choices=sorted(T.TASKSETS))
    p.add_argument("--limit", type=int, help="first N tasks of the set")
    p.add_argument("--ids", help="comma-separated task ids of the set, e.g. B-T1-U1001,B-T6-U1001")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--cycle", action="store_true", help="repeat the set until the budget ends")
    p.add_argument("--mcp-url", help="banking MCP endpoint for end-state grading, e.g. http://127.0.0.1:8000/mcp")
    p.add_argument("--reset", help="command that restarts the MCP sidecar after a mutating task")
    p.add_argument("--task-timeout-s", type=float, default=TASK_TIMEOUT_S)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("control", help="load-only control window")
    block_args(p, budget=False)
    p.add_argument("--seconds", type=float, default=120.0)
    p.set_defaults(func=cmd_control)

    p = sub.add_parser("apb", help="graded agents-per-box ramp on B15-RO")
    block_args(p, budget=False)
    llm_args(p)
    p.add_argument("--agent-url", required=True)
    p.add_argument("--entity")
    p.add_argument("--step-s", type=float, default=300.0)
    p.set_defaults(func=cmd_apb)

    p = sub.add_parser("bfcl", help="BFCL S100 through the proxy")
    block_args(p)
    llm_args(p, agent=False)
    p.add_argument("--mode", required=True, choices=sorted(bfcl.REGISTRY))
    p.add_argument("--served-model", help="model name the server-fc mode sends")
    p.add_argument("--python", default="python3", help="interpreter of the venv holding bfcl-eval==2025.12.17")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--limit", type=int, help="first N ids of S100's run order (H48: 8)")
    p.add_argument("--proxy-url", help="a running proxy (e.g. one shared request cap) instead of --upstream")
    p.set_defaults(func=cmd_bfcl)

    p = sub.add_parser("regrade", help="apply the current graders to recorded blocks")
    p.add_argument("runs", nargs="+")
    p.set_defaults(func=cmd_regrade)

    p = sub.add_parser("report", help="report.json + report.md")
    p.add_argument("runs", nargs="+")
    p.add_argument("--ref", help="reference arm for paired comparisons")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_report)
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())

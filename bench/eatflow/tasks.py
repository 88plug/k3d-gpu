"""Task sets and deterministic graders for the agent benchmark (stdlib only).

W-BANK: intel/enterprise-ai-blueprints usecases/banking-assistant (v1.0.0 @ 758d484).
  B24 = 3 users x 8 templates. Expected values are computed from the blueprint's
  seed data (src/mcp_server/data.py, sha256 pinned in agent.lock); the subset this
  grader needs is copied below and must match that file.
  B15-RO = templates T1..T5 (no write tool involved), the closed-loop concurrency set.
W-CODE: usecases/coding-agent-maf. C12 = 12 tasks with one exact answer each.

A grader sees only the final answer text, the tool calls the recording proxy saw
for the task, and (mutating banking tasks) the MCP server's end state. No judge
model. Each grader returns (passed: bool, reason: str).
"""
import json
import re

BLUEPRINTS = "intel/enterprise-ai-blueprints@758d4845520ca5456bf732264ef02d92a26d25a6"

# ---- seed subset (copied from src/mcp_server/data.py at BLUEPRINTS) ------------------
USERS = {
    "U1001": {"name": "Ada Lovelace", "email": "ada.lovelace@example.com"},
    "U1002": {"name": "Alan Turing", "email": "alan.turing@example.com"},
    "U1003": {"name": "Grace Hopper", "email": "grace.hopper@example.com"},
}
ACCOUNTS = {  # account_id: (user_id, type)
    "ACC-1001-CHK": ("U1001", "checking"), "ACC-1001-SAV": ("U1001", "savings"),
    "ACC-1002-CHK": ("U1002", "checking"), "ACC-1002-SAV": ("U1002", "savings"),
    "ACC-1002-CRD": ("U1002", "credit"), "ACC-1003-CHK": ("U1003", "checking"),
}
AVAILABLE = {
    "ACC-1001-CHK": 4250.75, "ACC-1001-SAV": 18200.00, "ACC-1002-CHK": 980.10,
    "ACC-1002-SAV": 5600.00, "ACC-1002-CRD": 3200.00, "ACC-1003-CHK": 12750.42,
}
OWED = {"ACC-1002-CRD": 1800.00}  # credit current_balance (amount owed), data.py _SEED_BALANCES
TRANSACTIONS = [  # (account_id, date, amount)
    ("ACC-1001-CHK", "2026-07-15", -52.30), ("ACC-1001-CHK", "2026-07-14", -12.99),
    ("ACC-1001-CHK", "2026-07-12", 3200.00), ("ACC-1001-CHK", "2026-07-10", -88.40),
    ("ACC-1001-CHK", "2026-07-08", -25.00), ("ACC-1001-SAV", "2026-07-01", 500.00),
    ("ACC-1001-SAV", "2026-06-01", 500.00), ("ACC-1002-CHK", "2026-07-16", -140.00),
    ("ACC-1002-CHK", "2026-07-13", -63.75), ("ACC-1002-CHK", "2026-07-11", 2100.00),
    ("ACC-1002-CRD", "2026-07-15", -220.00), ("ACC-1002-CRD", "2026-07-09", -45.60),
    ("ACC-1003-CHK", "2026-07-16", 4800.00), ("ACC-1003-CHK", "2026-07-12", -310.00),
]
LOANS = {  # loan_id: (user_id, type, principal_remaining, next_due_date)
    "LN-2001": ("U1001", "mortgage", 268400.55, "2026-08-01"),
    "LN-2002": ("U1002", "auto", 9120.30, "2026-08-05"),
    "LN-2003": ("U1002", "personal", 4300.00, "2026-08-10"),
}


def accounts_of(user):
    return [a for a, (u, _) in ACCOUNTS.items() if u == user]


def deposit_total(user):
    return round(sum(AVAILABLE[a] for a in accounts_of(user)
                     if ACCOUNTS[a][1] in ("checking", "savings")), 2)


def recent(account, k):
    return sorted((t for t in TRANSACTIONS if t[0] == account), key=lambda t: t[1], reverse=True)[:k]


# ---- text helpers -------------------------------------------------------------------
NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers(text):
    """Every number in the text, commas and currency signs ignored."""
    out = []
    for m in NUM_RE.findall((text or "").replace("$", "")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def has_amount(text, value):
    """True when |value| appears in the text as a number (to the cent). A tuple of values
    means any one of them (a credit account: available credit or amount owed)."""
    vals = value if isinstance(value, tuple) else (value,)
    nums = numbers(text)
    return any(abs(abs(x) - round(abs(v), 2)) < 0.005 for v in vals for x in nums)


def balance_answer(account):
    """Accepted amounts for one account's balance."""
    return (AVAILABLE[account], OWED[account]) if account in OWED else AVAILABLE[account]


def has_token(text, token):
    return re.search(r"(?<![\w.])" + re.escape(str(token)) + r"(?![\w])", text or "") is not None


def in_order(text, values):
    """The integers appear in this order (other numbers may sit between them)."""
    nums = [int(x) for x in numbers(text) if x == int(x)]
    i = 0
    for n in nums:
        if i < len(values) and n == values[i]:
            i += 1
    return i == len(values)


def tool_matches(called, wanted):
    """A recorded tool name matches a blueprint tool name, allowing a namespace prefix."""
    return called == wanted or called.endswith("_" + wanted) or called.endswith("." + wanted)


def calls_named(calls, wanted):
    return [c for c in calls if tool_matches(c.get("name") or "", wanted)]


# A refusal must cite the reason (funds), not just decline: "I cannot access accounts" fails.
INSUFFICIENT_RE = re.compile(r"insufficient|(?:not|n't) (?:have )?(?:enough|sufficient)|exceed|overdra|"
                             r"more than (?:the |your )?(?:available|balance)", re.I)
NO_LOAN_RE = re.compile(r"\b(no|not have (?:any|an?)|doesn't have (?:any|an?)|does not have (?:any|an?)|"
                        r"don't have (?:any|an?)|do not have (?:any|an?))\b[^.]*\bloans?\b", re.I)


# ---- B24 ----------------------------------------------------------------------------
def _bank_tasks():
    tasks = []
    chk = {"U1001": "ACC-1001-CHK", "U1002": "ACC-1002-CHK", "U1003": "ACC-1003-CHK"}
    for u in ("U1001", "U1002", "U1003"):
        accts = accounts_of(u)
        # T1 all balances (README prompt 1 for U1001)
        tasks.append({"id": f"B-T1-{u}", "template": "T1", "user": u, "mutating": False,
                      "prompt": f"My user id is {u}. What are the balances across all my accounts?",
                      "tools": ["get_balances"], "amounts": [balance_answer(a) for a in accts]})
        # T2 one account balance
        tasks.append({"id": f"B-T2-{u}", "template": "T2", "user": u, "mutating": False,
                      "prompt": f"What is the available balance on account {chk[u]}?",
                      "tools": ["get_account_balance|get_balances"], "amounts": [AVAILABLE[chk[u]]]})
        # T3 last k transactions (README prompt 2 for U1001)
        k = min(5, len(recent(chk[u], 99)))
        tasks.append({"id": f"B-T3-{u}", "template": "T3", "user": u, "mutating": False,
                      "prompt": f"Show me the {k} most recent transactions on account {chk[u]}.",
                      "tools": ["get_transactions"], "amounts": [t[2] for t in recent(chk[u], k)]})
        # T4 loan next-due and remaining (README prompt 3 for U1001)
        if u == "U1001":
            p = "For user U1001, when is the next mortgage payment due and how much is left on it?"
            lo = LOANS["LN-2001"]
            t4 = {"amounts": [lo[2]], "dates": [lo[3]]}
        elif u == "U1002":
            p = "For user U1002, when is the next auto loan payment due and how much is left on it?"
            lo = LOANS["LN-2002"]
            t4 = {"amounts": [lo[2]], "dates": [lo[3]]}
        else:
            p = "For user U1003, when is the next loan payment due and how much is left on it?"
            t4 = {"amounts": [], "dates": [], "no_loan": True}
        tasks.append({"id": f"B-T4-{u}", "template": "T4", "user": u, "mutating": False,
                      "prompt": p, "tools": ["get_loans|get_loan_summary"], **t4})
        # T5 find_user by email, then total deposits
        tasks.append({"id": f"B-T5-{u}", "template": "T5", "user": u, "mutating": False,
                      "prompt": (f"My email is {USERS[u]['email']}. What is the total available balance "
                                 "across my checking and savings accounts?"),
                      "tools": ["find_user", "get_balances|get_account_balance"], "amounts": [deposit_total(u)]})
        # T6 valid transfer (README prompt 4 for U1001)
        src, dst, amt = {"U1001": ("ACC-1001-CHK", "ACC-1001-SAV", 200.0),
                         "U1002": ("ACC-1002-CHK", "ACC-1002-SAV", 100.0),
                         "U1003": ("ACC-1003-CHK", "ACC-1002-SAV", 300.0)}[u]
        tasks.append({"id": f"B-T6-{u}", "template": "T6", "user": u, "mutating": True,
                      "prompt": f"I'm user {u}. Transfer ${amt:.0f} from {src} to {dst}. I confirm.",
                      "tools": ["transfer_funds"],
                      "end_state": {"balances": {src: round(AVAILABLE[src] - amt, 2),
                                                 dst: round(AVAILABLE[dst] + amt, 2)}}})
        # T7 loan payment (U1003 holds no loan: the end state must stay at seed)
        if u == "U1003":
            tasks.append({"id": f"B-T7-{u}", "template": "T7", "user": u, "mutating": True,
                          "prompt": "I'm user U1003. Pay $100 toward my auto loan from ACC-1003-CHK. I confirm.",
                          "tools": ["get_loans|get_loan_summary"], "no_loan": True,
                          "end_state": {"balances": {"ACC-1003-CHK": AVAILABLE["ACC-1003-CHK"]},
                                        "loans": {lid: lo[2] for lid, lo in LOANS.items()}}})
        else:
            lid, src, amt = {"U1001": ("LN-2001", "ACC-1001-CHK", 500.0),
                             "U1002": ("LN-2002", "ACC-1002-CHK", 531.0)}[u]
            tasks.append({"id": f"B-T7-{u}", "template": "T7", "user": u, "mutating": True,
                          "prompt": f"I'm user {u}. Pay ${amt:.0f} toward loan {lid} from {src}. I confirm.",
                          "tools": ["pay_loan"],
                          "end_state": {"balances": {src: round(AVAILABLE[src] - amt, 2)},
                                        "loans": {lid: round(LOANS[lid][2] - amt, 2)}}})
        # T8 overdraft transfer that must be refused (state checked when an MCP URL is given)
        src, dst, amt = {"U1001": ("ACC-1001-CHK", "ACC-1001-SAV", 5000.0),
                         "U1002": ("ACC-1002-CHK", "ACC-1002-SAV", 2000.0),
                         "U1003": ("ACC-1003-CHK", "ACC-1002-SAV", 20000.0)}[u]
        tasks.append({"id": f"B-T8-{u}", "template": "T8", "user": u, "mutating": False, "refuse": True,
                      "prompt": f"I'm user {u}. Transfer ${amt:.0f} from {src} to {dst}. I confirm.",
                      "tools": [], "source": src,
                      "end_state": {"balances": {src: AVAILABLE[src], dst: AVAILABLE[dst]}}})
    return tasks


B24 = _bank_tasks()
B15_RO = [t for t in B24 if t["template"] in ("T1", "T2", "T3", "T4", "T5")]

# ---- C12 ----------------------------------------------------------------------------
C12 = [
    {"id": "C01", "prompt": "Compute the sum of all prime numbers below 50, run it in the sandbox, and print the result.",
     "expect": ["328"]},  # README example prompt, verbatim
    {"id": "C02", "prompt": "Write a Python function for the nth Fibonacci number, run it, and print the 15th.",
     "expect": ["610"]},  # README example prompt, verbatim (F1 = F2 = 1)
    {"id": "C03", "prompt": "Install the 'sympy' package in the sandbox and list the first 10 primes with it.",
     "sequence": [2, 3, 5, 7, 11, 13, 17, 19, 23, 29], "absent": ["31"], "install": True},  # README, verbatim
    {"id": "C04", "prompt": "Run Python in the sandbox to count the prime numbers below 1000 and print the count.",
     "expect": ["168"]},
    {"id": "C05", "prompt": "Run Python in the sandbox to compute the sum of the decimal digits of 2**100 and print it.",
     "expect": ["115"]},
    {"id": "C06", "prompt": "Run Python in the sandbox to compute 15 factorial and print the exact integer.",
     "expect": ["1307674368000"]},
    {"id": "C07", "prompt": "Run Python in the sandbox to compute the greatest common divisor of 1071 and 462 and print it.",
     "expect": ["21"]},
    {"id": "C08", "prompt": "Run Python in the sandbox to compute the sum of the squares of the integers 1 to 100 and print it.",
     "expect": ["338350"]},
    {"id": "C09", "prompt": ("Run Python in the sandbox to count the vowels (a, e, i, o, u) in the string "
                             "'the quick brown fox jumps over the lazy dog' and print the count."),
     "expect": ["11"]},
    {"id": "C10", "prompt": ("Run Python in the sandbox to print the first 12 hex characters of the SHA-256 "
                             "digest of the UTF-8 string 'k3d-gpu'."),
     "expect": ["579f2fcf83d6"]},
    {"id": "C11", "prompt": "Install the 'sympy' package in the sandbox, then use sympy.prime to print the 100th prime.",
     "expect": ["541"], "install": True},
    {"id": "C12", "prompt": ("Install the 'sympy' package in the sandbox, then use sympy.divisor_count to print "
                             "the number of divisors of 720720."),
     "expect": ["240"], "install": True},
]

TASKSETS = {"B24": B24, "B15-RO": B15_RO, "C12": C12}


def taskset(name):
    if name not in TASKSETS:
        raise SystemExit(f"unknown task set {name!r} (use {', '.join(TASKSETS)})")
    return TASKSETS[name]


# ---- graders ------------------------------------------------------------------------
def _need_tools(task, calls):
    """Every required tool (alternatives split by '|') appears with valid args."""
    for spec in task.get("tools", []):
        alts = spec.split("|")
        ok = [c for a in alts for c in calls_named(calls, a) if c.get("valid")]
        if not ok:
            return f"no valid call to {spec}"
    return None


def grade_bank(task, answer, calls, state=None, evidence=None):
    """state: {"balances": {acct: available}, "loans": {loan: remaining}} read back from
    the MCP server after the task, or None when no MCP URL was given."""
    del evidence
    miss = _need_tools(task, calls)
    if miss:
        return False, miss
    if task.get("refuse"):
        if not checked_source(calls, task["source"]):
            return False, f"no valid balance read of {task['source']} and no transfer_funds attempt"
        if not INSUFFICIENT_RE.search(answer or ""):
            return False, "answer does not refuse for insufficient funds"
    if task.get("no_loan") and not NO_LOAN_RE.search(answer or ""):
        return False, "answer does not say the user holds no loan"
    for v in task.get("amounts", []):
        if not has_amount(answer, v):
            shown = " or ".join(f"{x:.2f}" for x in (v if isinstance(v, tuple) else (v,)))
            return False, f"amount {shown} missing from the answer"
    for d in task.get("dates", []):
        y, m, day = d.split("-")
        months = ["January", "February", "March", "April", "May", "June", "July", "August",
                  "September", "October", "November", "December"]
        mon = months[int(m) - 1]
        pats = [d, f"{mon} {int(day)}", f"{int(day)} {mon}", f"{mon[:3]} {int(day)}", f"{int(m)}/{int(day)}"]
        if not any(re.search(r"(?<!\d)" + re.escape(p) + r"(?!\d)", answer or "", re.I) for p in pats):
            return False, f"due date {d} missing from the answer"
    want = task.get("end_state")
    if want and (task.get("mutating") or (task.get("refuse") and state is not None)):
        if state is None:
            return False, "end state unverified (no --mcp-url)"
        for acct, v in want.get("balances", {}).items():
            got = state.get("balances", {}).get(acct)
            if got is None or abs(got - v) >= 0.005:
                return False, f"{acct} available {got} != {v:.2f}"
        for lid, v in want.get("loans", {}).items():
            got = state.get("loans", {}).get(lid)
            if got is None or abs(got - v) >= 0.005:
                return False, f"{lid} remaining {got} != {v:.2f}"
    return True, "ok"


def checked_source(calls, src):
    """T8 evidence: a transfer attempt (the server rejects it), get_balances, or a balance
    read of the source account."""
    if [c for c in calls_named(calls, "transfer_funds") + calls_named(calls, "get_balances") if c.get("valid")]:
        return True
    for c in calls_named(calls, "get_account_balance"):
        try:
            args = json.loads(c.get("arguments") or "{}")
        except ValueError:
            continue
        if c.get("valid") and isinstance(args, dict) and args.get("account_id") == src:
            return True
    return False


def grade_code(task, answer, calls, state=None, evidence=None):
    """evidence: text of the tool results the agent received (role=tool messages the proxy
    saw). When given, each expected token must also appear there, so an answer from
    memory after a failed run does not pass."""
    del state
    if not [c for c in calls_named(calls, "execute_python_code") if c.get("valid")]:
        return False, "no valid execute_python_code call"
    if task.get("install") and not [c for c in calls_named(calls, "install_package") if c.get("valid")]:
        return False, "no valid install_package call"
    for tok in task.get("expect", []):
        if not has_token(answer, tok):
            return False, f"{tok} missing from the answer"
        if evidence is not None and not has_token(evidence, tok):
            return False, f"{tok} not in the sandbox tool output"
    if task.get("sequence") and not in_order(answer, task["sequence"]):
        return False, "expected sequence missing from the answer"
    if task.get("sequence") and evidence is not None and not in_order(evidence, task["sequence"]):
        return False, "expected sequence not in the sandbox tool output"
    for tok in task.get("absent", []):
        if has_token(answer, tok):
            return False, f"{tok} in the answer: more than was asked for"
    return True, "ok"


def grader_for(task):
    return grade_code if task["id"].startswith("C") else grade_bank


def end_state_accounts(task):
    w = task.get("end_state") or {}
    return sorted(w.get("balances", {})), sorted(w.get("loans", {}))

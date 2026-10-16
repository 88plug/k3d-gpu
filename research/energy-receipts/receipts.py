"""Per-call joule receipts, a W3C-baggage joule budget, and receipt-driven variant steering
for MCP tools/call.

Standard library only. Three parts:

  receipt   After a tools/call, ask a ledger for the energy the tool's cgroup used over the
            call's [start, end] window and put it in the result's _meta under RECEIPT_KEY.
            Every domain is tagged "measured" or "modelled" and the receipt carries a
            1-sigma error and a confidence in [0, 1].

  budget    The request's _meta "baggage" value (reserved by MCP 2026-07-28 for W3C Baggage)
            may carry k3dgpu.joule_budget and k3dgpu.joules_spent. The middleware adds the
            receipt's joules to joules_spent and returns the updated baggage in the result's
            _meta, and exposes it through current_baggage() for nested outgoing calls.

  steering  Steerer keeps per-(tool, variant) EWMAs of joules and latency learned from
            receipts and picks the variant (P balloon, E balloon, iGPU, NVIDIA) to run.

The ledger is an interface (Ledger.window). MockLedger serves tests and the simulator. A real
ledger needs root for RAPL energy_uj (mode 0400) and CAP_PERFMON for cpu_core/cpu_atom perf
counters, so it runs as a privileged DaemonSet; it is not part of this module.
"""

from __future__ import annotations

import contextvars
import math
import random
import time
import urllib.parse
from dataclasses import dataclass, field

RECEIPT_KEY = "dev.k3dgpu/energy"  # reverse-DNS prefix, as MCP 2026-07-28 _meta rules recommend
BAGGAGE_KEY = "baggage"            # reserved MCP _meta key, W3C Baggage format
BUDGET_KEY = "k3dgpu.joule_budget"
SPENT_KEY = "k3dgpu.joules_spent"
VARIANT_KEY = "dev.k3dgpu/variant"  # optional client override of the steered variant

RECEIPT_DOMAINS = ("cpu_p", "cpu_e", "uncore_igpu", "nvidia0", "nvidia1", "residual")


# --------------------------------------------------------------------------- baggage

@dataclass
class BaggageMember:
    key: str
    value: str
    properties: list[str] = field(default_factory=list)


def parse_baggage(header: str | None) -> list[BaggageMember]:
    """Parse a W3C baggage-string. Malformed members are skipped, not fatal."""
    members: list[BaggageMember] = []
    if not header:
        return members
    for raw in header.split(","):
        parts = [p.strip() for p in raw.split(";")]
        if not parts[0] or "=" not in parts[0]:
            continue
        key, _, value = parts[0].partition("=")
        key = key.strip()
        if not key:
            continue
        members.append(BaggageMember(key, urllib.parse.unquote(value.strip()),
                                     [p for p in parts[1:] if p]))
    return members


def format_baggage(members: list[BaggageMember]) -> str:
    out = []
    for m in members:
        item = f"{m.key}={urllib.parse.quote(m.value, safe='')}"
        if m.properties:
            item += ";" + ";".join(m.properties)
        out.append(item)
    return ",".join(out)


def _get(members: list[BaggageMember], key: str) -> float | None:
    for m in members:
        if m.key == key:
            try:
                v = float(m.value)
            except ValueError:
                return None
            return v if math.isfinite(v) else None
    return None


def _set(members: list[BaggageMember], key: str, value: str) -> None:
    for m in members:
        if m.key == key:
            m.value = value
            return
    members.append(BaggageMember(key, value))


@dataclass
class JouleBudget:
    budget: float | None   # None = no budget declared
    spent: float

    @property
    def remaining(self) -> float | None:
        return None if self.budget is None else self.budget - self.spent


def read_budget(meta: dict | None) -> tuple[list[BaggageMember], JouleBudget]:
    members = parse_baggage((meta or {}).get(BAGGAGE_KEY))
    budget = _get(members, BUDGET_KEY)
    if budget is not None and budget < 0:
        budget = None
    spent = _get(members, SPENT_KEY) or 0.0
    return members, JouleBudget(budget, max(0.0, spent))


def charge(members: list[BaggageMember], jb: JouleBudget, joules: float) -> str:
    """Add joules to joules_spent; return the updated baggage-string.

    Only touches baggage that already carries a budget or a spent counter, so a request
    without one round-trips its baggage unchanged.
    """
    jb.spent += max(0.0, joules)
    if jb.budget is not None or _get(members, SPENT_KEY) is not None:
        _set(members, SPENT_KEY, f"{jb.spent:.3f}")
    return format_baggage(members)


# --------------------------------------------------------------------------- ledger

@dataclass
class DomainReading:
    joules: float
    sigma: float        # 1-sigma error, joules
    source: str         # "measured" | "modelled"


@dataclass
class LedgerReading:
    t0_ns: int
    t1_ns: int
    domains: dict[str, DomainReading]
    device: str = "cpu"           # where the call ran: cpu | igpu | nvidia0 | nvidia1
    core_type_mix: dict[str, float] = field(default_factory=dict)  # cycle share, P vs E


class Ledger:
    def window(self, cgroup: str, t0_ns: int, t1_ns: int) -> LedgerReading:
        raise NotImplementedError


def apportion_sigma(host_watts: float, bg_sigma_watts: float, window_s: float,
                    update_period_s: float = 0.001) -> float:
    """1-sigma error of a cgroup's share of a host energy counter over one window.

    Two terms: background power that cannot be told apart from the tool's own (sigma_bg x T,
    correlated over the window, so it scales with T, not sqrt(T)), and the counter's update
    granularity at each window edge (uniform over one update period: P*dt/sqrt(12), twice).
    """
    bg = bg_sigma_watts * window_s
    edge = math.sqrt(2.0) * host_watts * update_period_s / math.sqrt(12.0)
    return math.hypot(bg, edge)


def confidence(joules: float, sigma: float) -> float:
    """1 / (1 + relative error); 1.0 = exact, 0.5 = error as large as the signal."""
    if sigma <= 0:
        return 1.0
    rel = sigma / max(abs(joules), 1e-9)
    return 1.0 / (1.0 + rel)


def build_receipt(reading: LedgerReading) -> dict:
    by_domain = {d: 0.0 for d in RECEIPT_DOMAINS}
    sources = {}
    var = 0.0
    for name, dr in reading.domains.items():
        key = name if name in by_domain else "residual"
        by_domain[key] += dr.joules
        sources[key] = "modelled" if "modelled" in (dr.source, sources.get(key)) else dr.source
        var += dr.sigma ** 2
    total = sum(by_domain.values())
    sigma = math.sqrt(var)
    return {
        "joules_total": round(total, 6),
        "sigma_joules": round(sigma, 6),
        "confidence": round(confidence(total, sigma), 4),
        "by_domain": {k: round(v, 6) for k, v in by_domain.items()},
        "sources": sources,
        "device": reading.device,
        "core_type_mix": reading.core_type_mix,
        "window_ns": [reading.t0_ns, reading.t1_ns],
    }


class MockLedger(Ledger):
    """Returns preset readings, or a constant-power model when none is queued.

    Noise is drawn only when bg_sigma_watts > 0; sigma is always reported.
    """

    def __init__(self, watts: float = 5.0, bg_sigma_watts: float = 0.0,
                 host_watts: float = 40.0, device: str = "cpu", rng: random.Random | None = None):
        self.watts = watts
        self.bg_sigma_watts = bg_sigma_watts
        self.host_watts = host_watts
        self.device = device
        self.rng = rng or random.Random(0)
        self.queue: list[LedgerReading] = []
        self.calls: list[tuple[str, int, int]] = []

    def window(self, cgroup, t0_ns, t1_ns):
        self.calls.append((cgroup, t0_ns, t1_ns))
        if self.queue:
            return self.queue.pop(0)
        t = max(0, t1_ns - t0_ns) / 1e9
        sigma = apportion_sigma(self.host_watts, self.bg_sigma_watts, t)
        j = self.watts * t + (self.rng.gauss(0, sigma) if self.bg_sigma_watts else 0.0)
        return LedgerReading(t0_ns, t1_ns,
                             {"cpu_p": DomainReading(max(0.0, j), sigma, "modelled")},
                             device=self.device, core_type_mix={"p": 1.0, "e": 0.0})


# --------------------------------------------------------------------------- steering

@dataclass
class _Stat:
    joules: float = 0.0
    latency: float = 0.0
    n_j: int = 0
    n_l: int = 0
    last: float = -math.inf   # clock() of the last observation


class Steerer:
    """Pick a tool variant from receipt-learned EWMAs.

    cost(v) = joules EWMA, plus idle_watts x latency EWMA when cost_mode == "total" (the
    host burns idle power for as long as the call runs, so a slow, frugal variant can cost
    more in total than a fast one; this is race-to-idle).
    Feasible = latency EWMA <= (1 + latency_slack) x best known latency.
    Budget pressure (remaining < projected) = ignore the latency SLO and take min cost.
    Cold start and ties go to the default variant. epsilon explores outside budget pressure.
    Joule samples with confidence < min_confidence are dropped when gate is on (latency is
    always exact and always used).
    reprobe_s (off by default): outside budget pressure, a variant not observed for
    reprobe_s seconds is run once to refresh its estimate. Plain EWMA + epsilon only revisits
    a variant that looked bad with probability epsilon / len(variants) per call, so it is slow
    to notice that a contended device became free again.
    """

    def __init__(self, variants: dict[str, list[str]], defaults: dict[str, str], *,
                 alpha: float = 0.2, epsilon: float = 0.05, latency_slack: float = 0.10,
                 idle_watts: float = 0.0, cost_mode: str = "total", gate: bool = True,
                 min_confidence: float = 0.5, rng: random.Random | None = None,
                 reprobe_s: float | None = None, clock=time.monotonic):
        self.variants = variants
        self.defaults = defaults
        self.alpha = alpha
        self.epsilon = epsilon
        self.slack = latency_slack
        self.idle_watts = idle_watts
        self.cost_mode = cost_mode
        self.gate = gate
        self.min_conf = min_confidence
        self.rng = rng or random.Random(0)
        self.reprobe_s = reprobe_s
        self.clock = clock
        self.stats: dict[tuple[str, str], _Stat] = {}

    def _stat(self, tool, v):
        return self.stats.setdefault((tool, v), _Stat())

    def cost(self, tool, v) -> float:
        s = self._stat(tool, v)
        c = s.joules
        if self.cost_mode == "total":
            c += self.idle_watts * s.latency
        return c

    def choose(self, tool: str, pressure: bool = False) -> str:
        vs = self.variants[tool]
        default = self.defaults[tool]
        if len(vs) == 1:
            return vs[0]
        if not pressure and self.rng.random() < self.epsilon:
            return self.rng.choice(vs)
        known = [v for v in vs if self._stat(tool, v).n_j and self._stat(tool, v).n_l]
        if default not in known:
            return default
        if self.reprobe_s is not None and not pressure:
            now = self.clock()
            stale = [v for v in vs if now - self._stat(tool, v).last > self.reprobe_s]
            if stale:
                return min(stale, key=lambda v: self._stat(tool, v).last)
        if pressure:
            pool = known
        else:
            best = min(self._stat(tool, v).latency for v in known)
            pool = [v for v in known if self._stat(tool, v).latency <= (1 + self.slack) * best]
        best_cost = min(self.cost(tool, v) for v in pool)
        if self.cost(tool, default) <= best_cost * (1 + 1e-9) and default in pool:
            return default
        return min(pool, key=lambda v: self.cost(tool, v))

    def observe(self, tool: str, v: str, joules: float, latency_s: float, conf: float) -> None:
        s = self._stat(tool, v)
        a = self.alpha
        s.latency = latency_s if s.n_l == 0 else (1 - a) * s.latency + a * latency_s
        s.n_l += 1
        s.last = self.clock()
        if self.gate and conf < self.min_conf:
            return
        s.joules = joules if s.n_j == 0 else (1 - a) * s.joules + a * joules
        s.n_j += 1


# --------------------------------------------------------------------------- middleware

_current_baggage: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "k3dgpu_baggage", default=None)


def current_baggage() -> str | None:
    """Baggage to put in _meta of any nested outgoing call made while a tool runs.

    Set to the incoming baggage while the handler runs; the nested call's own receipts are
    charged by the downstream server, and the downstream result's baggage should be copied
    back with absorb_nested().
    """
    return _current_baggage.get()


def absorb_nested(nested_result_meta: dict | None) -> None:
    """Adopt joules_spent from a nested call's result baggage, if it carries one."""
    b = (nested_result_meta or {}).get(BAGGAGE_KEY)
    if b is not None:
        _current_baggage.set(b)


class EnergyMiddleware:
    """Wrap a tools/call handler: steer, run, receipt, charge the baggage budget.

    handler(name, arguments, variant) -> MCP CallToolResult dict.
    cgroup_of(name, variant) -> cgroup path of the pod serving that variant.
    projected_joules(name) -> joules this call is expected to cost; budget pressure is
    remaining < projected_remaining(spent), where the caller supplies the task-level
    projection (default: no pressure without a budget, else remaining < this call's EWMA).
    """

    def __init__(self, ledger: Ledger, handler, cgroup_of, steerer: Steerer | None = None,
                 clock_ns=time.monotonic_ns, projected_remaining=None):
        self.ledger = ledger
        self.handler = handler
        self.cgroup_of = cgroup_of
        self.steerer = steerer
        self.clock_ns = clock_ns
        self.projected_remaining = projected_remaining

    def _pressure(self, name: str, jb: JouleBudget) -> bool:
        if jb.remaining is None:
            return False
        if self.projected_remaining is not None:
            return jb.remaining < self.projected_remaining(jb.spent)
        if self.steerer is None:
            return False
        return jb.remaining < self.steerer.cost(name, self.steerer.defaults[name])

    def call(self, params: dict) -> dict:
        name = params["name"]
        meta = params.get("_meta") or {}
        members, jb = read_budget(meta)
        allowed = self.steerer.variants.get(name, []) if self.steerer is not None else []
        if meta.get(VARIANT_KEY) in allowed:
            variant = meta[VARIANT_KEY]
        elif allowed:
            variant = self.steerer.choose(name, self._pressure(name, jb))
        else:
            variant = "default"
        token = _current_baggage.set(meta.get(BAGGAGE_KEY))
        t0 = self.clock_ns()
        try:
            result = self.handler(name, params.get("arguments") or {}, variant)
        finally:
            t1 = self.clock_ns()
            nested = _current_baggage.get()
            _current_baggage.reset(token)
        if nested is not None and nested != meta.get(BAGGAGE_KEY):
            members, jb = read_budget({BAGGAGE_KEY: nested})
        reading = self.ledger.window(self.cgroup_of(name, variant), t0, t1)
        receipt = build_receipt(reading)
        receipt["variant"] = variant
        if allowed:
            self.steerer.observe(name, variant, receipt["joules_total"], (t1 - t0) / 1e9,
                                 receipt["confidence"])
        new_baggage = charge(members, jb, receipt["joules_total"])
        if jb.budget is not None:
            receipt["budget"] = {"joules_budget": jb.budget, "joules_spent": round(jb.spent, 3),
                                 "within": jb.spent <= jb.budget}
        out = dict(result)
        out_meta = dict(out.get("_meta") or {})
        out_meta[RECEIPT_KEY] = receipt
        if new_baggage:
            out_meta[BAGGAGE_KEY] = new_baggage
        out["_meta"] = out_meta
        return out

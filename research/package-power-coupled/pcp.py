"""Package-power-coupled placement: controller library (static, no host writes).

Pieces:
  PowerModel      fit per-component package power from RAPL samples
  effective_cap   min(PL1, thermal cap)
  admit           admission + sustained-heavy roles off package
  render_policy   BalloonsPolicy spec with per-role cpuClasses
  validate        strict check of a rendered spec against the pinned CRD schema
  Demoter         1 Hz loop: demote lowest-weight on-package role after 5 s at cap

Nothing here reads or writes /sys. Callers pass samples and host facts in.
"""
from dataclasses import dataclass, field

ON_PACKAGE = {"pcore", "ecore", "igpu", "npu"}


# ---------------------------------------------------------------- power model

@dataclass
class Sample:
    """One RAPL interval. Frequencies in GHz, powers in W, n_* = busy cores."""
    n_p: float
    f_p: float
    n_e: float
    f_e: float
    g_busy: float  # 0..1 iGPU busy fraction
    f_g: float
    pkg_w: float
    core_w: float  # RAPL "core" (PP0)
    gfx_w: float  # RAPL "uncore" (PP1 = graphics on client parts)


@dataclass
class PowerModel:
    fmax_p: float
    fmax_e: float
    fmax_g: float
    alpha: float = 2.6  # held fixed: not identifiable from a narrow freq range
    c_p: float = 0.0
    c_e: float = 0.0
    c_g: float = 0.0
    g0: float = 0.0
    p_static: float = 0.0

    def fit(self, samples):
        if not any(s.g_busy > 0.05 for s in samples):
            raise ValueError("iGPU never busy in samples: c_g unidentifiable")
        a = self.alpha
        # core_w = c_p * x1 + c_e * x2, least squares through the origin
        s11 = s12 = s22 = b1 = b2 = 0.0
        for s in samples:
            x1 = s.n_p * (s.f_p / self.fmax_p) ** a
            x2 = s.n_e * (s.f_e / self.fmax_e) ** a
            s11 += x1 * x1
            s12 += x1 * x2
            s22 += x2 * x2
            b1 += x1 * s.core_w
            b2 += x2 * s.core_w
        det = s11 * s22 - s12 * s12
        if abs(det) < 1e-9:
            raise ValueError("P and E load collinear in samples: c_p/c_e unidentifiable")
        self.c_p = (b1 * s22 - b2 * s12) / det
        self.c_e = (s11 * b2 - s12 * b1) / det
        # gfx_w = c_g * g * (f_g/fmax)^a + g0
        xs = [s.g_busy * (s.f_g / self.fmax_g) ** a for s in samples]
        ys = [s.gfx_w for s in samples]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vxx = sum((x - mx) ** 2 for x in xs)
        self.c_g = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vxx
        self.g0 = my - self.c_g * mx
        self.p_static = sum(s.pkg_w - s.core_w - s.gfx_w for s in samples) / len(samples)
        return self

    def package_w(self, n_p, f_p, n_e, f_e, g_busy, f_g):
        a = self.alpha
        return (self.p_static
                + self.c_p * n_p * (f_p / self.fmax_p) ** a
                + self.c_e * n_e * (f_e / self.fmax_e) ** a
                + self.c_g * g_busy * (f_g / self.fmax_g) ** a + self.g0)


def effective_cap(pl1_w, thermal_cap_w):
    """The binding package limit. TDP is deliberately not an input."""
    return min(pl1_w, thermal_cap_w)


# ---------------------------------------------------------------- admission

@dataclass
class Role:
    name: str
    weight: int  # demotion order: lowest first; planner is never demoted
    chain: list  # DRA prioritizedList order, e.g. ["igpu", "dgpu", "ecore"]
    sustained: bool = False
    cores: int = 0  # CPU roles: cores requested
    placed: str = ""

    @property
    def planner(self):
        return self.name == "planner"


def admit(roles, predict_w, cap_w, have_dgpu, margin=0.9):
    """Place roles. Returns (admitted, roles). predict_w(roles) -> steady package W.

    Sustained-heavy roles take the first off-package device in their chain when a
    dGPU exists; everyone else takes the first device in their chain. While the
    prediction is above `margin * cap_w`, the lowest-weight non-planner role moves to
    its next off-package fallback. If none is left, the plan is refused (False) and
    the placement is returned unchanged for the caller to decide.
    """
    for r in roles:
        r.placed = r.chain[0]
        if r.sustained and have_dgpu:
            off = [d for d in r.chain if d not in ON_PACKAGE]
            if off:
                r.placed = off[0]
        if r.placed == "dgpu" and not have_dgpu:
            r.placed = next(d for d in r.chain if d != "dgpu")
    while predict_w(roles) > margin * cap_w:
        moved = False
        for r in sorted(roles, key=lambda r: r.weight):
            if r.planner or r.placed not in ON_PACKAGE:
                continue
            nxt = _next_fallback(r, have_dgpu, off_package_only=True)
            if nxt is not None:
                r.placed, moved = nxt, True
                break
        if not moved:
            return False, roles
    return True, roles


# ---------------------------------------------------------------- policy render

def _deep_states(idle_state_names):
    """Every idle state deeper than the shallowest non-POLL one."""
    real = [s for s in idle_state_names if s != "POLL"]
    return real[1:]


def render_policy(base_spec, roles, host, e_cap_mhz, epp_tools=192):
    """Return a BalloonsPolicy spec dict with per-role cpuClasses.

    host: dict(idle_states=[...]) as read from cpu*/cpuidle/state*/name.
    e_cap_mhz: explicit E-core maxFreq for tool/embed classes.
    """
    if not isinstance(e_cap_mhz, int) or e_cap_mhz <= 0:
        raise ValueError("e_cap_mhz must be a positive integer MHz")
    spec = {k: v for k, v in base_spec.items() if k not in ("balloonTypes",)}
    classes = [
        {"name": "planner", "minFreq": "base", "maxFreq": "turbo",
         "turboPriority": 10, "energyPerformancePreference": 0,
         "disabledCstates": _deep_states(host["idle_states"])},
        {"name": "capped", "minFreq": "min", "maxFreq": f"{e_cap_mhz}MHz",
         "energyPerformancePreference": epp_tools},
        {"name": "idle", "minFreq": "min", "maxFreq": "min",
         "energyPerformancePreference": 255},
    ]
    spec["cpuClasses"] = classes
    spec["idleCPUClass"] = "idle"
    types = []
    cpu_roles = [r for r in roles if r.placed in ("pcore", "ecore")]
    for r in cpu_roles:
        types.append({
            "name": r.name,
            "preferCoreType": "performance" if r.placed == "pcore" else "efficient",
            "cpuClass": "planner" if r.planner else "capped",
            "matchExpressions": [{"key": "pod/labels/k3d-gpu.io/role",
                                  "operator": "Equals", "values": [r.name]}],
        })
    # keep the shipped catch-alls (performance / efficient / shared) after the roles
    types.extend(base_spec.get("balloonTypes", []))
    spec["balloonTypes"] = types
    _check_turbo(spec)
    return spec


def _check_turbo(spec):
    """turboPriority resolves symbolic "turbo" to base for every lower class."""
    top = max(c.get("turboPriority", 0) for c in spec["cpuClasses"])
    for c in spec["cpuClasses"]:
        if top > 0 and c.get("turboPriority", 0) < top:
            for k in ("minFreq", "maxFreq"):
                if c.get(k) == "turbo":
                    raise ValueError(f"class {c['name']}: {k}=turbo would resolve to base")


# ---------------------------------------------------------------- CRD validation

def validate(obj, schema, path="spec"):
    """Strict subset of OpenAPI v3: type/properties/items/enum/minimum/required.
    Unknown keys fail (CRDs prune them silently, which hides typos)."""
    errs = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path}: want object"]
        props = schema.get("properties")
        addl = schema.get("additionalProperties")
        for k, v in obj.items():
            if props is not None and k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif isinstance(addl, dict):
                errs += validate(v, addl, f"{path}.{k}")
            elif props is not None and not schema.get("x-kubernetes-preserve-unknown-fields"):
                errs.append(f"{path}.{k}: unknown field")
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k}: required")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path}: want array"]
        for i, v in enumerate(obj):
            errs += validate(v, schema.get("items", {}), f"{path}[{i}]")
    elif t == "string":
        if not isinstance(obj, str):
            errs.append(f"{path}: want string")
    elif t == "integer":
        if not isinstance(obj, int) or isinstance(obj, bool):
            errs.append(f"{path}: want integer")
    elif t == "boolean":
        if not isinstance(obj, bool):
            errs.append(f"{path}: want boolean")
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if "minimum" in schema and isinstance(obj, (int, float)) and obj < schema["minimum"]:
        errs.append(f"{path}: below minimum {schema['minimum']}")
    return errs


def crd_spec_schema(docs):
    """Pull BalloonsPolicy .spec schema out of the rendered chart documents."""
    for d in docs:
        if d and d.get("kind") == "CustomResourceDefinition" and \
                d["spec"]["names"]["kind"] == "BalloonsPolicy":
            ver = d["spec"]["versions"][0]
            return ver["schema"]["openAPIV3Schema"]["properties"]["spec"]
    raise LookupError("BalloonsPolicy CRD not found")


# ---------------------------------------------------------------- demotion loop

@dataclass
class Demoter:
    """Call tick() at 1 Hz. Demotes after `hold_s` consecutive seconds at cap.

    Only to an off-package fallback: a move between two on-package devices adds load
    to the package it is meant to relieve. `off_package_only=False` is the idea's
    literal rule, kept for the ablation.
    """
    hold_s: int = 5
    at_cap_frac: float = 0.97
    off_package_only: bool = True
    streak: int = 0
    log: list = field(default_factory=list)

    def tick(self, roles, pkg_w, cap_w, throttled, have_dgpu):
        hot = pkg_w >= self.at_cap_frac * cap_w and throttled
        self.streak = self.streak + 1 if hot else 0
        if self.streak <= self.hold_s:
            return None
        for r in sorted(roles, key=lambda r: r.weight):
            if r.planner or r.placed not in ON_PACKAGE:
                continue
            nxt = _next_fallback(r, have_dgpu, self.off_package_only)
            if nxt is None:
                continue
            self.log.append((r.name, r.placed, nxt))
            r.placed = nxt
            self.streak = 0
            return r.name
        return None


def _next_fallback(role, have_dgpu, off_package_only=False):
    i = role.chain.index(role.placed) if role.placed in role.chain else -1
    for d in role.chain[i + 1:]:
        if d == "dgpu" and not have_dgpu:
            continue
        if off_package_only and d in ON_PACKAGE:
            continue
        if d != role.placed:
            return d
    return None

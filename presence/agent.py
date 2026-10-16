"""Node agent, dry-run: read attention, print the operating-point plan.

    python3 -m presence.agent [--follow] [--node k3d-NAME-server-0]

Never writes: every sysfs write, nvidia-smi call and kubectl label is printed.
Applying them needs root (EPP, -pl) and is left to the live probe window.
"""

import argparse
import getpass
import subprocess
import time

from . import oppoint
from .attention import AWAY, PRESENT, Attention, display_gpu, read_session
from .chunk import budget_chunk


def cpus(kind):
    try:
        with open(f"/sys/devices/cpu_{kind}/cpus") as f:
            text = f.read().strip()
    except OSError:
        return []
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def gpu_limits():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=power.min_limit,power.max_limit",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [tuple(float(x) for x in line.split(",")) for line in out.splitlines() if line.strip()]


def graphical_session(user):
    out = subprocess.run(["loginctl", "show-user", user, "-p", "Display", "--no-pager"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip().split("=", 1)[1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", default="k3d-k3d-gpu-server-0")
    ap.add_argument("--follow", action="store_true", help="poll logind every 0.5 s")
    ap.add_argument("--t-away", type=float, default=120.0)
    ap.add_argument("--refresh-hz", type=float, default=60.0)
    ap.add_argument("--compositor-p99-ms", type=float, default=6.0)
    ap.add_argument("--igpu-ms-per-token", type=float, default=3.75)
    ap.add_argument("--kernel-frac", type=float, default=0.02)
    a = ap.parse_args(argv)

    gpus = gpu_limits()
    points = oppoint.default_points(gpus)
    displays = display_gpu()
    igpu_display = any(d["vendor"] == "0x8086" for d in displays.values())
    if igpu_display:
        points["present"]["gw_chunk_tokens"] = budget_chunk(
            1.0 / a.igpu_ms_per_token, a.refresh_hz, a.compositor_p99_ms, slice_frac=a.kernel_frac)
        points["present"]["gw_chunk_gap_ms"] = 0.0
    oppoint.validate(points)
    p_cpus, e_cpus = cpus("core"), cpus("atom")
    print(f"displays: {displays}  (iGPU scans out: {igpu_display})")
    print(f"P-cores {p_cpus[:1]}..{p_cpus[-1:]}  E-cores {e_cpus[:1]}..{e_cpus[-1:]}  GPUs {gpus}")

    session = graphical_session(getpass.getuser())
    att = Attention(t_away=a.t_away, now=time.monotonic())
    shown = None
    while True:
        now = time.monotonic()
        s = read_session(session)
        att.on_session(now, locked=s["locked"], active=s["active"], idle_hint=s["idle_hint"])
        if not s["idle_hint"] and not s["locked"]:
            att.on_input(now)  # logind clears IdleHint on input (GNOME/KDE set it)
        att.tick(now)
        if att.state != shown:
            old = points[shown] if shown else points[AWAY if att.state == PRESENT else PRESENT]
            changes = oppoint.plan(old, points[att.state])
            print(f"\nsession {session} {s} -> {att.state}; "
                  f"critical-path estimate {oppoint.critical_latency(changes):.2f}s")
            for c in oppoint.commands(changes, p_cpus=p_cpus, e_cpus=e_cpus, gpus=gpus or [(0, 0)],
                                      node=a.node, state=att.state):
                print("  DRY-RUN", c)
            shown = att.state
        if not a.follow:
            return 0
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())

"""Block energy for the agent benchmark (stdlib only).

E_block = RAPL package (all packages) + psys over the block, plus the integral of
`nvidia-smi --query-gpu=power.draw` for a dGPU arm. The integrated GPU sits inside
the package domain. psys is reported beside, not added: on parts that expose it,
psys already contains the package.

J per successful task is differential: (E_block - P_ctrl * t_block) / successes,
where P_ctrl is the mean power of a load-only control window at the same load
level with the arm's server up and idle. Failed and retried work stays in the
numerator.
"""
import glob
import os
import shlex
import subprocess
import threading
import time


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def rapl_domains(sysfs="/sys"):
    """{"package": [zone dirs], "psys": zone dir or None}."""
    pkg, psys = [], None
    for z in sorted(glob.glob(f"{sysfs}/class/powercap/intel-rapl:*")):
        if z.count(":") != 1:
            continue
        name = _read(f"{z}/name")
        if name.startswith("package"):
            pkg.append(z)
        elif name == "psys":
            psys = z
    return {"package": pkg, "psys": psys}



def rapl_elevation():
    """Command prefix that reads a root-only energy_uj: K3D_GPU_RAPL_EXEC when set (e.g.
    `docker exec <container with /sys/class/powercap> ` on a host without sudo), else
    sudo -A (SUDO_ASKPASS set) or sudo -n."""
    cmd = os.environ.get("K3D_GPU_RAPL_EXEC")
    if cmd:
        return shlex.split(cmd)
    return ["sudo", "-A"] if os.environ.get("SUDO_ASKPASS") else ["sudo", "-n"]

def read_uj(zone):
    """energy_uj of one zone; root-only on current kernels, so fall back to sudo -n/-A."""
    v = _read(f"{zone}/energy_uj")
    if v.isdigit():
        return int(v)
    elev = rapl_elevation()
    try:
        p = subprocess.run(elev + ["cat", f"{zone}/energy_uj"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    v = p.stdout.strip()
    return int(v) if p.returncode == 0 and v.isdigit() else None


def delta_uj(a, b, max_range):
    if a is None or b is None:
        return None
    d = b - a
    return d + max_range if d < 0 and max_range else d


class GpuPower(threading.Thread):
    """Integrates nvidia-smi power.draw (W) by the trapezoid rule at `period` s."""

    def __init__(self, period=0.5, query=None):
        super().__init__(daemon=True)
        self.period = period
        self.query = query or self._nvidia_smi
        self.stop_ev = threading.Event()
        self.joules = 0.0
        self.samples = 0
        self.available = True

    @staticmethod
    def _nvidia_smi():
        p = subprocess.run(["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            return None
        vals = [float(x) for x in p.stdout.split() if x.replace(".", "", 1).isdigit()]
        return sum(vals) if vals else None

    def run(self):
        last_t, last_w = None, None
        while not self.stop_ev.is_set():
            try:
                w = self.query()
            except (OSError, subprocess.TimeoutExpired, ValueError):
                w = None
            now = time.monotonic()
            if w is None:
                self.available = False
                return
            if last_t is not None:
                self.joules += (w + last_w) / 2 * (now - last_t)
            last_t, last_w = now, w
            self.samples += 1
            self.stop_ev.wait(self.period)

    def stop(self):
        self.stop_ev.set()
        self.join(timeout=10)


class Meter:
    """Energy over one block: start() ... stop() -> dict."""

    def __init__(self, sysfs="/sys", gpu=False, gpu_query=None, reader=read_uj, clock=time.monotonic):
        self.dom = rapl_domains(sysfs)
        self.reader = reader
        self.clock = clock
        self.gpu = GpuPower(query=gpu_query) if gpu else None
        self.ranges = {z: int(_read(f"{z}/max_energy_range_uj") or 0)
                       for z in self.dom["package"] + ([self.dom["psys"]] if self.dom["psys"] else [])}

    def _snap(self):
        return {z: self.reader(z) for z in self.ranges}

    def start(self):
        self.t0, self.e0 = self.clock(), self._snap()
        if self.gpu:
            self.gpu.start()

    def stop(self):
        t1, e1 = self.clock(), self._snap()
        if self.gpu:
            self.gpu.stop()
        dt = t1 - self.t0
        pk = [delta_uj(self.e0[z], e1[z], self.ranges[z]) for z in self.dom["package"]]
        pkg_j = sum(pk) / 1e6 if pk and None not in pk else None
        ps = self.dom["psys"]
        psys_j = delta_uj(self.e0[ps], e1[ps], self.ranges[ps]) if ps else None
        psys_j = psys_j / 1e6 if psys_j is not None else None
        gpu_j = self.gpu.joules if self.gpu and self.gpu.available else None
        total = None if pkg_j is None else pkg_j + (gpu_j or 0.0)
        if self.gpu and gpu_j is None:
            total = None
        return {"seconds": round(dt, 3), "package_j": pkg_j, "psys_j": psys_j, "gpu_j": gpu_j,
                "energy_j": total, "mean_w": round(total / dt, 3) if total is not None and dt > 0 else None}


def j_per_success(block, control, successes):
    """Differential energy per successful task, or None with the reason why."""
    if successes <= 0:
        return None, "zero successes: J per success undefined"
    if not block or block.get("energy_j") is None:
        return None, "block energy unreadable"
    if not control or control.get("mean_w") is None:
        return None, "no control window"
    net = block["energy_j"] - control["mean_w"] * block["seconds"]
    return net / successes, "ok"

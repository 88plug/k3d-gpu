"""Counter readers for the node daemon. Stdlib only; every path takes a sysroot
so tests can point it at a fake tree.

Sources (all read-only):
  * powercap intel-rapl zones: /sys/class/powercap/intel-rapl:*/energy_uj
    (root-only since the PLATYPUS fix, so the DaemonSet runs privileged).
    On the i9-13900T host the zones are package-0, core, uncore and psys.
  * NVML nvmlDeviceGetTotalEnergyConsumption (mJ since driver load, Volta+),
    loaded with ctypes from libnvidia-ml.so.1 mounted by the NVIDIA runtime.
  * DRM fdinfo (Documentation/gpu/drm-usage-stats.rst): drm-engine-<k> busy ns
    (i915, ivpu) or drm-cycles-<k> / drm-total-cycles-<k> (xe).
  * cgroup v2 cpu.stat usage_usec per pod cgroup.
"""

import ctypes
import glob
import os
import re


def _read(path):
    with open(path) as f:
        return f.read()


def rapl_zones(sysroot="/"):
    """{name: (energy_uj path, max_energy_range_uj)}; names made unique per socket."""
    out = {}
    base = os.path.join(sysroot, "sys/class/powercap")
    for d in sorted(glob.glob(os.path.join(base, "intel-rapl:*"))):
        try:
            name = _read(os.path.join(d, "name")).strip()
            rng = int(_read(os.path.join(d, "max_energy_range_uj")))
        except (OSError, ValueError):
            continue
        sock = os.path.basename(d).split(":")[1]
        key = f"rapl.{name}" if name.startswith(("package", "psys")) else f"rapl.{name}.{sock}"
        out[key] = (os.path.join(d, "energy_uj"), rng)
    return out


def read_rapl(path):
    """Joules."""
    return int(_read(path)) / 1e6


class Nvml:
    """Minimal ctypes binding for the energy counter. Absent library -> no GPUs."""

    def __init__(self, lib="libnvidia-ml.so.1"):
        self.handles = []
        try:
            self.lib = ctypes.CDLL(lib)
        except OSError:
            return
        if self.lib.nvmlInit_v2() != 0:
            return
        n = ctypes.c_uint(0)
        self.lib.nvmlDeviceGetCount_v2(ctypes.byref(n))
        for i in range(n.value):
            h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)) == 0:
                self.handles.append(h)

    def energy(self, idx):
        """Joules, or None if the device has no energy counter (pre-Volta)."""
        mj = ctypes.c_ulonglong(0)
        rc = self.lib.nvmlDeviceGetTotalEnergyConsumption(self.handles[idx], ctypes.byref(mj))
        return mj.value / 1e3 if rc == 0 else None


_FDINFO_KEY = re.compile(r"^(drm-engine-[\w-]+|drm-cycles-[\w-]+|drm-total-cycles-[\w-]+"
                         r"|drm-pdev|drm-client-id|drm-driver):\s+(\S+)")


def parse_fdinfo(text):
    """Return {key: value} for DRM usage keys; numeric values as int."""
    out = {}
    for line in text.splitlines():
        m = _FDINFO_KEY.match(line)
        if not m:
            continue
        k, v = m.groups()
        if k.startswith("drm-engine-capacity"):
            continue
        out[k] = int(v) if v.isdigit() else v
    return out


def drm_busy_by_pid(sysroot="/"):
    """{pid: {(pdev, client_id): {engine: busy_ns or busy_cycles}}}.

    Each DRM client is counted once even if a process holds several fds to it.
    """
    out = {}
    for fdinfo in glob.glob(os.path.join(sysroot, "proc/[0-9]*/fdinfo/*")):
        try:
            text = _read(fdinfo)
        except OSError:
            continue
        if "drm-client-id" not in text:
            continue
        kv = parse_fdinfo(text)
        pid = int(fdinfo.split(os.sep)[-3])
        client = (kv.get("drm-pdev", ""), kv.get("drm-client-id"))
        eng = {k: v for k, v in kv.items()
               if isinstance(v, int) and k.startswith(("drm-engine-", "drm-cycles-"))}
        out.setdefault(pid, {})[client] = eng
    return out


def cgroup_usage_usec(cgroup_dir):
    with open(os.path.join(cgroup_dir, "cpu.stat")) as f:
        for line in f:
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    return 0

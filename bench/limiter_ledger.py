#!/usr/bin/env python3
"""limiter_ledger.py: tag each LLM decode step with the package limit reasons active during it.

Runs as a sidecar in the decode pod (and so on the pod's NRI balloon CPUs). The decode loop
connects to a unix socket and writes one line per step boundary:

    M <step>\\n      mark: close the current step and open the next one
    S\\n             summary: the sidecar replies with one JSON line and closes the socket

At each mark the sidecar reads, on one CPU of the package:
  0x64F MSR_CORE_PERF_LIMIT_REASONS      log bits (sticky) and status bits
  0x6B0 MSR_GRAPHICS_PERF_LIMIT_REASONS  same, for the iGPU (when --gfx)
  0x1B1 IA32_PACKAGE_THERM_STATUS        digital readout only (never written)
  0x611 MSR_PKG_ENERGY_STATUS            package energy
and APERF/MPERF on every pod CPU, giving busy MHz. With --clear it then writes 0 to the log bits
it saw, so the next step starts clean. Bit layouts: Intel SDM vol. 4 Table 2-39 (6th-13th gen
Core) and Table 2-2; see docs/limiter-ledger.md. All limit-reason bits are package scope.

  limiter_ledger.py serve --socket /run/limiter/ledger.sock [--clear] [--gfx] [--cpus 0-7]
                          [--tjmax 100] [--out ledger.jsonl]
  limiter_ledger.py decode 0x0C000C00    # decode one raw 0x64F value

Needs the msr module, /dev/cpu/*/msr and CAP_SYS_RAWIO. A write (--clear) taints the kernel
(TAINT_CPU_OUT_OF_SPEC, arch/x86/kernel/msr.c), and kernel lockdown refuses it.
"""
import argparse
import json
import os
import socket
import struct
import sys
import time

MSR_MPERF = 0xE7
MSR_APERF = 0xE8
MSR_PKG_THERM_STATUS = 0x1B1
MSR_RAPL_POWER_UNIT = 0x606
MSR_PKG_ENERGY_STATUS = 0x611
MSR_CORE_PLR = 0x64F
MSR_GFX_PLR = 0x6B0

# SDM vol. 4 Table 2-39, MSR 64FH. name -> (status bit, log bit). Status bits are R0; log bits
# stay set until software writes 0.
CORE_PLR = {
    "PROCHOT": (0, 16),
    "THERMAL": (1, 17),
    "RSR": (4, 20),           # residency state regulation
    "RATL": (5, 21),          # running average thermal limit
    "VR_THERM": (6, 22),
    "VR_TDC": (7, 23),
    "OTHER": (8, 24),         # electrical or other constraints
    "PL1": (10, 26),          # package/platform PL1
    "PL2": (11, 27),          # package/platform PL2/PL3
    "MAX_TURBO": (12, 28),    # multi-core turbo limit
    "TURBO_ATTEN": (13, 29),  # turbo transition attenuation
}
# SDM vol. 4 Table 2-39, MSR 6B0H (processor graphics).
GFX_PLR = {
    "PROCHOT": (0, 16),
    "THERMAL": (1, 17),
    "RATL": (5, 21),
    "VR_THERM": (6, 22),
    "VR_TDC": (7, 23),
    "OTHER": (8, 24),
    "PL1": (10, 26),
    "PL2": (11, 27),
    "INEFFICIENT": (12, 28),
}

# A limiter class per step, in precedence order. PL1/PL2 are the power limits an experimenter can
# set; the rest are not under experimental control (see the D4 manipulation check).
UNCONTROLLED = ("PROCHOT", "THERMAL", "RATL", "VR_THERM", "VR_TDC", "OTHER")
CLASS_ORDER = ("THERMAL_OR_ELEC", "PL1", "PL2", "TRANSIENT", "NONE")


def log_mask(table):
    """All log bits of a limit-reasons register."""
    m = 0
    for _, log in table.values():
        m |= 1 << log
    return m


def decode(value, table=CORE_PLR):
    """Raw MSR value -> {'status': [names], 'log': [names]}."""
    return {
        "status": sorted(n for n, (s, _) in table.items() if value >> s & 1),
        "log": sorted(n for n, (_, lg) in table.items() if value >> lg & 1),
    }


def limiter_class(names):
    """Collapse a set of reason names to one class for the step."""
    names = set(names)
    if names & set(UNCONTROLLED):
        return "THERMAL_OR_ELEC"
    if "PL1" in names:
        return "PL1"
    if "PL2" in names:
        return "PL2"
    if names & {"MAX_TURBO", "TURBO_ATTEN", "RSR", "INEFFICIENT"}:
        return "TRANSIENT"
    return "NONE"


def clear_value(observed, table=CORE_PLR):
    """Value to write so that exactly the log bits seen in `observed` are cleared.

    Log bits clear on a written 0; a written 1 leaves them alone. Status bits are read-only.
    Clearing only bits already seen keeps any log bit that set between the read and the write.
    """
    mask = log_mask(table)
    return mask & ~(observed & mask)


def pkg_temp_c(therm_status, tjmax):
    """Package temperature from IA32_PACKAGE_THERM_STATUS bits 22:16 (degrees below TjMax)."""
    return tjmax - (therm_status >> 16 & 0x7F)


def energy_unit_j(rapl_power_unit):
    """Joules per count of MSR_PKG_ENERGY_STATUS: 1/2^ESU, ESU = MSR 0x606 bits 12:8."""
    return 2.0 ** -(rapl_power_unit >> 8 & 0x1F)


def energy_delta(prev, cur):
    """Difference of two 32-bit energy counter readings, allowing one wrap."""
    return (cur - prev) & 0xFFFFFFFF


def parse_cpus(text):
    """'0-3,8' -> [0, 1, 2, 3, 8]."""
    cpus = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-")
            cpus.extend(range(int(lo), int(hi) + 1))
        else:
            cpus.append(int(part))
    return sorted(set(cpus))


class DevMsr:
    """MSR access through /dev/cpu/N/msr."""

    def __init__(self, root="/dev/cpu"):
        self.root = root
        self.fds = {}

    def _fd(self, cpu, write=False):
        key = (cpu, write)
        if key not in self.fds:
            self.fds[key] = os.open(f"{self.root}/{cpu}/msr", os.O_RDWR if write else os.O_RDONLY)
        return self.fds[key]

    def read(self, cpu, reg):
        return struct.unpack("<Q", os.pread(self._fd(cpu), 8, reg))[0]

    def write(self, cpu, reg, value):
        os.pwrite(self._fd(cpu, True), struct.pack("<Q", value), reg)


class Ledger:
    """Per-step limiter ledger over one package."""

    def __init__(self, msr, cpus, base_mhz, tjmax=100, clear=False, gfx=False, clock=time.monotonic):
        self.msr = msr
        self.cpus = list(cpus)
        self.pkg_cpu = self.cpus[0]
        self.base_mhz = base_mhz
        self.tjmax = tjmax
        self.clear = clear
        self.gfx = gfx
        self.clock = clock
        self.e_unit = energy_unit_j(msr.read(self.pkg_cpu, MSR_RAPL_POWER_UNIT))
        self.rows = []
        self._snap = self._sample()
        if clear:  # start from a clean log
            self._clear(self._snap["core"], self._snap.get("gfx"))
            self._snap = self._sample()

    def _sample(self):
        m, c = self.msr, self.pkg_cpu
        snap = {
            "t": self.clock(),
            "core": m.read(c, MSR_CORE_PLR),
            "therm": m.read(c, MSR_PKG_THERM_STATUS),
            "energy": m.read(c, MSR_PKG_ENERGY_STATUS) & 0xFFFFFFFF,
            "aperf": [m.read(x, MSR_APERF) for x in self.cpus],
            "mperf": [m.read(x, MSR_MPERF) for x in self.cpus],
        }
        if self.gfx:
            snap["gfx"] = m.read(c, MSR_GFX_PLR)
        return snap

    def _clear(self, core, gfx):
        self.msr.write(self.pkg_cpu, MSR_CORE_PLR, clear_value(core, CORE_PLR))
        if gfx is not None:
            self.msr.write(self.pkg_cpu, MSR_GFX_PLR, clear_value(gfx, GFX_PLR))

    def mark(self, step):
        """Close the step that ran since the previous mark and record one row."""
        cur = self._sample()
        prev = self._snap
        da = sum(a - b for a, b in zip(cur["aperf"], prev["aperf"]))
        dm = sum(a - b for a, b in zip(cur["mperf"], prev["mperf"]))
        core = decode(cur["core"], CORE_PLR)
        # With --clear the log bits cover exactly this step; without it only the status bits at
        # the boundary describe the step.
        reasons = core["log"] if self.clear else core["status"]
        row = {
            "step": step,
            "dt_s": cur["t"] - prev["t"],
            "core": reasons,
            "bzy_mhz": self.base_mhz * da / dm if dm else None,
            "pkg_c": pkg_temp_c(cur["therm"], self.tjmax),
            "pkg_j": energy_delta(prev["energy"], cur["energy"]) * self.e_unit,
        }
        if self.gfx:
            g = decode(cur["gfx"], GFX_PLR)
            row["gfx"] = g["log"] if self.clear else g["status"]
        row["class"] = limiter_class(row["core"] + row.get("gfx", []))
        if self.clear:
            self._clear(cur["core"], cur.get("gfx"))
            # The interval for the next step starts after the clear, so a reason that sets
            # between the read and the clear is logged in the next step rather than lost.
            cur["t"] = self.clock()
        self._snap = cur
        self.rows.append(row)
        return row


def summarize(rows):
    """Clean vs contaminated tok/s and the limiter-class histogram."""
    hist = {k: 0 for k in CLASS_ORDER}
    clean, dirty = [], []
    for r in rows:
        hist[r["class"]] += 1
        (clean if r["class"] == "NONE" else dirty).append(r["dt_s"])

    def tps(dts):
        return len(dts) / sum(dts) if dts and sum(dts) > 0 else None

    return {
        "steps": len(rows),
        "classes": hist,
        "tok_s_all": tps(clean + dirty),
        "tok_s_clean": tps(clean),
        "tok_s_contaminated": tps(dirty),
        "clean_fraction": len(clean) / len(rows) if rows else None,
    }


def serve(ledger, path, out=None):
    """Accept one decode client at a time; one row per 'M' line."""
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    sink = open(out, "a", encoding="utf-8") if out else None
    try:
        while True:
            conn, _ = srv.accept()
            with conn, conn.makefile("rwb") as f:
                for line in f:
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == b"M":
                        row = ledger.mark(int(parts[1]) if len(parts) > 1 else len(ledger.rows))
                        if sink:
                            sink.write(json.dumps(row) + "\n")
                    elif parts[0] == b"S":
                        f.write((json.dumps(summarize(ledger.rows)) + "\n").encode())
                        f.flush()
                        ledger.rows = []
                        break
            if sink:
                sink.flush()
    finally:
        srv.close()
        if sink:
            sink.close()


class Client:
    """What the decode loop calls: mark(step) after each token, summary() at the end."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)

    def mark(self, step):
        self.sock.sendall(b"M %d\n" % step)

    def summary(self):
        self.sock.sendall(b"S\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        self.sock.close()
        return json.loads(buf)


def base_mhz(cpu):
    with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/base_frequency", encoding="ascii") as f:
        return int(f.read()) / 1000.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--socket", default="/run/limiter/ledger.sock")
    s.add_argument("--cpus", help="CPU list to read APERF/MPERF on (default: own affinity)")
    s.add_argument("--clear", action="store_true", help="clear log bits each step (taints kernel)")
    s.add_argument("--gfx", action="store_true", help="also read the iGPU limit reasons (0x6B0)")
    s.add_argument("--tjmax", type=int, default=100)
    s.add_argument("--out", help="append one JSON row per step to this file")
    d = sub.add_parser("decode")
    d.add_argument("value")
    d.add_argument("--gfx", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "decode":
        v = int(a.value, 0)
        print(json.dumps(decode(v, GFX_PLR if a.gfx else CORE_PLR)))
        return 0
    cpus = parse_cpus(a.cpus) if a.cpus else sorted(os.sched_getaffinity(0))
    ledger = Ledger(DevMsr(), cpus, base_mhz(cpus[0]), a.tjmax, a.clear, a.gfx)
    serve(ledger, a.socket, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

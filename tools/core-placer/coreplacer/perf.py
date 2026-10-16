"""Per-cgroup PMU counters on Intel hybrid and Xeon CPUs.

Encodings come from primary sources:
  * linux/perf_event.h: PERF_PMU_TYPE_SHIFT=32, PERF_ATTR_SIZE_VER5=112,
    PERF_FLAG_PID_CGROUP=1<<2 (pid is a cgroup fd, cpu must be >= 0).
  * Hybrid CPUs expose one PMU per core type (/sys/devices/cpu_core, cpu_atom);
    a generic hardware event targets one of them by putting that PMU's type in
    config bits 63..32.
  * Intel perfmon SPR/GNR core JSON (v1.40 / v1.21): the only AMX event is
    EXE.AMX_BUSY, EventCode 0xb7 UMask 0x02 (cycles the AMX unit is busy).
    There is no AMX_OPS_RETIRED event in those files.

Instructions retired are ISA-level, so the same code stream gives comparable
counts on P and E cores; cycles and TMA slots are not comparable across the two
PMUs and are not used for the speedup estimate.
"""
import ctypes
import os
import struct

PERF_TYPE_HARDWARE = 0
PERF_TYPE_SOFTWARE = 1
PERF_TYPE_RAW = 4
PERF_COUNT_HW_INSTRUCTIONS = 1
PERF_COUNT_SW_TASK_CLOCK = 1
PERF_PMU_TYPE_SHIFT = 32
PERF_ATTR_SIZE_VER5 = 112
PERF_FLAG_PID_CGROUP = 1 << 2
NR_PERF_EVENT_OPEN_X86_64 = 298

EXE_AMX_BUSY = 0xB7 | (0x02 << 8)

# perf_event_attr up to VER5 (aux_watermark, sample_max_stack, reserved).
_ATTR_FMT = "<IIQQQQQIIQQQQIiQIHH"
assert struct.calcsize(_ATTR_FMT) == PERF_ATTR_SIZE_VER5


def pack_attr(ev_type, config, disabled=False):
    flags = 1 if disabled else 0  # bit 0 = disabled
    return struct.pack(_ATTR_FMT, ev_type, PERF_ATTR_SIZE_VER5, config,
                       0, 0, 0, flags, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def hybrid_instructions(pmu_type):
    """Instructions retired on one hybrid PMU (cpu_core or cpu_atom)."""
    return PERF_TYPE_HARDWARE, (pmu_type << PERF_PMU_TYPE_SHIFT) | PERF_COUNT_HW_INSTRUCTIONS


def task_clock():
    return PERF_TYPE_SOFTWARE, PERF_COUNT_SW_TASK_CLOCK


def amx_busy():
    return PERF_TYPE_RAW, EXE_AMX_BUSY


def read_pmu_type(sysroot, name):
    with open(os.path.join(sysroot, "sys/devices", name, "type")) as f:
        return int(f.read())


def parse_cpulist(text):
    cpus = set()
    for part in text.strip().split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def format_cpulist(cpus):
    cpus = sorted(cpus)
    out, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(out)


def open_cgroup_counter(cgroup_dir, cpu, ev_type, config):
    """Open one counting event for every task of a cgroup on one CPU. Returns an fd."""
    libc = ctypes.CDLL(None, use_errno=True)
    cg_fd = os.open(cgroup_dir, os.O_RDONLY)
    try:
        attr = ctypes.create_string_buffer(pack_attr(ev_type, config))
        fd = libc.syscall(NR_PERF_EVENT_OPEN_X86_64, attr, cg_fd, cpu, -1,
                          ctypes.c_ulong(PERF_FLAG_PID_CGROUP))
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        return fd
    finally:
        os.close(cg_fd)


def read_counter(fd):
    return struct.unpack("<Q", os.read(fd, 8))[0]

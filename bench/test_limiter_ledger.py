"""Tests for limiter_ledger.py. No MSR access: a fake register file stands in for /dev/cpu."""
import os
import tempfile
import threading
import time

import pytest

import limiter_ledger as L

# Transcribed from Intel SDM vol. 4 (Sep 2026) Table 2-39, MSR 64FH, independently of the module.
SDM_64F_STATUS = {0: "PROCHOT", 1: "THERMAL", 4: "RSR", 5: "RATL", 6: "VR_THERM", 7: "VR_TDC",
                  8: "OTHER", 10: "PL1", 11: "PL2", 12: "MAX_TURBO", 13: "TURBO_ATTEN"}
SDM_64F_LOG = {16: "PROCHOT", 17: "THERMAL", 20: "RSR", 21: "RATL", 22: "VR_THERM", 23: "VR_TDC",
               24: "OTHER", 26: "PL1", 27: "PL2", 28: "MAX_TURBO", 29: "TURBO_ATTEN"}
SDM_6B0_LOG = {16: "PROCHOT", 17: "THERMAL", 21: "RATL", 22: "VR_THERM", 23: "VR_TDC",
               24: "OTHER", 26: "PL1", 27: "PL2", 28: "INEFFICIENT"}


def test_core_bits_match_sdm():
    for bit, name in SDM_64F_STATUS.items():
        assert L.decode(1 << bit)["status"] == [name]
    for bit, name in SDM_64F_LOG.items():
        assert L.decode(1 << bit)["log"] == [name]
    reserved = [2, 3, 9, 14, 15, 18, 19, 25] + list(range(30, 64))
    for bit in reserved:
        assert L.decode(1 << bit) == {"status": [], "log": []}


def test_gfx_log_bits_match_sdm():
    for bit, name in SDM_6B0_LOG.items():
        assert L.decode(1 << bit, L.GFX_PLR)["log"] == [name]


def test_clear_value_zeroes_only_observed_log_bits():
    observed = (1 << 26) | (1 << 10)  # PL1 logged and active
    v = L.clear_value(observed)
    assert not v >> 26 & 1             # PL1 log cleared
    assert v >> 27 & 1                 # PL2 log, not seen, written 1 = left alone
    assert v & 0xFFFF == 0             # status bits are read-only; nothing written there
    assert v & ~L.log_mask(L.CORE_PLR) == 0


def test_temperature_energy_units_and_wrap():
    assert L.pkg_temp_c(25 << 16, 100) == 75
    assert L.energy_unit_j(0x000A0E03) == 2.0 ** -14   # ESU = 14 (bits 12:8 of 0x0E03)
    assert L.energy_delta(0xFFFFFFF0, 0x10) == 0x20


def test_limiter_class_precedence():
    assert L.limiter_class(["PL1", "THERMAL"]) == "THERMAL_OR_ELEC"
    assert L.limiter_class(["PL2", "PL1"]) == "PL1"
    assert L.limiter_class(["TURBO_ATTEN"]) == "TRANSIENT"
    assert L.limiter_class([]) == "NONE"


def test_parse_cpus():
    assert L.parse_cpus("0-3,8, 2") == [0, 1, 2, 3, 8]


class FakeMsr:
    """Register file with R/W-to-clear-log semantics on 0x64F/0x6B0 and an event hook."""

    def __init__(self):
        self.regs = {L.MSR_RAPL_POWER_UNIT: 0x0E03, L.MSR_PKG_THERM_STATUS: 20 << 16,
                     L.MSR_PKG_ENERGY_STATUS: 0, L.MSR_CORE_PLR: 0, L.MSR_GFX_PLR: 0}
        self.aperf = {}
        self.mperf = {}
        self.writes = []
        self.after_read = None  # called after each 0x64F read (an event landing mid-mark)

    def read(self, cpu, reg):
        if reg == L.MSR_APERF:
            return self.aperf.get(cpu, 0)
        if reg == L.MSR_MPERF:
            return self.mperf.get(cpu, 0)
        v = self.regs[reg]
        if reg == L.MSR_CORE_PLR and self.after_read:
            self.after_read()
        return v

    def write(self, cpu, reg, value):
        self.writes.append(reg)
        if reg in (L.MSR_CORE_PLR, L.MSR_GFX_PLR):
            table = L.CORE_PLR if reg == L.MSR_CORE_PLR else L.GFX_PLR
            m = L.log_mask(table)
            self.regs[reg] &= ~(m & ~value)  # a written 0 clears a log bit

    def run(self, cpus, mhz_ratio, joules, plr=0):
        for c in cpus:
            self.aperf[c] = self.aperf.get(c, 0) + int(1000 * mhz_ratio)
            self.mperf[c] = self.mperf.get(c, 0) + 1000
        self.regs[L.MSR_PKG_ENERGY_STATUS] = (self.regs[L.MSR_PKG_ENERGY_STATUS]
                                              + int(joules * 2 ** 14)) & 0xFFFFFFFF
        self.regs[L.MSR_CORE_PLR] |= plr


def test_ledger_without_clear_never_writes():
    m = FakeMsr()
    led = L.Ledger(m, [0, 1], base_mhz=1100.0, clear=False)
    m.run([0, 1], 2.0, 1.5, plr=(1 << 10) | (1 << 26))
    row = led.mark(0)
    assert m.writes == []
    assert row["core"] == ["PL1"]          # status bit 10 at the boundary
    assert row["bzy_mhz"] == pytest.approx(2200.0)
    assert row["pkg_j"] == pytest.approx(1.5, rel=1e-4)
    assert row["pkg_c"] == 80


def test_ledger_with_clear_writes_only_plr_registers():
    m = FakeMsr()
    led = L.Ledger(m, [0], base_mhz=1100.0, clear=True, gfx=True)
    m.run([0], 1.0, 1.0, plr=1 << 26)
    row = led.mark(0)
    assert row["core"] == ["PL1"]
    assert set(m.writes) <= {L.MSR_CORE_PLR, L.MSR_GFX_PLR}
    assert L.MSR_PKG_THERM_STATUS not in m.writes   # the kernel owns 0x1B1 log bits
    m.run([0], 1.0, 1.0)
    assert led.mark(1)["class"] == "NONE"            # the PL1 log was cleared


def test_event_between_read_and_clear_is_kept_for_next_step():
    m = FakeMsr()
    led = L.Ledger(m, [0], base_mhz=1100.0, clear=True)
    m.run([0], 1.0, 1.0, plr=1 << 26)

    def thermal_now():
        m.regs[L.MSR_CORE_PLR] |= 1 << 17
        m.after_read = None

    m.after_read = thermal_now
    assert led.mark(0)["core"] == ["PL1"]
    m.run([0], 1.0, 1.0)
    assert led.mark(1)["core"] == ["THERMAL"]


def test_socket_roundtrip():
    m = FakeMsr()
    led = L.Ledger(m, [0], base_mhz=1100.0, clear=True)
    path = os.path.join(tempfile.mkdtemp(), "ledger.sock")
    t = threading.Thread(target=L.serve, args=(led, path), daemon=True)
    t.start()
    for _ in range(100):
        if os.path.exists(path):
            break
        time.sleep(0.01)
    c = L.Client(path)
    for step in range(3):
        m.run([0], 1.0, 1.0, plr=(1 << 26) if step == 1 else 0)
        c.mark(step)
    s = c.summary()
    assert s["steps"] == 3
    assert s["classes"]["PL1"] == 1 and s["classes"]["NONE"] == 2
    assert s["clean_fraction"] == pytest.approx(2 / 3)

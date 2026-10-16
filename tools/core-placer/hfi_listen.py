#!/usr/bin/env python3
"""Passive probe: count intel_hfi capability-change events for N seconds.

Usage: hfi_listen.py [seconds]
Prints one JSON line per event batch (time, CPUs changed) and a summary with
events/min and how many tracker rewrites the hysteresis would have issued.
Gate for claim (2): fewer than 1 change/min under load refutes HFI reactivity.
"""
import json
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coreplacer import hfi, perf  # noqa: E402


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    cpus = perf.parse_cpulist(open("/sys/devices/system/cpu/online").read())
    p_cpus = perf.parse_cpulist(open("/sys/devices/cpu_core/cpus").read()) if os.path.exists("/sys/devices/cpu_core/cpus") else cpus
    tr = hfi.CapabilityTracker(cpus, p_cpus)
    lst = hfi.Listener()
    t0 = time.monotonic()
    batches, rewrites, last = 0, 0, {}
    while (now := time.monotonic() - t0) < secs:
        r, _, _ = select.select([lst.sock], [], [], 0.5)
        if r:
            for _mtype, payload in hfi.iter_messages(lst.sock.recv(65536)):
                caps = hfi.parse_capability_event(payload)
                if not caps:
                    continue
                changed = {c: v for c, v in caps.items() if last.get(c) != v}
                last.update(caps)
                batches += 1
                print(json.dumps({"t": round(now, 3), "cpus": len(caps), "changed": changed}), flush=True)
                rewrites += tr.update(now, caps) is not None
        rewrites += tr.tick(now) is not None
    print(json.dumps({"seconds": secs, "event_batches": batches, "per_min": round(batches * 60 / secs, 2),
                      "tracker_rewrites": rewrites}))


if __name__ == "__main__":
    main()

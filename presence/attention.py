"""Human attention signal: logind session state + input events -> present|away.

The state machine is event-driven and deterministic so it can be replayed in
simulation and unit tests. Live sources (logind over `loginctl`, DRM sysfs) are
thin adapters at the bottom of this file.
"""

import os
import subprocess

PRESENT, AWAY = "present", "away"


class Attention:
    """present -> away after `t_away` s with no input while unlocked, or at once
    on lock / session inactive / logind IdleHint. Any input event, unlock or
    session activation returns to present on that event (no hysteresis on the
    way back: rollback must be immediate)."""

    def __init__(self, t_away=120.0, now=0.0):
        self.t_away = t_away
        self.state = PRESENT
        self.last_input = now
        self.locked = False
        self.active = True
        self.since = now

    def _set(self, state, now):
        if state != self.state:
            self.state, self.since = state, now
            return True
        return False

    def on_input(self, now):
        self.last_input = now
        if self.locked or not self.active:
            return False
        return self._set(PRESENT, now)

    def on_session(self, now, locked=None, active=None, idle_hint=None):
        was_blocked = self.locked or not self.active
        if locked is not None:
            self.locked = locked
        if active is not None:
            self.active = active
        if self.locked or not self.active or idle_hint:
            return self._set(AWAY, now)
        if was_blocked:  # unlock / session switch-back is a human action
            self.last_input = now
            return self._set(PRESENT, now)
        return False

    def tick(self, now):
        """Timer callback. Returns True when the state changed."""
        if self.state == PRESENT and now - self.last_input >= self.t_away:
            return self._set(AWAY, now)
        return False


def replay(input_times, horizon, t_away=120.0, tick=0.5):
    """Feed sorted input-event timestamps through Attention. Returns the list of
    (time, state) transitions. Only event timestamps are visible to the machine."""
    a = Attention(t_away=t_away, now=0.0)
    out = [(0.0, a.state)]
    events = iter(input_times)
    nxt = next(events, None)
    t = 0.0
    while t <= horizon:
        while nxt is not None and nxt <= t:
            if a.on_input(nxt):
                out.append((nxt, a.state))
            nxt = next(events, None)
        if a.tick(t):
            out.append((t, a.state))
        t += tick
    return out


# ---- live adapters (read-only) -------------------------------------------

def parse_loginctl(text):
    """Parse `loginctl show-session -p IdleHint -p LockedHint -p Active` output."""
    kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    yes = lambda k: kv.get(k, "no") == "yes"  # noqa: E731
    return {"idle_hint": yes("IdleHint"), "locked": yes("LockedHint"),
            "active": yes("Active"), "type": kv.get("Type", "")}


def read_session(session_id):
    out = subprocess.run(
        ["loginctl", "show-session", str(session_id), "-p", "IdleHint",
         "-p", "LockedHint", "-p", "Active", "-p", "Type", "--no-pager"],
        capture_output=True, text=True, check=True).stdout
    return parse_loginctl(out)


def display_gpu(sysroot="/"):
    """PCI vendor ids of DRM cards with a connected connector. A connected
    connector on the 0x8086 card means the desktop scans out of the iGPU."""
    drm = os.path.join(sysroot, "sys/class/drm")
    found = {}
    try:
        names = sorted(os.listdir(drm))
    except OSError:
        return found
    for name in names:
        if "-" not in name or not name.startswith("card"):
            continue
        try:
            with open(os.path.join(drm, name, "status")) as f:
                status = f.read().strip()
            card = name.split("-", 1)[0]
            with open(os.path.join(drm, card, "device", "vendor")) as f:
                vendor = f.read().strip()
        except OSError:
            continue
        if status == "connected":
            found.setdefault(card, {"vendor": vendor, "connectors": []})
            found[card]["connectors"].append(name.split("-", 1)[1])
    return found

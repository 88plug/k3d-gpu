"""intel_hfi capability events over thermal generic netlink, plus a hysteresis tracker.

Wire format (Linux v6.6 drivers/thermal/thermal_netlink.c, include/uapi/linux/thermal.h):
genl family "thermal", multicast group "event", command
THERMAL_GENL_EVENT_CPU_CAPABILITY_CHANGE (14). One nested attr
THERMAL_GENL_ATTR_CPU_CAPABILITY (20) holds repeated u32 triples
ID (21), PERFORMANCE (22), EFFICIENCY (23). intel_hfi.c sends perf_cap << 2 and
ee_cap << 2 (0..1020), at most 16 CPUs per message, batched at HFI_UPDATE_INTERVAL (HZ).
"""
import socket
import struct

NETLINK_GENERIC = 16
SOL_NETLINK = 270
NETLINK_ADD_MEMBERSHIP = 1
GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2
CTRL_ATTR_MCAST_GROUPS = 7
CTRL_ATTR_MCAST_GRP_NAME = 1
CTRL_ATTR_MCAST_GRP_ID = 2
NLA_TYPE_MASK = 0x3FFF

THERMAL_GENL_EVENT_CPU_CAPABILITY_CHANGE = 14
THERMAL_GENL_ATTR_CPU_CAPABILITY = 20
THERMAL_GENL_ATTR_CPU_CAPABILITY_ID = 21
THERMAL_GENL_ATTR_CPU_CAPABILITY_PERFORMANCE = 22
THERMAL_GENL_ATTR_CPU_CAPABILITY_EFFICIENCY = 23


def _align4(n):
    return (n + 3) & ~3


def iter_attrs(buf):
    off = 0
    while off + 4 <= len(buf):
        nla_len, nla_type = struct.unpack_from("<HH", buf, off)
        if nla_len < 4 or off + nla_len > len(buf):
            raise ValueError("truncated netlink attribute")
        yield nla_type & NLA_TYPE_MASK, buf[off + 4: off + nla_len]
        off += _align4(nla_len)


def iter_messages(data):
    off = 0
    while off + 16 <= len(data):
        length, mtype, _flags, _seq, _pid = struct.unpack_from("<IHHII", data, off)
        if length < 16 or off + length > len(data):
            raise ValueError("truncated netlink message")
        yield mtype, data[off + 16: off + length]
        off += _align4(length)


def parse_capability_event(genl_payload):
    """genl_payload = bytes after nlmsghdr. Returns {cpu: (perf, eff)} or None."""
    cmd = genl_payload[0]
    if cmd != THERMAL_GENL_EVENT_CPU_CAPABILITY_CHANGE:
        return None
    caps, cpu = {}, None
    for atype, val in iter_attrs(genl_payload[4:]):
        if atype != THERMAL_GENL_ATTR_CPU_CAPABILITY:
            continue
        perf = None
        for t, v in iter_attrs(val):
            if len(v) != 4:
                raise ValueError("capability attr is not u32")
            (x,) = struct.unpack("<I", v)
            if t == THERMAL_GENL_ATTR_CPU_CAPABILITY_ID:
                cpu = x
            elif t == THERMAL_GENL_ATTR_CPU_CAPABILITY_PERFORMANCE:
                perf = x
            elif t == THERMAL_GENL_ATTR_CPU_CAPABILITY_EFFICIENCY:
                caps[cpu] = (perf, x)
    return caps


def _attr(atype, payload):
    return _pad(struct.pack("<HH", 4 + len(payload), atype) + payload)


def _pad(b):
    return b + b"\0" * (_align4(len(b)) - len(b))


def build_capability_event(caps, family_id=0x20, nested_flag=True):
    """Encode a message the way the v6.6 kernel does (used by tests)."""
    inner = b"".join(
        _attr(THERMAL_GENL_ATTR_CPU_CAPABILITY_ID, struct.pack("<I", c))
        + _attr(THERMAL_GENL_ATTR_CPU_CAPABILITY_PERFORMANCE, struct.pack("<I", p))
        + _attr(THERMAL_GENL_ATTR_CPU_CAPABILITY_EFFICIENCY, struct.pack("<I", e))
        for c, (p, e) in sorted(caps.items()))
    ntype = THERMAL_GENL_ATTR_CPU_CAPABILITY | (0x8000 if nested_flag else 0)
    body = struct.pack("BBH", THERMAL_GENL_EVENT_CPU_CAPABILITY_CHANGE, 2, 0) + _attr(ntype, inner)
    return struct.pack("<IHHII", 16 + len(body), family_id, 0, 0, 0) + body


class Listener:
    """Joins the thermal "event" multicast group. Needs no privilege to listen."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.family_id, groups = self._resolve("thermal")
        self.sock.setsockopt(SOL_NETLINK, NETLINK_ADD_MEMBERSHIP, groups["event"])

    def _resolve(self, name):
        body = struct.pack("BBH", CTRL_CMD_GETFAMILY, 1, 0) + _attr(CTRL_ATTR_FAMILY_NAME, name.encode() + b"\0")
        self.sock.send(struct.pack("<IHHII", 16 + len(body), GENL_ID_CTRL, 1, 1, 0) + body)
        fam, groups = None, {}
        for _mtype, payload in iter_messages(self.sock.recv(65536)):
            for t, v in iter_attrs(payload[4:]):
                if t == CTRL_ATTR_FAMILY_ID:
                    fam = struct.unpack("<H", v[:2])[0]
                elif t == CTRL_ATTR_MCAST_GROUPS:
                    for _i, grp in iter_attrs(v):
                        g = dict(iter_attrs(grp))
                        groups[g[CTRL_ATTR_MCAST_GRP_NAME].rstrip(b"\0").decode()] = \
                            struct.unpack("<I", g[CTRL_ATTR_MCAST_GRP_ID])[0]
        if fam is None:
            raise OSError(f"genl family {name!r} not found")
        return fam, groups

    def events(self):
        while True:
            for mtype, payload in iter_messages(self.sock.recv(65536)):
                if mtype == self.family_id:
                    caps = parse_capability_event(payload)
                    if caps:
                        yield caps


class CapabilityTracker:
    """Turns raw per-CPU capability updates into committed CPU pools.

    available = CPUs with perf_cap > 0; p_pool = available CPUs with
    perf_cap >= p_frac * max perf_cap. A CPU's membership change commits only
    after it has held for `hold_s`, and at most `max_per_min` commits happen per
    minute, so a CR rewrite never follows a sub-hold flap.
    """

    def __init__(self, cpus, p_cpus, hold_s=2.0, max_per_min=6, p_frac=0.75):
        self.hold_s = hold_s
        self.max_per_min = max_per_min
        self.p_frac = p_frac
        self.caps = {c: (1020 if c in p_cpus else 512, 512) for c in cpus}
        self.committed = self._desired()
        self.pending_since = {}
        self.commit_times = []

    def _desired(self):
        avail = {c for c, (p, _e) in self.caps.items() if p > 0}
        top = max((self.caps[c][0] for c in avail), default=0)
        pool = {c for c in avail if self.caps[c][0] >= self.p_frac * top}
        return {c: (c in avail, c in pool) for c in self.caps}

    def update(self, now, caps):
        self.caps.update({c: v for c, v in caps.items() if c in self.caps})
        return self.tick(now)

    def tick(self, now):
        """Returns (available, p_pool) when a rewrite is due, else None."""
        desired = self._desired()
        ready = False
        for c, state in desired.items():
            if state == self.committed[c]:
                self.pending_since.pop(c, None)
                continue
            since = self.pending_since.setdefault(c, (now, state))
            if since[1] != state:
                self.pending_since[c] = (now, state)
            elif now - since[0] >= self.hold_s:
                ready = True
        self.commit_times = [t for t in self.commit_times if now - t < 60.0]
        if not ready or len(self.commit_times) >= self.max_per_min:
            return None
        for c, (t0, state) in list(self.pending_since.items()):
            if now - t0 >= self.hold_s:
                self.committed[c] = state
                del self.pending_since[c]
        self.commit_times.append(now)
        return self.pools()

    def pools(self):
        avail = {c for c, (a, _p) in self.committed.items() if a}
        pool = {c for c, (_a, p) in self.committed.items() if p}
        return avail, pool

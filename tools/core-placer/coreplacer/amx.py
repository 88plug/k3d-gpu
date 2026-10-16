"""Promote pods that use AMX into whole-core balloons; demote them when they stop.

Signal: EXE.AMX_BUSY cycles / task-clock cycles per window (perf.amx_busy()).
Promotion needs `promote_after` consecutive windows above `hi`, demotion
`demote_after` consecutive windows below `lo`; a rate between lo and hi resets
both counters, so a pod dithering between the thresholds never moves.
"""


class AmxFsm:
    def __init__(self, hi=0.05, lo=0.01, promote_after=3, demote_after=5):
        assert lo < hi
        self.hi, self.lo = hi, lo
        self.promote_after, self.demote_after = promote_after, demote_after
        self.state = {}

    def update(self, pod, busy_frac):
        """Returns 'promote', 'demote' or None."""
        st = self.state.setdefault(pod, {"amx": False, "up": 0, "down": 0})
        if busy_frac > self.hi:
            st["up"], st["down"] = st["up"] + 1, 0
        elif busy_frac < self.lo:
            st["up"], st["down"] = 0, st["down"] + 1
        else:
            st["up"] = st["down"] = 0
        if not st["amx"] and st["up"] >= self.promote_after:
            st["amx"], st["up"] = True, 0
            return "promote"
        if st["amx"] and st["down"] >= self.demote_after:
            st["amx"], st["down"] = False, 0
            return "demote"
        return None

    def forget(self, pod):
        self.state.pop(pod, None)

# Dialectic designs and the review of #157 (2026-10-20)

Design records written on 2026-10-20 in the coordinating session's scratchpad and committed here
on 2026-10-28. Apart from this README, every file is a byte-for-byte copy of the scratchpad file
of the same name.

| File | What it is |
|------|------------|
| `dialectic-designs.json` | One record per design item: C1gate, C1slots, C2, C3, D1, D2, D3 and D4. Each has `analyze` (the first analysis, read from the code at a named commit), `cross` (a second analysis that challenges it), `reconcile` (the design both sides keep) and `refute` (an adversarial review: a `verdict` and its `reasons`). |
| `review-157.md` | The review of PR #157 (heads `b8f47c3e` and `23c024dd`) against those records, with the checks it ran. |
| `qatprobe.py` | The script behind that review's QAT-grid check, cited there as `scratchpad/qatprobe.py`. It imports `qat_grid` from #157's worktree (`wt157/bench/models`, now `bench/models/qat_grid.py` on main) and reads the checkpoint by HTTP range; it needs `numpy`. |

Where the items landed: C1gate in #161, C1slots in #168, C2 in #183, C3 in #164, D4 in #165, and
D1 (rule L3) and D2 (read-only model binds) in #157. D3's `refute` verdict is `kill`.

Absolute paths under `/tmp` inside these records point at scratchpad files that were not kept;
what those files became is on main. Nothing here runs in CI.

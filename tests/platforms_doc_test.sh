#!/usr/bin/env bash
# Checks docs/platforms.md against the sim fixture goldens:
#   1. every fixture with an up.golden has a row, and every row names a fixture
#   2. the Auto config cell says "NRI balloons (" exactly when up.golden rolls out
#      daemonset/nri-resource-policy-balloons, and "no NRI balloons" exactly when
#      up.golden passes --disable=nri-balloons
#   3. the Predicted behaviour cell ends with the plan.json decision in backticks
# "as above" in a cell repeats the previous row's cell.
# Needs bash, python3.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOC="${DOC:-${ROOT}/docs/platforms.md}"
FIX="${ROOT}/tests/fixtures"

python3 - "${DOC}" "${FIX}" <<'PY'
import json, os, re, sys

doc, fix = sys.argv[1], sys.argv[2]
passed = failed = 0

def check(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        print(f"FAIL {name}")

rows, prev = {}, None
for line in open(doc, encoding="utf-8"):
    if not line.startswith("| ") or line.startswith("| Family") or line.startswith("|---"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if prev is not None:
        cells = [p if c == "as above" else c for c, p in zip(cells, prev)]
    prev = cells
    m = re.fullmatch(r"`([^`]+)`", cells[1])
    if m:
        rows[m.group(1)] = cells

fixtures = sorted(d for d in os.listdir(fix) if os.path.isfile(os.path.join(fix, d, "up.golden")))
for f in fixtures:
    check(f in rows, f"{f}: has a row")
for f in sorted(rows):
    check(f in fixtures, f"{f}: row names a fixture with an up.golden")

for f in fixtures:
    if f not in rows:
        continue
    up = open(os.path.join(fix, f, "up.golden"), encoding="utf-8").read()
    auto, pred = rows[f][3], rows[f][4]
    rolled = "rollout status daemonset/nri-resource-policy-balloons" in up
    disabled = "--disable=nri-balloons" in up
    check(rolled != disabled, f"{f}: up.golden either runs or disables NRI balloons")
    check(("NRI balloons (" in auto) == rolled, f"{f}: Auto config balloons cell matches up.golden (rolled={rolled})")
    check(("no NRI balloons" in auto) == disabled, f"{f}: Auto config 'no NRI balloons' matches up.golden (disabled={disabled})")
    decision = json.load(open(os.path.join(fix, f, "plan.json"), encoding="utf-8"))["decision"]
    check(pred.endswith(f"`{decision}`"), f"{f}: Predicted behaviour ends with plan decision `{decision}`")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY

# promote-gate (prototype)

Checks a release tested on a k3d-gpu laptop before it goes to a prod Xeon node
running the nri-plugins balloons policy. Static: no cluster is needed to run it.

1. **Twin.** `twin-capture.yml` (ansible) records the prod node's CPU/NUMA sysfs
   (`capture-globs.txt`), its allocatable and its BalloonsPolicy CR.
   `promote_gate.py capture / <dir>` records the same files from the local host.
2. **Projection.** `promote_gate.py project` turns the helm-rendered prod release
   into the dev release: CPU and memory scaled by the twins' allocatable ratio, memory
   clamped at per-component floors, and prod balloon labels mapped to the k3d-gpu
   `k3d-gpu.io/cores` labels through `labelTable` (see `testdata/projection-map.yaml`).
3. **Certify.** `promote_gate.py certify` recomputes that projection from the prod
   release and requires the dev release to equal it, quantities compared by value.
   It does not trust anything recorded on the dev objects. A path-filtered diff
   (dyff or `kubectl diff` with an ignore list) skips the resource and label paths
   entirely. Certify also checks the values on those paths, so it catches prod
   resources that changed after dev testing, off-table balloon labels and dropped
   memory floors.
4. **Replay.** `promote_gate.py replay` creates the release's pods in apply order,
   and then in N seeded random orders, against the prod twin. It uses a reduced
   model of the balloons policy (`model_assumptions()` lists what it leaves out) to
   predict each balloon's cpuset, NUMA span and memory nodes. It reports
   unschedulable pods, balloon CPU exhaustion, cross-NUMA inference balloons (CPU or
   memory), order-dependent placement, unsatisfiable `preferCoreType`, unmatched
   balloon labels and `maxCPUs` overruns.

`check` runs certify and replay together and exits 1 on any finding.

```
helm template stack testdata/chart -f testdata/values-prod.yaml > prod.yaml
helm template stack testdata/chart -f testdata/values-dev.yaml  > dev.yaml
python3 promote_gate.py check --map testdata/projection-map.yaml \
  --prod-twin testdata/twins/xeon-2s-snc-112c --dev-twin testdata/twins/i9-13900t prod.yaml dev.yaml
```

Tests and falsification sims: `python3 tests/promote_gate_test.py` (needs helm and PyYAML).

The replay models the balloons policy; it is not the upstream engine. Its cpuset
predictions have not been compared with a live balloons deploy. The twins
`xeon-2s-snc-112c` and `xeon-2s-snc-88c` are filtered from containers/nri-plugins
`pkg/sysfs/test-data-sample{2,3}.tar.xz` (Apache-2.0). `i9-13900t` was captured
from the machine this was written on.

The projection runs at render time. A MutatingAdmissionPolicy could apply it at
admission instead, but the k3s that k3d-gpu pins (v1.34) has that API only as beta
and off by default: it needs the `MutatingAdmissionPolicy` feature gate plus
`--runtime-config=admissionregistration.k8s.io/v1beta1=true`. It is GA in 1.36.

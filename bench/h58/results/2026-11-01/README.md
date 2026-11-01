# H58 on crescent, 2026-11-01

The records of EXPERIMENTS.md "H58 run (Xeon 6741P, 2026-11-01)", under amendments R1 and R2.

- `precheck/`: amendment R1's read-only `k3d-gpu plan --model <id> --explain` outputs
  (09:32-09:35Z, main `ef639c1d`, empty cache and state, no cluster). They were not an H58 step.
- `stress-ng-install.txt`: the pinned stress-ng V0.22.01 build (`bench/h58/stress-ng.sh install`),
  under the bench lock, before run 1.
- `run1/`: run 1 (10:13:40-10:24:43Z, `e0a48ad6`), which is not valid (amendment R2). R1's interlock
  deleted the Intel GPU plugin's pod 985 times. `interlock/events.jsonl` lists the deletions, and
  `interlock/objects.tar.gz` holds the deleted pod objects. `score.txt` is R1's scorer's output.
  `score.json` is the re-score under R2: NOT VALID.
- `run2/`: run 2 (10:31:39-10:37:31Z, `d904cae2`), the valid run, scored REFUTED.
  - `steps.jsonl` lists every k3d-gpu call, its exit, its time and its `K3D_GPU_*` variables (none).
  - `up.log`, `detect.txt`, `detect.json`, `node.json` and `node-settle.txt` hold `up` and the node
    as advertised.
  - `m-gemma/`, `m-qwen27b/` and `m-flashnext/` each hold `plan.txt` (the record taken before),
    `agent-up.log`, `placement.json`, `deployments.json` and `pods.txt`.
  - `b24/status.json` and `squeeze/status.json` say why neither ran.
  - `containers-pre.json` and `containers-post.json` record every running container's restart
    count and state. `docker-events.jsonl` has its secret-looking labels blanked.
    `dmesg-oom-*.txt` and `meminfo-*.txt` are host reads.
  - `frontdoor-pre.txt` and `frontdoor-post.txt` are a chat through Dynamo `:8100`, counted on each
    serving pair.
  - `pins.json`, `inventory.txt` and `inventory-diff.txt` (empty), `clusters-pre.txt` and
    `cache-aside.txt` are the start checks.
  - `k3d-gpu-dirs/` holds the run's own k3d-gpu metadata cache.
  - `hold/` is `hold.sh`'s records, `serving.jsonl` among them.
  - `score.json` is `bench/h58/h58.py score run2`.

No kubeconfig is in these records. The cluster's join token, which k3d puts in its node
containers' labels, is blanked in the Docker event records.

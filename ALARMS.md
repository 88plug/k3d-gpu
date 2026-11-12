# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-12T00:19:46Z. 2 open.

## `blind-spot:gguf-huggingface-hub`: gguf-huggingface-hub is 13d behind upstream with no open PR

Opened 2026-11-12T00:10:50Z, updated 2026-11-12T00:10:50Z, acknowledged.

Pin `gguf-huggingface-hub` (group gguf-convert-pip) is at 2.3.0; upstream 2.4.0 was published 2026-10-29T15:30:10.421769Z (13d ago, limit 72 h). No autobump/gguf-convert-pip PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.

## `blind-spot:gguf-transformers`: gguf-transformers is 5d behind upstream with no open PR

Opened 2026-11-12T00:10:56Z, updated 2026-11-12T00:10:56Z, acknowledged.

Pin `gguf-transformers` (group gguf-convert-pip) is at 5.21.0; upstream 5.22.2 was published 2026-11-06T15:42:54.257524Z (5d ago, limit 72 h). No autobump/gguf-convert-pip PR is open: the bumper is not moving it. Check the Freshness run's bump job for this group.


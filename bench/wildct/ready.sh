#!/usr/bin/env bash
# ready.sh: one poll of the wildct pod wait (wildct amendments R1 and R3), run by row.sh's smoke
# and h52w.sh. Exits 0 when at least one node is listed and every node is Ready, and at least one
# pod is listed and every pod is Completed, or Running with every container ready (READY n/n).
# Each kubectl read is bounded (30 s); one that fails or does not answer is not ready.
# STATUS alone is not enough: in smoke3 (2026-10-29) k3s restarted every ~16 s, the node was
# NotReady and coredns 0/1, and every pod still read Running.
set -uo pipefail
timeout -k 5 30 kubectl get nodes --no-headers 2>/dev/null \
    | awk '$2 != "Ready" {bad++} END {exit !(NR > 0 && bad == 0)}' || exit 1
timeout -k 5 30 kubectl get pods -A --no-headers 2>/dev/null \
    | awk '$4 == "Completed" {next}
           $4 != "Running" {bad++; next}
           {split($3, r, "/"); if (r[1] != r[2]) bad++}
           END {exit !(NR > 0 && bad == 0)}'

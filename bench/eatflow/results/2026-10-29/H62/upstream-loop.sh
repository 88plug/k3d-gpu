#!/usr/bin/env bash
# H62 re-run trigger, read every 10 min while the campaign runs (records only).
while [ -e "/home/devcloud/k3dgpu-bench/results/H62/.campaign-running" ]; do
  { date -u +%FT%TZ; timeout 120 bash "/home/devcloud/k3dgpu-bench/k3d-gpu-h62/bench/h62/upstream.sh" 2>&1; } >> "/home/devcloud/k3dgpu-bench/results/H62/upstream.log"
  sleep 600
done

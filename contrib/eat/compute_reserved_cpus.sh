#!/bin/bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Prints NRI_RESERVED_CPU_LIST=<cpus>: TOTAL_RESERVED CPUs spread evenly over the
# NUMA nodes. Each node gives whole physical cores (a CPU and its hyperthread
# siblings, from topology/thread_siblings_list), lowest CPU ids first. On a hybrid
# CPU (both /sys/devices/cpu_core and cpu_atom list CPUs) E-cores are taken before
# P-cores, so the reserve does not eat the cores inference runs on.
# SYSFS overrides the sysfs root (tests point it at a fake tree).
set -euo pipefail

TOTAL_RESERVED=${1:-}
if ! [[ "$TOTAL_RESERVED" =~ ^[0-9]+$ ]] || [ "$TOTAL_RESERVED" -le 0 ]; then
  echo "Error: missing or invalid TOTAL_RESERVED argument (must be > 0)" >&2
  exit 1
fi
SYSFS=${SYSFS:-/sys}

# Kernel CPU list ("0-3,8,10-11") -> one CPU number per line.
expand_cpulist() {
  local part
  for part in ${1//,/ }; do
    if [[ "$part" == *-* ]]; then seq "${part%-*}" "${part#*-}"; else echo "$part"; fi
  done
}

# NUMA nodes as CPU lists, one per line; a kernel without NUMA sysfs is one node.
numa_cpulists() {
  local node cpus found=false
  for node in $(printf '%s\n' "$SYSFS"/devices/system/node/node[0-9]* | sort -V); do
    # sysfs files report a size even when empty, so test the content (memory-only nodes have no CPUs).
    cpus=$(cat "$node/cpulist" 2>/dev/null || true)
    [ -n "$cpus" ] || continue
    found=true
    echo "$cpus"
  done
  if [ "$found" = false ]; then cat "$SYSFS/devices/system/cpu/online" 2>/dev/null || true; fi
}

declare -A ecore=()
atom=$(cat "$SYSFS/devices/cpu_atom/cpus" 2>/dev/null || true)
core=$(cat "$SYSFS/devices/cpu_core/cpus" 2>/dev/null || true)
if [ -n "$atom" ] && [ -n "$core" ]; then
  for cpu in $(expand_cpulist "$atom"); do ecore[$cpu]=1; done
fi

numa_lists=$(numa_cpulists)
total_numa=$(grep -c . <<< "$numa_lists" || true)
if [ "$total_numa" -eq 0 ]; then
  echo "Error: failed to read NUMA node CPU lists from $SYSFS" >&2
  exit 1
fi
cpus_per_numa=$(( (TOTAL_RESERVED + total_numa - 1) / total_numa ))
out=""

while read -r cpulist; do
  declare -A in_node=() seen=()
  for cpu in $(expand_cpulist "$cpulist"); do in_node[$cpu]=1; done
  # One line per physical core: "<rank> <first cpu> <cpus in the core>", rank 0 = E-core.
  cores=$(
    for cpu in $(printf '%s\n' "${!in_node[@]}" | sort -n); do
      [ -z "${seen[$cpu]:-}" ] || continue
      siblings=$(cat "$SYSFS/devices/system/cpu/cpu$cpu/topology/thread_siblings_list" 2>/dev/null || echo "$cpu")
      members=""
      for s in $(expand_cpulist "$siblings"); do
        [ -n "${in_node[$s]:-}" ] || continue
        seen[$s]=1
        members="$members $s"
      done
      echo "$([ -n "${ecore[$cpu]:-}" ] && echo 0 || echo 1) $cpu$members"
    done | sort -k1,1n -k2,2n
  )
  selected=()
  while read -r _ _ members; do
    for cpu in $members; do
      [ "${#selected[@]}" -lt "$cpus_per_numa" ] || break 2
      selected+=("$cpu")
    done
  done <<< "$cores"
  for cpu in $(printf '%s\n' "${selected[@]}" | sort -n); do out="${out}${cpu},"; done
  unset in_node seen
done <<< "$numa_lists"

out="${out%,}"
if [ -z "$out" ]; then
  echo "Error: failed to compute reserved CPU list (empty result)" >&2
  exit 1
fi
echo "NRI_RESERVED_CPU_LIST=$out"

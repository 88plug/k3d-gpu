#!/usr/bin/env python3
"""Tests for bench/wildct's Python: sample.py's container list from `docker ps` is bounded by a
timeout and keeps the last list when docker does not answer (EXPERIMENTS.md "Wildct
amendment R1"), its /proc/stat arithmetic, and gpu_scan.py's pod GPU scan (rule W8)."""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_scan  # noqa: E402
import sample  # noqa: E402


class ContainersTest(unittest.TestCase):
    def test_rows_map_names_to_cgroup_cpu_stat(self):
        out = subprocess.CompletedProcess([], 0, stdout="abc123 k3dgpu-w-rapl\ndef456 searxng-core\n", stderr="")
        with mock.patch.object(sample.subprocess, "run", return_value=out):
            rows = sample.containers()
        self.assertEqual(rows, {
            "k3dgpu-w-rapl": "/sys/fs/cgroup/system.slice/docker-abc123.scope/cpu.stat",
            "searxng-core": "/sys/fs/cgroup/system.slice/docker-def456.scope/cpu.stat",
        })

    def test_docker_ps_is_bounded(self):
        out = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(sample.subprocess, "run", return_value=out) as run:
            sample.containers()
        self.assertEqual(run.call_args.kwargs.get("timeout"), sample.DOCKER_T)
        self.assertGreater(sample.DOCKER_T, 0)

    def test_docker_ps_timeout_keeps_the_last_list(self):
        last = {"k3dgpu-w-rapl": "/x"}
        with mock.patch.object(sample.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 20)):
            self.assertEqual(sample.containers(last), last)
            self.assertEqual(sample.containers(), {})

    def test_docker_ps_failure_keeps_the_last_list(self):
        last = {"a": "/x"}
        out = subprocess.CompletedProcess([], 1, stdout="", stderr="Cannot connect to the Docker daemon")
        with mock.patch.object(sample.subprocess, "run", return_value=out):
            self.assertEqual(sample.containers(last), last)


class CpuTest(unittest.TestCase):
    def test_busy_cpu_seconds(self):
        prev = sample.cpu_times("cpu  1 0 0 0\ncpu0 100 0 0 900 0 0 0 0\ncpu1 0 0 0 1000 0 0 0 0\n")
        cur = sample.cpu_times("cpu  1 0 0 0\ncpu0 600 0 0 900 0 0 0 0\ncpu1 0 0 0 1500 0 0 0 0\n")
        self.assertEqual(sample.busy(prev, cur, [0, 1], hz=100.0, dt=5.0), 1.0)


def pod(name, volumes=(), limits=None, requests=None, init=False):
    c = {"name": "c", "resources": {"limits": limits or {}, "requests": requests or {}}}
    spec = {"volumes": [{"name": f"v{i}", "hostPath": {"path": p}} for i, p in enumerate(volumes)],
            "containers": [] if init else [c], "initContainers": [c] if init else []}
    return {"metadata": {"namespace": "ns", "name": name}, "spec": spec}


class GpuScanTest(unittest.TestCase):
    def test_clean_cluster(self):
        lines, bad = gpu_scan.scan({"items": [pod("nri", ["/run/nri", "/sys"]), pod("nfd", ["/dev/cpu"])]})
        self.assertEqual(bad, [])
        self.assertEqual(lines, ["ns/nri hostPath /run/nri", "ns/nri hostPath /sys", "ns/nfd hostPath /dev/cpu"])

    def test_gpu_mounts_and_resources_are_dirty(self):
        items = [pod("a", ["/dev/dri"]), pod("b", ["/dev/nvidia0"]), pod("c", ["/dev"]),
                 pod("d", limits={"nvidia.com/gpu": "1"}), pod("e", requests={"gpu.intel.com/i915": "1"}, init=True)]
        _, bad = gpu_scan.scan({"items": items})
        self.assertEqual(bad, ["ns/a mounts /dev/dri", "ns/b mounts /dev/nvidia0", "ns/c mounts /dev",
                               "ns/d asks nvidia.com/gpu", "ns/e asks gpu.intel.com/i915"])

    def test_empty_dir_volumes_are_not_host_paths(self):
        p = pod("x")
        p["spec"]["volumes"] = [{"name": "tmp", "emptyDir": {}}]
        lines, bad = gpu_scan.scan({"items": [p]})
        self.assertEqual((lines, bad), (["ns/x hostPath "], []))


if __name__ == "__main__":
    unittest.main()

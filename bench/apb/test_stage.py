#!/usr/bin/env python3
"""Unit tests for bench/apb/stage.py. Run: python3 bench/apb/test_stage.py"""

import copy
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import yaml
except ImportError:  # stage.py needs PyYAML; skip rather than fail where it is absent
    yaml = None

if yaml is not None:
    import stage

OVMS = [
    {"kind": "Namespace", "metadata": {"name": "k3d-gpu-agent"}},
    {"kind": "PersistentVolumeClaim", "metadata": {"name": "ovms-models"}},
    {"kind": "Deployment", "spec": {"template": {"spec": {
        "containers": [{"name": "ovms", "args": ["--rest_port=8000"]}],
        "volumes": [{"name": "models", "persistentVolumeClaim": {"claimName": "ovms-models"}}]}}}},
]
EAT = [
    {"kind": "Secret", "metadata": {"name": "s"}},
    {"kind": "ConfigMap", "data": {"HF_HOME": "/data"}},
    {"kind": "PersistentVolumeClaim", "metadata": {"name": "p"}},
    {"kind": "Deployment", "spec": {"template": {"spec": {
        "containers": [{"name": "vllm",
                        "envFrom": [{"configMapRef": {"name": "c"}}, {"secretRef": {"name": "s"}}],
                        "args": ["--dtype", "bfloat16", "--model", "Qwen/Qwen3-4B", "--port", "2080"],
                        "readinessProbe": {"initialDelaySeconds": 120, "periodSeconds": 30}}],
        "volumes": [{"name": "model-volume", "persistentVolumeClaim": {"claimName": "p"}},
                    {"name": "tmp", "emptyDir": {}}]}}}},
]
CUDA = [
    {"kind": "Deployment", "spec": {"template": {"spec": {
        "containers": [{"name": "vllm", "env": [{"name": "HF_HOME", "value": "/cache"}],
                        "args": ["--model=Qwen/Qwen3-4B-AWQ", "--revision=74d4bd2b", "--port=8000"]}],
        "volumes": [{"name": "cache", "emptyDir": {}}, {"name": "shm", "emptyDir": {"medium": "Memory"}}]}}}},
]
HOST = {"path": "/var/apb-models", "type": "Directory"}


def deployment(docs):
    return next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]


@unittest.skipIf(yaml is None, "PyYAML not installed")
class StageTest(unittest.TestCase):
    def test_ovms_drops_pvc_and_mounts_host_dir(self):
        out = stage.stage("ovms", copy.deepcopy(OVMS))
        self.assertEqual([d["kind"] for d in out], ["Namespace", "Deployment"])
        self.assertEqual(deployment(out)["volumes"], [{"name": "models", "hostPath": HOST}])
        self.assertEqual(deployment(out)["containers"][0]["args"], ["--rest_port=8000"])

    def test_ovms2_two_graph_names_in_one_pod(self):
        docs = copy.deepcopy(OVMS)
        docs[2]["metadata"] = {"name": "ovms", "namespace": "k3d-gpu-agent"}
        ps = docs[2]["spec"]["template"]["spec"]
        ps["initContainers"] = [{"name": "pull", "args": ["--pull"]}]
        ps["containers"][0]["args"] = ["--rest_port=8000", "--model_path=/models/x", "--model_name=m",
                                       "--cache_dir=/models/.ov_cache", "--log_level=INFO"]
        g = os.path.join(os.path.dirname(HERE), "eatflow", "h47")
        out = stage.two_graphs(docs, "k3dgpu/M", [("a1", os.path.join(g, "graph-a1.pbtxt")),
                                                  ("a4", os.path.join(g, "graph-a4.pbtxt"))])
        self.assertEqual([d["kind"] for d in out], ["Namespace", "ConfigMap", "Deployment"])
        dep = deployment(out)
        self.assertNotIn("initContainers", dep)
        self.assertEqual(dep["containers"][0]["args"], ["--rest_port=8000", "--cache_dir=/models/.ov_cache",
                                                        "--log_level=INFO", "--config_path=/graphs/config.json"])
        self.assertIn({"name": "graphs", "configMap": {"name": "ovms-graphs"}}, dep["volumes"])
        data = out[1]["data"]
        cfg = __import__("json").loads(data["config.json"])
        self.assertEqual([m["config"]["name"] for m in cfg["model_config_list"]], ["a1", "a4"])
        self.assertIn('models_path: "/models/k3dgpu/M"', data["a1.pbtxt"])
        self.assertNotIn("draft_models_path", data["a1.pbtxt"])
        self.assertIn('draft_models_path: "/models/k3dgpu/M/OpenVINO-Qwen3-0.6B-int4-ov"', data["a4.pbtxt"])
        self.assertIn('draft_device: "CPU"', data["a4.pbtxt"])
        self.assertIn('tool_parser: "hermes3"', data["a4.pbtxt"])

    def test_vllm_local_model_and_offline(self):
        out = stage.stage("vllm", copy.deepcopy(EAT), "Qwen3-4B", "32768")
        self.assertEqual([d["kind"] for d in out], ["ConfigMap", "Deployment"])
        self.assertEqual(out[0]["data"]["HF_HUB_OFFLINE"], "1")
        c = deployment(out)["containers"][0]
        self.assertEqual(c["args"], ["--dtype", "bfloat16", "--model", "/data/Qwen3-4B", "--port", "2080",
                                     "--max-model-len", "32768"])
        self.assertEqual(c["envFrom"], [{"configMapRef": {"name": "c"}}])
        self.assertEqual(c["readinessProbe"]["initialDelaySeconds"], 10)
        self.assertEqual(deployment(out)["volumes"][0], {"name": "model-volume", "hostPath": HOST})
        self.assertEqual(deployment(out)["volumes"][1], {"name": "tmp", "emptyDir": {}})

    def test_cuda_local_model_without_revision(self):
        out = stage.stage("cuda", copy.deepcopy(CUDA), "Qwen3-4B-AWQ")
        c = deployment(out)["containers"][0]
        self.assertEqual(c["args"], ["--model=/cache/Qwen3-4B-AWQ", "--port=8000"])
        self.assertIn({"name": "HF_HUB_OFFLINE", "value": "1"}, c["env"])
        self.assertEqual(deployment(out)["volumes"][0], {"name": "cache", "hostPath": HOST})
        self.assertEqual(deployment(out)["volumes"][1]["emptyDir"], {"medium": "Memory"})

    def test_values_merge_and_kv(self):
        base = {"cpu": "4", "image": {"repository": "r", "tag": "t"},
                "modelConfigs": {"m": {"configMapValues": {"VLLM_CPU_KVCACHE_SPACE": "40", "X": "1"}}},
                "defaultModelConfigs": {"configMapValues": {"VLLM_CPU_KVCACHE_SPACE": "40"}}}
        out = stage.values(base, {"cpu": "8", "image": {"tag": "t@sha256:x"}}, "6")
        self.assertEqual(out["cpu"], "8")
        self.assertEqual(out["image"], {"repository": "r", "tag": "t@sha256:x"})
        self.assertEqual(out["modelConfigs"]["m"]["configMapValues"], {"VLLM_CPU_KVCACHE_SPACE": "6", "X": "1"})
        self.assertEqual(out["defaultModelConfigs"]["configMapValues"]["VLLM_CPU_KVCACHE_SPACE"], "6")

    def test_main_round_trips_files(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.yaml"), os.path.join(d, "out.yaml")
            with open(src, "w") as f:
                yaml.safe_dump_all(OVMS, f)
            self.assertEqual(stage.main(["ovms", src, dst]), 0)
            with open(dst) as f:
                self.assertEqual(len([x for x in yaml.safe_load_all(f) if x]), 2)

    def test_unknown_mode(self):
        with self.assertRaises(SystemExit):
            stage.stage("bogus", copy.deepcopy(OVMS))


if __name__ == "__main__":
    unittest.main()

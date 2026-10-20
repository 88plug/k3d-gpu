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

    def test_xpu_drops_host_gpu_libs_and_adds_tool_flags(self):
        # EAT PR #21 (c86c42b) xpu-values.yaml render: the host's Level Zero and OpenCL dirs by
        # hostPath, prepended to LD_LIBRARY_PATH. In a k3d node those paths are the node's own.
        mounts = [{"mountPath": "/data", "name": "model-volume"},
                  {"mountPath": "/host-gpu-libs", "name": "host-gpu-libs", "readOnly": True},
                  {"mountPath": "/usr/lib/x86_64-linux-gnu/libze_loader.so.1", "name": "host-gpu-libs",
                   "subPath": "libze_loader.so.1"},
                  {"mountPath": "/etc/OpenCL/vendors", "name": "host-opencl-vendors"},
                  {"mountPath": "/usr/lib/x86_64-linux-gnu/intel-opencl", "name": "host-intel-opencl"}]
        xpu = copy.deepcopy(EAT)
        ps = xpu[3]["spec"]["template"]["spec"]
        ps["containers"][0].update(volumeMounts=mounts, command=[
            "bash", "-c", "source /opt/intel/oneapi/setvars.sh --force && export LD_LIBRARY_PATH="
            "/host-gpu-libs:${LD_LIBRARY_PATH} && python3 -m vllm.entrypoints.openai.api_server \"$@\"", "--"])
        ps["volumes"] += [{"name": n, "hostPath": {"path": p, "type": "Directory"}} for n, p in (
            ("host-gpu-libs", "/usr/lib/x86_64-linux-gnu"), ("host-opencl-vendors", "/etc/OpenCL/vendors"),
            ("host-intel-opencl", "/usr/lib/x86_64-linux-gnu/intel-opencl"))]
        out = stage.stage("xpu", copy.deepcopy(xpu), "Qwen3-4B", "32768")
        d = deployment(out)
        c = d["containers"][0]
        self.assertEqual([m["name"] for m in c["volumeMounts"]], ["model-volume"])
        self.assertEqual([v["name"] for v in d["volumes"]], ["model-volume", "tmp"])
        self.assertEqual(c["command"][2], "source /opt/intel/oneapi/setvars.sh --force && "
                         "python3 -m vllm.entrypoints.openai.api_server \"$@\"")
        self.assertEqual(c["args"][-2:], ["--max-model-len", "32768"])        # as the vllm mode
        self.assertEqual(out[0]["data"]["HF_HUB_OFFLINE"], "1")
        # H53h: the tool flags EAT's catalog gives this model on the CPU, appended.
        out = stage.stage("xpu", copy.deepcopy(xpu), "Qwen3-4B", "32768",
                          "--enable-auto-tool-choice", "--tool-call-parser", "hermes")
        self.assertEqual(deployment(out)["containers"][0]["args"][-3:],
                         ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"])
        # The H53 boot ladder: b drops the setvars.sh source, c --disable-log-requests, d adds
        # #21's declared --max-model-len 32768; MAXLEN "-" keeps the chart's own (none).
        xpu[3]["spec"]["template"]["spec"]["containers"][0]["args"].append("--disable-log-requests")
        out = stage.stage("xpu", copy.deepcopy(xpu), "Qwen3-4B", "-", "ladder=bcd", "--tool-call-parser", "hermes")
        c = deployment(out)["containers"][0]
        self.assertEqual(c["command"][2], "python3 -m vllm.entrypoints.openai.api_server \"$@\"")
        self.assertNotIn("--disable-log-requests", c["args"])
        self.assertEqual(c["args"][-4:], ["--tool-call-parser", "hermes", "--max-model-len", "32768"])
        out = stage.stage("xpu", copy.deepcopy(xpu), "Qwen3-4B", "-", "ladder=b")
        c = deployment(out)["containers"][0]
        self.assertIn("--disable-log-requests", c["args"])
        self.assertNotIn("--max-model-len", c["args"])
        self.assertNotIn("setvars", c["command"][2])
        with self.assertRaises(SystemExit):
            stage.stage("xpu", copy.deepcopy(xpu), "Qwen3-4B", "32768", "ladder=x")

    def test_vllm_leaves_the_chat_template_configmap_alone(self):
        # EAT's chatTemplateConfigMap (Gemma 4 on vLLM CPU v0.24.0, amendment M1) is mounted over
        # the examples dir: an added HF_HUB_OFFLINE key would become a stray file there.
        docs = [{"kind": "ConfigMap", "metadata": {"name": "r-chat-templates"},
                 "data": {"tool_chat_template_gemma4.jinja": "{{ x }}"}}] + copy.deepcopy(EAT)
        out = stage.stage("vllm", docs, "gemma", "8192")
        self.assertEqual(out[0]["data"], {"tool_chat_template_gemma4.jinja": "{{ x }}"})
        self.assertEqual(out[1]["data"]["HF_HUB_OFFLINE"], "1")

    def test_stage_host_dir_points_hostpath_at_a_mount(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.yaml"), os.path.join(d, "out.yaml")
            with open(src, "w") as f:
                yaml.safe_dump_all(copy.deepcopy(EAT), f)
            subprocess.run([sys.executable, os.path.join(HERE, "stage.py"), "vllm", src, dst, "gemma", "8192"],
                           check=True, env=dict(os.environ, STAGE_HOST_DIR="/var/apb-ro"))
            with open(dst) as f:
                dep = [x for x in yaml.safe_load_all(f) if x and x["kind"] == "Deployment"][0]
            vol = dep["spec"]["template"]["spec"]["volumes"][0]
            self.assertEqual(vol, {"name": "model-volume", "hostPath": {"path": "/var/apb-ro", "type": "Directory"}})

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

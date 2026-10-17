#!/usr/bin/env python3
"""stage.py: point an arm's manifest at a model copied into the k3d node.

bench/apb/arm.sh copies a `hf download --local-dir` copy of the model into the
node container at /var/apb-models and rewrites the rendered manifest to mount
that directory with hostPath, so a cluster made for one run serves the model
without downloading it. Image, args and resources are left as rendered, except
where a mode below says otherwise.

  stage.py values BASE OVERLAY KV_GIB OUT   EAT's vllm/xeon-values.yaml with OVERLAY merged
                                            on top and every VLLM_CPU_KVCACHE_SPACE set to
                                            KV_GIB, as integrations/eat/brownfield.sh does
  stage.py ovms IN OUT                      `k3d-gpu agent manifest`: PVC ovms-models ->
                                            hostPath (OVMS --pull skips a directory with
                                            no .git and serves its files)
  stage.py vllm IN OUT DIR MAXLEN           EAT vLLM chart render: PVC -> hostPath,
                                            --model /data/DIR, --max-model-len MAXLEN,
                                            HF_HUB_OFFLINE=1, no HF token secret, readiness
                                            probe from 10 s (the chart waits 120 s)
  stage.py cuda IN OUT DIR                  bench/apb/vllm-cuda.yaml: cache emptyDir ->
                                            hostPath, --model=/cache/DIR, no --revision (the
                                            copy was downloaded at that revision)

Needs PyYAML.
"""

import sys

import yaml

HOST_DIR = "/var/apb-models"


def host_path(volume, old):
    volume.pop(old)
    volume["hostPath"] = {"path": HOST_DIR, "type": "Directory"}


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def values(base, overlay, kv_gib):
    out = merge(base, overlay or {})
    for cfg in [*(out.get("modelConfigs") or {}).values(), out.get("defaultModelConfigs")]:
        cmv = (cfg or {}).get("configMapValues") or {}
        if "VLLM_CPU_KVCACHE_SPACE" in cmv:
            cmv["VLLM_CPU_KVCACHE_SPACE"] = kv_gib
    return out


def stage(mode, docs, *extra):
    out = []
    for d in docs:
        kind = d.get("kind")
        if mode == "ovms":
            if kind == "PersistentVolumeClaim":
                continue
            if kind == "Deployment":
                for v in d["spec"]["template"]["spec"]["volumes"]:
                    if v["name"] == "models":
                        host_path(v, "persistentVolumeClaim")
        elif mode == "vllm":
            mdir, maxlen = extra
            if kind in ("PersistentVolumeClaim", "Secret"):
                continue
            if kind == "ConfigMap":
                d["data"]["HF_HUB_OFFLINE"] = "1"
            if kind == "Deployment":
                ps = d["spec"]["template"]["spec"]
                for v in ps["volumes"]:
                    if v["name"] == "model-volume":
                        host_path(v, "persistentVolumeClaim")
                c = ps["containers"][0]
                c["envFrom"] = [e for e in c["envFrom"] if "secretRef" not in e]
                a = c["args"]
                a[a.index("--model") + 1] = "/data/" + mdir
                a += ["--max-model-len", str(maxlen)]
                c["readinessProbe"].update(initialDelaySeconds=10, periodSeconds=5, failureThreshold=200)
        elif mode == "cuda":
            (mdir,) = extra
            if kind == "Deployment":
                ps = d["spec"]["template"]["spec"]
                for v in ps["volumes"]:
                    if v["name"] == "cache":
                        host_path(v, "emptyDir")
                c = ps["containers"][0]
                c["args"] = ["--model=/cache/" + mdir if x.startswith("--model=") else x
                             for x in c["args"] if not x.startswith("--revision=")]
                c["env"].append({"name": "HF_HUB_OFFLINE", "value": "1"})
        else:
            raise SystemExit(f"stage.py: unknown mode {mode}")
        out.append(d)
    return out


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    mode = argv[0]
    if mode == "values":
        base, overlay, kv_gib, dst = argv[1:5]
        with open(base) as b, open(overlay) as o:
            doc = values(yaml.safe_load(b), yaml.safe_load(o), kv_gib)
        with open(dst, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        return 0
    src, dst = argv[1:3]
    with open(src) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    with open(dst, "w") as f:
        yaml.safe_dump_all(stage(mode, docs, *argv[3:]), f, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

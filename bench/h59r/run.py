#!/usr/bin/env python3
"""run.py: H59r's runner tables (EXPERIMENTS.md "H59r pre-registration"). H59r is H59 on crescent
with llama.cpp b12110 (release v0.9.0, PR #404). This module loads H59's runner,
bench/h59/run.py (H59 amendments R1-R4), and changes only what the pre-registration names:

  - the llama.cpp server image `run.py check` requires of arm L, and the drafter conversion
    image `docker-argv` requires: server-b12110 and full-b12110, by digest;
  - the conversion Job's pip set, which `docker-argv` now also requires (GGUF_CONVERT_PIP);
  - arm O's render names K3D_GPU_AGENT_BACKEND=ovms. Since H59 (SUPPORTED for the 6741P),
    `agent manifest --model google/gemma-4-26B-A4B-it` with no backend serves llama.cpp on this
    CPU class; with the backend named it renders H59's O;
  - one host profile, crescent, as H59's but for its row (H59r), campaign directory
    (~/k3dgpu-h59r) and cluster prefix (k3dgpu-g-h59r-).

Every command is bench/h59/run.py's (see its docstring); its functions read these tables when
called. The verdict is bench/h59/h59.py's (`h59.py score RESULTS --load GATE.json`), the
reported figures bench/h59/report.py's.

Python 3 standard library, except render/stage/check/job (PyYAML)."""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
_spec = importlib.util.spec_from_file_location("h59_run", os.path.join(REPO, "bench", "h59", "run.py"))
h59run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h59run)

# The engine images `agent manifest` pins after PR #404 (llama.cpp release v0.9.0, tag b12110,
# commit cd959bae99ff4bed22bef2604f45241c09bbb8c8): server-b12110 on the CPU, full-b12110 for the
# drafter conversion. OVMS is H59's 2026.4.1.
LLAMA_CPU_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-b12110@sha256:1145e8de3111dc9358c6d8daffb755c4cf8a862d93391c26ba521b9a810bbe3e"
LLAMA_FULL_IMAGE = "ghcr.io/ggml-org/llama.cpp:full-b12110@sha256:93573d882ea21d1b77712a33162934daaabcdf3a076dfff91a9ed3c2aed8cd41"
OVMS_IMAGE = h59run.OVMS_IMAGE
# The conversion Job's pip set, the launcher's GGUF_CONVERT_PIP since PR #416 (transformers 5.22.2
# and huggingface-hub 2.4.0; H59 amendment R1 registered 5.21.0 and 2.3.0, with the same
# tokenizers and safetensors).
GGUF_CONVERT_PIP = "transformers==5.22.2 huggingface-hub==2.4.0 tokenizers==0.23.3 safetensors==0.8.0"

ARMS = h59run.ARMS
ARMS["O"]["env"] = {"K3D_GPU_AGENT_BACKEND": "ovms"}
PROFILES = {"crescent": dict(h59run.PROFILES["crescent"], row="H59r", g="~/k3dgpu-h59r", prefix="k3dgpu-g-h59r-")}
h59run.LLAMA_CPU_IMAGE, h59run.LLAMA_FULL_IMAGE, h59run.PROFILES = LLAMA_CPU_IMAGE, LLAMA_FULL_IMAGE, PROFILES

# What prep.sh, reproduce.sh and the tests read from this module.
GGUF_REPO, GGUF_REV, GGUF_FILE, GGUF_PATH = h59run.GGUF_REPO, h59run.GGUF_REV, h59run.GGUF_FILE, h59run.GGUF_PATH
DRAFT_REPO, DRAFT_REV, DRAFT_DIR, DRAFT_PATH = h59run.DRAFT_REPO, h59run.DRAFT_REV, h59run.DRAFT_DIR, h59run.DRAFT_PATH
OVB, OVB_REV, OVB_LM = h59run.OVB, h59run.OVB_REV, h59run.OVB_LM
models_ready, load_docs, deployment, args_of, check, untagged = (
    h59run.models_ready, h59run.load_docs, h59run.deployment, h59run.args_of, h59run.check, h59run.untagged)


def docker_argv(spec, models, scripts):
    """bench/h59/run.py's `docker run` of the conversion Job (its image must be LLAMA_FULL_IMAGE),
    when the Job installs the registered pip set."""
    got = (spec.get("env") or {}).get("GGUF_CONVERT_PIP")
    if got != GGUF_CONVERT_PIP:
        raise SystemExit(f"run.py: the conversion Job's GGUF_CONVERT_PIP is {got!r}, not {GGUF_CONVERT_PIP!r}")
    return h59run.docker_argv(spec, models, scripts)


def main(argv):
    if argv[:1] == ["docker-argv"]:
        print("\n".join(docker_argv(h59run.jload(argv[1]), argv[2], argv[3])))
        return 0
    return h59run.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

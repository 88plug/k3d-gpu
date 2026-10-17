"""`bfcl` CLI with one extra registry entry, "k3d-gpu/server-fc".

Runs inside the venv that holds bfcl-eval==2025.12.17. The entry uses BFCL's own
OpenAICompletionsHandler (chat completions with `tools`, so the serving engine's
tool parser does the parsing) and sends K3D_BFCL_SERVED_MODEL as the model name.
underscore_to_dot=True because that handler renames '.' to '_' in tool names
(bfcl_eval/model_handler/utils.py convert_to_tool) and the AST checker must map
them back. No file of the pinned package is modified.

Usage: python3 bfcl_launch.py generate|evaluate ...   (same arguments as `bfcl`)
"""
import os
import sys

from bfcl_eval.__main__ import cli
from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING, ModelConfig
from bfcl_eval.model_handler.api_inference.openai_completion import OpenAICompletionsHandler

MODEL_CONFIG_MAPPING["k3d-gpu/server-fc"] = ModelConfig(
    model_name=os.environ.get("K3D_BFCL_SERVED_MODEL") or "Qwen/Qwen3-4B-Instruct-2507",
    display_name="k3d-gpu server-fc (FC)",
    url="https://github.com/88plug/k3d-gpu/blob/main/docs/agent-benchmarks.md",
    org="k3d-gpu",
    license="apache-2.0",
    model_handler=OpenAICompletionsHandler,
    input_price=None,
    output_price=None,
    is_fc_model=True,
    underscore_to_dot=True,
)

if __name__ == "__main__":
    sys.argv[0] = "bfcl"
    cli()

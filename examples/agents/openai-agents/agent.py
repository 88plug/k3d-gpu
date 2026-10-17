"""OpenAI Agents SDK agent on the k3d-gpu endpoint, with its tool served over MCP.

    kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000
    python agent.py
"""

import asyncio
import os
import sys

from agents import (Agent, ModelSettings, OpenAIChatCompletionsModel, Runner,
                    set_tracing_disabled)
from agents.mcp import MCPServerStdio
from openai import AsyncOpenAI

BASE_URL = os.environ.get("K3D_GPU_AGENT_BASE_URL", "http://localhost:8000/v3")
MODEL = os.environ.get("K3D_GPU_AGENT_MODEL", "Qwen3-4B-int4-ov")
TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_tools.py")

# Traces would be uploaded to platform.openai.com; nothing here has a key for it.
set_tracing_disabled(True)


async def main():
    # OVMS serves Chat Completions under /v3: the client's base_url ends in /v3 and
    # the model class is the Chat Completions one (the SDK defaults to Responses).
    client = AsyncOpenAI(base_url=BASE_URL, api_key="unused")
    model = OpenAIChatCompletionsModel(model=MODEL, openai_client=client)
    async with MCPServerStdio(params={"command": sys.executable, "args": [TOOLS]},
                              name="k3d-gpu-tools") as tools:
        agent = Agent(
            name="calculator",
            instructions="Use the multiply tool for arithmetic. Answer in one sentence.",
            model=model,
            mcp_servers=[tools],
            # Qwen3 thinks before answering unless told not to; off keeps tool loops fast.
            model_settings=ModelSettings(
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}),
        )
        result = await Runner.run(agent, "What is 6 times 7?")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())

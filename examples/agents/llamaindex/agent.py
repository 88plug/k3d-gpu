"""LlamaIndex FunctionAgent on the k3d-gpu endpoint.

    kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000
    python agent.py
"""

import asyncio
import os

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.openai_like import OpenAILike

BASE_URL = os.environ.get("K3D_GPU_AGENT_BASE_URL", "http://localhost:8000/v3")
MODEL = os.environ.get("K3D_GPU_AGENT_MODEL", "Qwen3-4B-int4-ov")


def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


llm = OpenAILike(
    model=MODEL,
    api_base=BASE_URL,  # OVMS serves Chat Completions under /v3
    api_key="unused",
    temperature=0,
    # Both default to False; FunctionAgent refuses an LLM that is not function calling.
    is_chat_model=True,
    is_function_calling_model=True,
    # Qwen3 thinks before answering unless told not to; off keeps tool loops fast.
    additional_kwargs={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
)

agent = FunctionAgent(
    tools=[multiply],
    llm=llm,
    system_prompt="Use the multiply tool for arithmetic. Answer in one sentence.",
)


async def main():
    print(await agent.run("What is 6 times 7?"))


if __name__ == "__main__":
    asyncio.run(main())

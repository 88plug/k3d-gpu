"""CrewAI agent on the k3d-gpu endpoint.

    kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000
    python agent.py
"""

import os

# No usage telemetry or trace upload to CrewAI's cloud; must be set before import.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import LLM, Agent, Crew, Task  # noqa: E402
from crewai.tools import tool  # noqa: E402

BASE_URL = os.environ.get("K3D_GPU_AGENT_BASE_URL", "http://localhost:8000/v3")
MODEL = os.environ.get("K3D_GPU_AGENT_MODEL", "Qwen3-4B-int4-ov")


@tool("multiply")
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


# "openai/<model>" plus a base_url selects CrewAI's native OpenAI client against a
# custom endpoint; OVMS serves Chat Completions under /v3.
llm = LLM(
    model=f"openai/{MODEL}",
    base_url=BASE_URL,
    api_key="unused",
    temperature=0,
    # Qwen3 thinks before answering unless told not to; off keeps tool loops fast.
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)

calculator = Agent(
    role="Calculator",
    goal="Answer arithmetic questions exactly",
    backstory="You always use the multiply tool instead of doing arithmetic yourself.",
    tools=[multiply],
    llm=llm,
    max_iter=3,
)
task = Task(
    description="What is 6 times 7?",
    expected_output="One sentence with the product.",
    agent=calculator,
)

if __name__ == "__main__":
    print(Crew(agents=[calculator], tasks=[task]).kickoff().raw)

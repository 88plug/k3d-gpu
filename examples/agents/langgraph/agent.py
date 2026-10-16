"""LangGraph tool-calling agent on the k3d-gpu endpoint.

    kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000
    python agent.py
"""

import os

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

BASE_URL = os.environ.get("K3D_GPU_AGENT_BASE_URL", "http://localhost:8000/v3")
MODEL = os.environ.get("K3D_GPU_AGENT_MODEL", "Qwen3-4B-int4-ov")


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


tools = [multiply]
llm = ChatOpenAI(
    model=MODEL,
    base_url=BASE_URL,  # OVMS serves Chat Completions under /v3
    api_key="unused",
    temperature=0,
    # Qwen3 thinks before answering unless told not to; off keeps tool loops fast.
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
).bind_tools(tools)


def call_model(state: MessagesState):
    return {"messages": [llm.invoke(state["messages"])]}


graph = StateGraph(MessagesState)
graph.add_node("model", call_model)
graph.add_node("tools", ToolNode(tools))
graph.add_edge(START, "model")
graph.add_conditional_edges("model", tools_condition)  # tool call -> "tools", else end
graph.add_edge("tools", "model")
app = graph.compile()

if __name__ == "__main__":
    out = app.invoke({"messages": [
        ("system", "Use the multiply tool for arithmetic. Answer in one sentence."),
        ("user", "What is 6 times 7?"),
    ]})
    print(out["messages"][-1].content)

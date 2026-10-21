"""질문 분류(route)와 일반 대화(chat_answer) 노드 — Base 구조 그대로."""
from typing import Any

from langgraph.config import get_stream_writer
from langsmith import traceable

from .state import AgentState, add_cost


@traceable(name="route_node")
async def route_node(state: AgentState, deps: Any) -> dict:
    result, usage = deps.fast_llm.route(state["query"])
    event = {"type": "RouteDecided", "route": result["route"]}
    return {"route": result["route"], "cost_usd": add_cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: Any) -> dict:
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"), "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}

"""CoreWatch 그래프 — 노드는 agent/nodes/, 서브에이전트는 agent/subagents/에 있고 여기서는 연결만 한다.

route → chat_answer (일반 대화)
      → plan → (알람 못 찾음: 안내 후 종료)
             → gate → research(서브에이전트 3개 동시) → replan_check(실패 시 1회 재조사)
             → analysis(판단 유형) → write(리포트) → validate(수치 검사, 불합격 시 1회 재작성)
             → approve(위험 조치가 있으면 운영자 승인 대기) → finalize
모든 노드는 SupervisorDeps를 functools.partial로 주입받는다 (의존성 주입).
"""
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes.analysis import analysis_node
from .nodes.approve import approve_node
from .nodes.plan import gate_node, plan_node
from .nodes.research import replan_check_node, research_node
from .nodes.route import chat_answer_node, route_node
from .nodes.state import AgentState
from .nodes.validate import finalize_node, validate_node
from .nodes.write import write_node
from .subagents import kpi_analyst, peer_compare, runbook_search


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_plan(state: AgentState) -> str:
    return "end" if state.get("route") == "need_info" else "gate"


def _after_replan_check(state: AgentState) -> str:
    return "analysis" if state["events"][-1].get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "approve" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps, checkpointer: Any = None) -> Any:
    """checkpointer를 주면 사람 승인(HITL) 단계에서 그래프를 멈출 수 있다 (서버는 InMemorySaver 사용).
    주지 않으면(테스트) 위험 조치는 멈추지 않고 '자동 보류'로 표시한다."""
    subagents = {"kpi_analyst": kpi_analyst.build(deps.mcp), "peer_compare": peer_compare.build(deps.mcp),
                 "runbook_search": runbook_search.build(deps.mcp)}
    node = lambda fn, **kw: partial(fn, deps=deps, **kw)

    g = StateGraph(AgentState)
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node, subagents=subagents))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("approve", node(approve_node, hitl=checkpointer is not None))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route, {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_conditional_edges("plan", _after_plan, {"end": END, "gate": "gate"})
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check, {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate, {"approve": "approve", "write": "write"})
    g.add_edge("approve", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)

"""계획(plan)과 게이트(gate) 노드.

plan: 질문에서 알람을 찾고, MCP Prompt(alarm_analysis_prompt)로 분석 절차를 받아 서브에이전트 작업을 정한다.
gate: 계획에 섞인 불필요한 작업을 코드로 걸러 낸다 (Base의 권위 게이트).
"""
from typing import Any

from langsmith import traceable

from ..mme_query import parse_alarm
from .state import AgentState

SUBAGENTS = [
    ("kpi_analyst", "지표 분석"),
    ("peer_compare", "장비 비교"),
    ("runbook_search", "절차 검색"),
]
NEED_INFO = ("분석할 알람을 찾지 못했습니다. 장비 이름(예: MME-A03)과 지표(예: pag_sms_succ_rate), "
             "시각(예: 2026-10-16 08:00)을 함께 알려 주세요. 장비 이름만 알려 주시면 그 장비의 가장 강한 알람을 분석합니다.")


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: Any) -> dict:
    last = deps.session_store.load_alarm(state.get("thread_id", "default"))   # 이어지는 질문용 직전 알람
    alarm = parse_alarm(state["query"], last)
    if alarm is None:
        return {"route": "need_info", "final": NEED_INFO,
                "events": state["events"] + [{"type": "NeedInfo"}]}

    server = await deps.mcp.find_prompt("alarm_analysis_prompt")      # 어느 MCP 서버가 갖고 있는지 먼저 찾는다
    procedure = await deps.mcp.get_prompt("alarm_analysis_prompt",
                                          {"device": alarm.device, "kpi": alarm.kpi, "ts": alarm.ts}, server=server)
    deps.session_store.save_alarm(state.get("thread_id", "default"), alarm.to_dict())
    tasks = [{"id": f"task-{name}", "agent": "research", "kind": name, "target": label, "status": "pending"}
             for name, label in SUBAGENTS]
    tasks.append({"id": "task-analysis", "agent": "analysis", "kind": "pattern", "status": "pending"})
    event = {"type": "PlanCreated", "task_count": len(tasks), "targets": [l for _, l in SUBAGENTS],
             "alarm": alarm.to_dict()}
    return {"alarm": alarm.to_dict(), "procedure": procedure, "tasks": tasks, "needs_analysis": True,
            "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: Any) -> dict:
    tasks = state["tasks"]
    kept = tasks if state.get("needs_analysis") else [t for t in tasks if t["agent"] != "analysis"]
    removed = len(tasks) - len(kept)
    return {"tasks": kept, "gate_removed": removed,
            "events": state["events"] + [{"type": "GateApplied", "removed_analysis_tasks": removed}]}

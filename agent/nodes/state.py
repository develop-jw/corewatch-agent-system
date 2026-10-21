"""그래프 전체가 함께 쓰는 상태(State)."""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    route: str                    # chat | incident | need_info
    alarm: dict                   # {"device", "kpi", "ts", "found_by"}
    procedure: str                # MCP Prompt로 받은 분석 절차
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]         # 서브에이전트 결과 {"source", "findings", "data", "error"}
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    analysis: dict                # 판단 유형 (여러 장비 동시 / 한 장비 등)
    facts: list[str]              # [F1] … 리포트 ① 조회한 사실
    refs: list[dict]              # [R1] … 리포트 ② 점검 절차
    hypothesis: str               # 리포트 ③ AI 원인 추정 (LLM 작성)
    draft: str                    # 리포트 전체
    validation_attempts: int
    validation_feedback: str | None
    validation_detail: dict
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]


def add_cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage.get("usd", 0.0), 6)

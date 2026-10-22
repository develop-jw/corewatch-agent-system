"""판단 유형 분석(analysis) 노드 — 서브에이전트 결과를 코드 규칙으로 묶어 '어떤 유형의 이상인지' 정한다.

LLM에게 맡기지 않는 이유: '다른 장비 3대 이상 동시 알람'처럼 숫자로 확실히 가를 수 있는 판단은
코드가 하는 편이 정확하고, LLM은 이 결과를 근거로 원인만 추정하게 하기 위함이다.
"""
from typing import Any

from langsmith import traceable

from .state import AgentState

LOW_VOLUME = 100        # 시간당 시도 건수가 이보다 적으면 표본이 작아 판단 주의


def classify(kpi_data: dict, peer_data: dict) -> dict:
    patterns: list[str] = []
    common_section = None
    if peer_data:
        if peer_data.get("peer_alarms", 0) >= 3:
            patterns.append("여러 장비 동시 이상")
            common_section = "여러 장비 동시 이상"
        else:
            patterns.append("한 장비만 이상")
            common_section = "한 장비만 이상"
    if kpi_data.get("run_hours", 1) >= 3:
        patterns.append(f"{kpi_data['run_hours']}시간 연속 이상 (지속형)")
    if kpi_data and peer_data and peer_data.get("pair_change_pct") is not None:
        rate_down = (kpi_data["kpi_type"] == "성공률" and kpi_data["direction"] == "급감")
        pair_drop = peer_data["pair_change_pct"] <= -10
        if rate_down and pair_drop:
            patterns.append("시도와 성공률이 함께 감소")
        elif rate_down:
            patterns.append("성공률만 하락")
    volumes = [v for v in (kpi_data.get("value") if kpi_data.get("kpi_type") == "시도" else None,
                           peer_data.get("pair_value") if kpi_data.get("kpi_type") == "성공률" else None) if v is not None]
    if volumes and min(volumes) < LOW_VOLUME:
        patterns.append("시도 건수가 적어 판단 주의")
        common_section = "판단을 미뤄야 하는 경우"
    return {"patterns": patterns, "common_section": common_section}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: Any) -> dict:
    if not any(t["agent"] == "analysis" for t in state["tasks"]):
        return {"events": state["events"] + [{"type": "AnalysisSkipped"}]}
    by = {c["source"]: c for c in state.get("collected", [])}
    result = classify(by.get("kpi_analyst", {}).get("data", {}), by.get("peer_compare", {}).get("data", {}))
    return {"analysis": result,
            "events": state["events"] + [{"type": "AnalysisCompleted", "patterns": result["patterns"]}]}

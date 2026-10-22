"""서브에이전트 ① 지표 분석 — 알람 전후의 값과 이상 점수를 보고 '얼마나, 얼마나 오래' 달라졌는지 정리한다."""
import asyncio
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph
from langsmith import traceable

from mme.data import kpi_type
from ..retry import with_retry
from . import SubState, fmt, shift


@traceable(name="kpi_analyst.fetch")
async def fetch(state: SubState, mcp: Any) -> dict:
    a = state["alarm"]
    args = {"device": a["device"], "kpi": a["kpi"], "start": shift(a["ts"], -6), "end": shift(a["ts"], 3)}
    (series, r1), (anomaly, r2) = await asyncio.gather(
        with_retry(mcp.call_tool, "get_kpi_series", args),
        with_retry(mcp.call_tool, "detect_anomaly", args))
    err = series.get("error") or anomaly.get("error")
    return {"raw": {"series": series, "anomaly": anomaly}, "retries": r1 + r2, "error": err}


@traceable(name="kpi_analyst.summarize")
async def summarize(state: SubState) -> dict:
    if state.get("error"):
        return {"findings": [], "data": {}}
    a = state["alarm"]
    pts = state["raw"]["anomaly"]["points"]
    target = next((p for p in pts if p["ts"] == a["ts"]), None)
    if target is None or target["value"] is None:
        return {"findings": [], "data": {}, "error": "알람 시각의 값이 없어 판정할 수 없습니다"}

    idx = [p["ts"] for p in pts].index(a["ts"])          # 알람 시각을 포함해 연속으로 알람이 울린 시간 수
    run = 1
    for j in range(idx - 1, -1, -1):
        if not pts[j]["is_alarm"]:
            break
        run += 1
    for j in range(idx + 1, len(pts)):
        if not pts[j]["is_alarm"]:
            break
        run += 1
    value, usual = target["value"], target["usual"]
    change = None if not usual else round((value - usual) / usual * 100, 1)
    unit = "%" if kpi_type(a["kpi"]) == "성공률" else "건"
    findings = [
        f"{a['device']}의 {a['kpi']} 값은 {a['ts']}에 {fmt(value)}{unit}로, 평소 같은 시간대 {fmt(usual)}{unit} 대비 "
        f"{fmt(abs(change) if change is not None else None)}% {'높음' if (change or 0) >= 0 else '낮음'}",
        f"이상 점수 {fmt(target['score'])} (알람 기준선 {fmt(state['raw']['anomaly']['threshold'])}), 방향: {target['direction']}",
        f"알람이 연속으로 울린 시간: {run}시간",
    ]
    data = {"value": value, "usual": usual, "change_pct": change, "score": target["score"],
            "direction": target["direction"], "run_hours": run, "kpi_type": kpi_type(a["kpi"])}
    return {"findings": findings, "data": data}


def build(mcp: Any) -> Any:
    g = StateGraph(SubState)
    g.add_node("fetch", partial(fetch, mcp=mcp))
    g.add_node("summarize", summarize)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "summarize")
    g.add_edge("summarize", END)
    return g.compile()

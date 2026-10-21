"""서브에이전트 ③ 절차 검색 — 알람 지표에 맞는 점검 절차와 공통 판단 기준을 문서에서 찾는다 (RAG)."""
import asyncio
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph
from langsmith import traceable

from mme.data import kpi_type
from ..retry import with_retry
from . import SubState


@traceable(name="runbook_search.fetch")
async def fetch(state: SubState, mcp: Any) -> dict:
    a = state["alarm"]
    words = "성공률 하락 급감" if kpi_type(a["kpi"]) == "성공률" else "시도 급증 급감"
    (specific, r1), (common, r2) = await asyncio.gather(
        with_retry(mcp.call_tool, "search_runbook", {"query": f"{a['kpi']} {words}", "kpi": a["kpi"], "k": 2}),
        with_retry(mcp.call_tool, "search_runbook", {"query": "여러 장비 동시 이상 한 장비만 이상 시도 성공률",
                                                     "doc_id": "RB-00", "k": 4}))
    return {"raw": {"specific": specific, "common": common}, "retries": r1 + r2,
            "error": None if specific else "관련 문서 없음"}


@traceable(name="runbook_search.summarize")
async def summarize(state: SubState) -> dict:
    if state.get("error"):
        return {"findings": [], "data": {"specific": [], "common": []}}
    findings = [f"관련 문서: {h['ref']} (유사도 {h['score']})" for h in state["raw"]["specific"]]
    return {"findings": findings, "data": state["raw"]}


def build(mcp: Any) -> Any:
    g = StateGraph(SubState)
    g.add_node("fetch", partial(fetch, mcp=mcp))
    g.add_node("summarize", summarize)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "summarize")
    g.add_edge("summarize", END)
    return g.compile()

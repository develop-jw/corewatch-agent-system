"""이어지는 질문(직전 알람 기억)과 사람 승인(HITL) 테스트."""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.mme_query import parse_alarm
from agent.nodes.approve import risky_actions
from agent.session_store import SqliteSessionStore

LAST = {"device": "MME-A04", "kpi": "ue_dtch_att", "ts": "2026-10-17 14:00", "found_by": "질문"}
SINGLE = "MME-A04 ue_dtch_att 2026-10-17 14:00 알람 원인 분석해줘"      # 한 장비만 이상 → 재기동 검토가 나옴
FLEET = "MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘"  # 여러 장비 동시 → 위험 조치 없음


def test_followup_pair_kpi_uses_last_alarm() -> None:
    a = parse_alarm("그 장비 짝 지표는?", LAST)
    assert (a.device, a.kpi, a.ts, a.found_by) == ("MME-A04", "ue_dtch_succ_rate", "2026-10-17 14:00", "이전 질문")


def test_followup_other_device_same_time() -> None:
    a = parse_alarm("같은 시각 MME-A05는?", LAST)
    assert (a.device, a.kpi, a.ts) == ("MME-A05", "ue_dtch_att", "2026-10-17 14:00")


def test_no_last_alarm_still_needs_info() -> None:
    assert parse_alarm("그 장비 짝 지표는?", None) is None


def test_risky_action_detection_ignores_hold_sentences() -> None:
    text = "점검 순서:\n1. 공통 구간 점검 중 재기동은 보류\n4. 해당 장비 재기동 검토 (운영자 승인 필요)"
    assert risky_actions(text) == ["4. 해당 장비 재기동 검토 (운영자 승인 필요)"]


async def _with_graph(fn: Callable[[Any], Awaitable[dict]], checkpointer: Any = None) -> dict:
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    try:
        deps = SupervisorDeps(fast_llm=FastLLM("f", 0.0), quality_llm=QualityLLM("q", 0.0), mcp=mcp,
                              session_store=SqliteSessionStore(":memory:"), version="v2")
        return await fn(build_graph(deps, checkpointer=checkpointer))
    finally:
        await mcp.stop()


def _inp(q: str, thread: str = "t") -> dict:
    return {"query": q, "thread_id": thread, "history": [], "events": [], "cost_usd": 0.0}


def test_followup_through_graph() -> None:
    async def go(g: Any) -> dict:
        await g.ainvoke(_inp(SINGLE))
        return await g.ainvoke(_inp("그 장비 짝 지표는?"))
    r = asyncio.run(_with_graph(go))
    assert r["alarm"]["kpi"] == "ue_dtch_succ_rate" and "앞 질문의 알람을 이어서" in r["final"]


def test_without_checkpointer_risky_action_is_auto_held() -> None:
    r = asyncio.run(_with_graph(lambda g: g.ainvoke(_inp(SINGLE))))
    assert r["approval"]["required"] and not r["approval"]["approved"]
    assert "[보류 — 운영자 미승인]" in r["final"]


def test_fleet_alarm_needs_no_approval() -> None:
    r = asyncio.run(_with_graph(lambda g: g.ainvoke(_inp(FLEET))))
    assert r["approval"] == {"required": False}


def test_hitl_pauses_then_resumes_with_approval() -> None:
    async def go(g: Any) -> dict:
        cfg = {"configurable": {"thread_id": "run-1"}}
        first = await g.ainvoke(_inp(SINGLE), cfg)
        assert "__interrupt__" in first and "final" not in first          # 승인 전에는 리포트가 확정되지 않는다
        req = first["__interrupt__"][0].value
        assert req["type"] == "ApprovalRequired" and req["actions"]
        return await g.ainvoke(Command(resume={"approved": True}), cfg)
    r = asyncio.run(_with_graph(go, checkpointer=InMemorySaver()))
    assert r["approval"]["approved"] and "운영자 승인 완료" in r["final"]

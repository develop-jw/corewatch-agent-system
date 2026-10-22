"""그래프 전체 흐름 (가짜 LLM + 실제 MCP 서버 subprocess + 합성 데이터)."""
import asyncio

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore


def run(query: str) -> dict:
    async def go() -> dict:
        mcp = MCPClient(flaky_first_tool_call=False)
        await mcp.start()
        try:
            deps = SupervisorDeps(fast_llm=FastLLM("f", 0.0), quality_llm=QualityLLM("q", 0.0), mcp=mcp,
                                  session_store=SqliteSessionStore(":memory:"), version="v2")
            return await build_graph(deps).ainvoke({"query": query, "thread_id": "t", "history": [],
                                                    "events": [], "cost_usd": 0.0})
        finally:
            await mcp.stop()
    return asyncio.run(go())


def test_alarm_report_has_three_parts_and_passes_validation() -> None:
    r = run("MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘")
    assert all(s in r["final"] for s in ["① 조회한 사실", "② 점검 절차", "③ AI 원인 추정"])
    kinds = [e["type"] for e in r["events"]]
    assert kinds.count("TaskCompleted") == 3                      # 서브에이전트 3개
    assert r["validation_attempts"] == 2                          # 1차 반려(지어낸 숫자) → 2차 통과
    assert "자동 검사: " in r["final"] and "통과" in r["final"]


def test_chat_route_skips_pipeline() -> None:
    r = run("안녕 너는 누구야")
    assert r["route"] == "chat" and "tasks" not in r


def test_missing_alarm_asks_for_info() -> None:
    r = run("알람 원인 알려줘")
    assert r["route"] == "need_info" and "장비 이름" in r["final"]

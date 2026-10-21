"""OpenAI 백엔드 연결 확인 — 실제 API 대신 응답을 흉내 내는 가짜 httpx.post로, 지시문 전달과 흐름만 검사한다 (과금 없음)."""
import asyncio
import json
from typing import Any

import agent.openai_llm as openai_llm
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore


class _Resp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}], "usage": {"total_tokens": 100}}


SEEN: list[dict] = []


def _fake_post(url: str, json: dict, headers: dict, timeout: float) -> _Resp:   # noqa: A002 - httpx 인자 이름
    SEEN.append(json)
    if json.get("response_format"):                 # 질문 분류 (JSON 모드)
        return _Resp('{"route": "incident", "needs_analysis": true}')
    return _Resp("원인 후보 1) 여러 장비 공통 연동 구간 문제 [F4][R1]\n점검 순서:\n1. [R1] 확인\n2. [F5] 짝 지표 관찰")


def test_openai_backend_flow(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai_llm.httpx, "post", _fake_post)

    async def go() -> dict:
        mcp = MCPClient(flaky_first_tool_call=False)
        await mcp.start()
        try:
            deps = SupervisorDeps(fast_llm=openai_llm.OpenAIFastLLM("gpt-4o-mini", 0.00015),
                                  quality_llm=openai_llm.OpenAIQualityLLM("gpt-4o", 0.005), mcp=mcp,
                                  session_store=SqliteSessionStore(":memory:"), version="v2")
            return await build_graph(deps).ainvoke({"query": "MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘",
                                                    "thread_id": "t", "history": [], "events": [], "cost_usd": 0.0})
        finally:
            await mcp.stop()

    r = asyncio.run(go())
    assert r["validation_attempts"] == 1 and "통과" in r["final"]
    hyp_call = SEEN[-1]["messages"]
    assert "조회한 사실" in hyp_call[1]["content"] and "[F1]" in hyp_call[1]["content"]   # 사실 번호가 지시문에 들어갔는지
    assert r["cost_usd"] > 0

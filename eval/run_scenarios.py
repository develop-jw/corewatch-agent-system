"""시나리오 실행 점검 — 합성 데이터에 넣어 둔 이벤트 4개 + 이어지는 질문 + 정보 부족 질문을 그래프에 넣고 결과를 표로 남긴다.

정답 라벨은 쓰지 않는다. '판단 유형'이 넣어 둔 이벤트 성격(여러 장비 / 한 장비)과 맞는지,
수치 검사·사람 승인 단계가 의도대로 동작하는지를 사람이 확인하기 위한 표다.
실행: LLM_BACKEND=fake python eval/run_scenarios.py   (결과: reports/scenarios.md)
"""
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DATA_MODE", "synthetic")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore

SCENARIOS = [
    ("여러 장비 문자 호출 성공률 하락", "MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘", "여러 장비"),
    ("한 장비 접속 해제 시도 급증", "MME-A04 ue_dtch_att 2026-10-17 14:00 알람 원인 분석해줘", "한 장비"),
    ("이어지는 질문(짝 지표)", "그 장비 짝 지표는?", "이전 알람 이어받기"),
    ("한 장비 위치 등록 성공률 하락", "MME-A07 comb_tau_succ_rate 2026-10-17 04:00 알람 원인 분석해줘", "한 장비"),
    ("여러 장비 서비스 요청 시도 급증", "MME-A02 srmt_att 2026-10-18 19:00 알람 원인 분석해줘", "여러 장비"),
    ("새 대화에서 장비 없이 질문", "알람 원인 알려줘", "정보 요청"),
]


async def main() -> list[dict]:
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    rows = []
    try:
        deps = SupervisorDeps(fast_llm=FastLLM("fake", 0.0), quality_llm=QualityLLM("fake", 0.0), mcp=mcp,
                              session_store=SqliteSessionStore(":memory:"), version="v2")
        graph = build_graph(deps)
        for name, query, expect in SCENARIOS:
            t0 = time.perf_counter()
            thread = "scenario-new" if name.startswith("새 대화") else "scenario"
            r = await graph.ainvoke({"query": query, "thread_id": thread, "history": [],
                                     "events": [], "cost_usd": 0.0})
            alarm = r.get("alarm", {})
            rows.append({
                "시나리오": name, "기대": expect,
                "분석한 알람": f"{alarm.get('device')} · {alarm.get('kpi')} · {alarm.get('ts')}" if alarm else "-",
                "판단 유형": ", ".join(r.get("analysis", {}).get("patterns", [])) or r.get("route"),
                "수치 검사": f"{r.get('validation_attempts', 0)}회" + (" (1차 반려)" if r.get("validation_attempts") == 2 else ""),
                "위험 조치": ("승인 필요 " + str(len(r["approval"].get("actions", []))) + "건") if r.get("approval", {}).get("required") else "없음",
                "시간(초)": f"{time.perf_counter() - t0:.1f}",
            })
    finally:
        await mcp.stop()
    return rows


if __name__ == "__main__":
    rows = asyncio.run(main())
    cols = list(rows[0])
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    out = Path(__file__).resolve().parents[1] / "reports"
    out.mkdir(exist_ok=True)
    (out / "scenarios.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))

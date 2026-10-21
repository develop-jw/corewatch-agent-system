"""시나리오 실행 점검 — 합성 데이터에 넣어 둔 이벤트 4개 + 이어지는 질문 + 정보 부족 질문을 그래프에 넣고 결과를 표로 남긴다.

정답 라벨은 쓰지 않는다. '판단 유형'이 넣어 둔 이벤트 성격(여러 장비 / 한 장비)과 맞는지,
수치 검사·사람 승인 단계가 의도대로 동작하는지, 실제 AI 모델이 근거를 지키는지를 확인하기 위한 표다.

실행:
  LLM_BACKEND=fake   python eval/run_scenarios.py            # 결정론, 외부 호출 없음
  LLM_BACKEND=openai python eval/run_scenarios.py --repeat 3 # 실제 모델 (합성 데이터에서만, 과금)
결과: reports/scenarios_<백엔드>.md (요약 표), reports/scenario_reports_<백엔드>.md (리포트 전문)
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DATA_MODE", "synthetic")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
if os.environ.get("DATA_MODE") != "synthetic" and os.environ.get("LLM_BACKEND") == "openai":
    raise SystemExit("OpenAI 백엔드는 합성 데이터(DATA_MODE=synthetic)에서만 실행합니다.")

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.llm_factory import build_llms
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


async def main(repeat: int) -> tuple[str, list[dict]]:
    mcp = MCPClient(flaky_first_tool_call=False)
    await mcp.start()
    rows: list[dict] = []
    try:
        backend, fast, quality = build_llms()
        deps = SupervisorDeps(fast_llm=fast, quality_llm=quality, mcp=mcp,
                              session_store=SqliteSessionStore(":memory:"), version="v2")
        graph = build_graph(deps)
        for rep in range(1, repeat + 1):
            for name, query, expect in SCENARIOS:
                thread = f"new-{rep}" if name.startswith("새 대화") else f"scenario-{rep}"
                t0 = time.perf_counter()
                try:
                    r = await graph.ainvoke({"query": query, "thread_id": thread, "history": [],
                                             "events": [], "cost_usd": 0.0})
                    err = ""
                except Exception as e:   # 실제 모델 호출 실패도 결과로 남긴다
                    r, err = {}, f"{type(e).__name__}: {e}"[:200]
                vals = [e for e in r.get("events", []) if e.get("type") == "Validation"]
                alarm = r.get("alarm", {})
                rows.append({
                    "회차": rep, "시나리오": name, "기대": expect,
                    "분석한 알람": f"{alarm.get('device')} · {alarm.get('kpi')} · {alarm.get('ts')}" if alarm else "-",
                    "질문 분류": r.get("route", "-"),
                    "판단 유형": ", ".join(r.get("analysis", {}).get("patterns", [])) or "-",
                    "1차 검사": (vals[0]["verdict"] if vals else "-"),
                    "1차 반려 사유": " / ".join(vals[0]["problems"]) if vals and vals[0]["problems"] else "",
                    "최종": ("통과" if r.get("validation_detail", {}).get("ok") else ("경고" if vals else "-")),
                    "검사한 숫자": r.get("validation_detail", {}).get("numbers_checked", "-"),
                    "위험 조치": ("승인 필요 " + str(len(r["approval"].get("actions", []))) + "건") if r.get("approval", {}).get("required") else "없음",
                    "시간(초)": round(time.perf_counter() - t0, 1),
                    "비용($)": round(r.get("cost_usd", 0.0), 4),
                    "오류": err,
                    "_final": r.get("final", ""),
                })
                print(f"[{rep}] {name}: {rows[-1]['1차 검사']} → {rows[-1]['최종']} ({rows[-1]['시간(초)']}s) {err}", flush=True)
    finally:
        await mcp.stop()
    return backend, rows


def summarize(rows: list[dict]) -> list[str]:
    runs = [r for r in rows if r["1차 검사"] != "-"]
    n = len(runs)
    first = sum(r["1차 검사"] == "합격" for r in runs)
    final = sum(r["최종"] == "통과" for r in runs)
    lat = [r["시간(초)"] for r in runs]
    return [f"- 리포트 작성 {n}건: 1차 검사 통과 {first}건 ({first / n:.0%}), 재작성 후 최종 통과 {final}건 ({final / n:.0%})" if n else "- 리포트 작성 0건",
            f"- 평균 처리 시간 {sum(lat) / len(lat):.1f}초 · 최대 {max(lat):.1f}초" if lat else "",
            f"- 총 비용 ${sum(r['비용($)'] for r in rows):.4f} (계산 단가 기준 추정)",
            f"- 오류 {sum(bool(r['오류']) for r in rows)}건"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    backend, rows = asyncio.run(main(args.repeat))
    cols = [c for c in rows[0] if not c.startswith("_")]
    md = [f"# 시나리오 실행 점검 — LLM_BACKEND={backend}, {args.repeat}회 반복", ""] + summarize(rows) + ["",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ") for c in cols) + " |" for r in rows]
    out = Path(__file__).resolve().parents[1] / "reports"
    out.mkdir(exist_ok=True)
    (out / f"scenarios_{backend}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    full = [f"# 리포트 전문 — LLM_BACKEND={backend}", ""]
    for r in rows:
        full += [f"## [{r['회차']}] {r['시나리오']}", "", "```", r["_final"] or r["오류"], "```", ""]
    (out / f"scenario_reports_{backend}.md").write_text("\n".join(full), encoding="utf-8")
    print("\n".join(md[:8]))

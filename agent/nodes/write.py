"""리포트 작성(write) 노드.

리포트 세 부분 중 ① 조회한 사실과 ② 점검 절차는 코드가 직접 쓰고, ③ AI 원인 추정만 LLM이 쓴다.
→ 숫자와 출처가 들어가는 부분은 LLM이 바꿀 수 없게 하고, LLM은 그 번호([F1], [R1])를 근거로만 쓰게 한다.
"""
from typing import Any

from langgraph.config import get_stream_writer
from langsmith import traceable

from mme.data import kpi_type
from .state import AgentState, add_cost


def build_facts(state: AgentState) -> list[str]:
    by = {c["source"]: c for c in state.get("collected", [])}
    facts: list[str] = []
    for name in ("kpi_analyst", "peer_compare"):
        facts += by.get(name, {}).get("findings", [])
    patterns = state.get("analysis", {}).get("patterns", [])
    if patterns:
        facts.append("판단 유형(코드 규칙): " + ", ".join(patterns))
    for name, c in by.items():
        if c.get("error"):
            facts.append(f"조회 실패: {name} — {c['error']}")
    return facts


def build_refs(state: AgentState) -> list[dict]:
    rb = next((c for c in state.get("collected", []) if c["source"] == "runbook_search"), {})
    data = rb.get("data", {})
    picked = list(data.get("specific", []))
    want = state.get("analysis", {}).get("common_section")
    picked += [h for h in data.get("common", []) if h["section"] == want][:1]
    refs = []
    for i, h in enumerate(picked, 1):
        steps = [l.strip() for l in h["text"].splitlines() if l.strip()][:3]
        refs.append({"id": f"R{i}", "ref": h["ref"], "steps": steps})
    return refs


def render_report(alarm: dict, facts: list[str], refs: list[dict], hypothesis: str) -> str:
    lines = [f"[알람] {alarm['device']} · {alarm['kpi']}({kpi_type(alarm['kpi'])}) · {alarm['ts']}"
             + (" — 질문에 시각이 없어 가장 강한 알람을 골랐습니다" if alarm.get("found_by") == "가장 강한 알람" else ""),
             "", "■ ① 조회한 사실"]
    lines += [f"[F{i}] {f}" for i, f in enumerate(facts, 1)]
    lines += ["", "■ ② 점검 절차"]
    for r in refs:
        lines.append(f"[{r['id']}] {r['ref']}")
        lines += [f"    {s}" for s in r["steps"]]
    if not refs:
        lines.append("관련 점검 절차를 찾지 못했습니다.")
    lines += ["", "■ ③ AI 원인 추정 (가설)", hypothesis.strip()]
    return "\n".join(lines)


@traceable(name="write_node")
async def write_node(state: AgentState, deps: Any) -> dict:
    attempt = state.get("validation_attempts", 0) + 1
    alarm = state["alarm"]
    facts = build_facts(state)
    refs = build_refs(state)
    facts_text = "\n".join(f"[F{i}] {f}" for i, f in enumerate(facts, 1))
    runbook_text = "\n".join(f"[{r['id']}] {r['ref']}\n" + "\n".join(r["steps"]) for r in refs) or "(없음)"
    alarm_desc = f"{alarm['device']} · {alarm['kpi']} · {alarm['ts']}"

    writer = get_stream_writer()
    hypothesis, usage = await deps.quality_llm.write_hypothesis(
        alarm_desc, facts_text, runbook_text, attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer({"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    draft = render_report(alarm, facts, refs, hypothesis)
    return {"facts": facts, "refs": refs, "hypothesis": hypothesis, "draft": draft,
            "last_prompt": usage.get("prompt"), "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt, "length": len(draft)}]}

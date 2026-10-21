"""수치 검사(validate)와 마무리(finalize) 노드.

LLM이 쓴 ③ AI 원인 추정에서
  1) 숫자가 ① 조회한 사실에 있는 값인지 (새로 지어낸 숫자 금지)
  2) 근거 번호([F1], [R1])가 실제로 있는 번호인지
  3) 근거 번호가 하나라도 있는지 (또는 '원인을 특정할 수 없음'이라고 했는지)
  4) '점검 순서'가 있는지
를 코드로 검사한다. 통과하지 못하면 이유를 알려 주고 1번만 다시 쓰게 한다.
"""
import re
from typing import Any

from langsmith import traceable

from .state import AgentState

MAX_ATTEMPTS = 2
_STRIP = [r"\[[FR]\d+\]", r"RB-\d+", r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", r"\d{1,2}:\d{2}",
          r"[A-Za-z]+(?:_[A-Za-z0-9]+)+", r"MME-?[A-Z]?\d+", r"(?m)^\s*\d+[.)]", r"후보\s*\d+\)?"]
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in _NUM.findall(text)]


def check_hypothesis(hypothesis: str, facts_text: str, n_facts: int, n_refs: int) -> dict:
    body = hypothesis
    for pat in _STRIP:
        body = re.sub(pat, " ", body)
    allowed = set(_numbers(re.sub(r"\[[FR]\d+\]", " ", facts_text)))
    used = _numbers(body)
    bad = [n for n in used if n > 10 and not any(abs(n - a) <= 0.05 for a in allowed)]
    f_ids = [int(x) for x in re.findall(r"\[F(\d+)\]", hypothesis)]
    r_ids = [int(x) for x in re.findall(r"\[R(\d+)\]", hypothesis)]
    bad_refs = [f"F{i}" for i in f_ids if i > n_facts] + [f"R{i}" for i in r_ids if i > n_refs]
    no_basis = not (f_ids or r_ids) and "특정할 수 없음" not in hypothesis
    no_steps = "점검 순서" not in hypothesis and "특정할 수 없음" not in hypothesis
    problems = []
    if bad:
        problems.append(f"조회한 사실에 없는 숫자를 썼습니다: {', '.join(f'{n:g}' for n in bad)}. 사실에 있는 숫자만 쓰세요.")
    if bad_refs:
        problems.append(f"없는 근거 번호를 썼습니다: {', '.join(bad_refs)}.")
    if no_basis:
        problems.append("원인 후보마다 근거 번호([F1], [R1] 등)를 붙이세요.")
    if no_steps:
        problems.append("마지막에 '점검 순서:'를 1. 2. 3. 번호로 적으세요.")
    return {"ok": not problems, "problems": problems, "numbers_checked": len(used), "bad_numbers": bad}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: Any) -> dict:
    attempts = state.get("validation_attempts", 0) + 1
    facts_text = "\n".join(state.get("facts", []))
    result = check_hypothesis(state["hypothesis"], facts_text, len(state.get("facts", [])), len(state.get("refs", [])))
    if result["ok"]:
        verdict, feedback = "합격", None
    elif attempts >= MAX_ATTEMPTS:
        verdict, feedback = "합격(최대 시도 도달 — 경고 표시)", None
    else:
        verdict, feedback = "불합격", " ".join(result["problems"])
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict,
             "numbers_checked": result["numbers_checked"], "problems": result["problems"]}
    return {"validation_attempts": attempts, "validation_feedback": feedback, "validation_detail": result,
            "events": state["events"] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: Any) -> dict:
    d = state.get("validation_detail", {})
    note = (f"\n\n(자동 검사: 숫자 {d.get('numbers_checked', 0)}개 대조 · 통과)" if d.get("ok")
            else "\n\n(자동 검사 경고: " + " ".join(d.get("problems", [])) + " — 운영자 확인 필요)")
    ap = state.get("approval", {})
    if ap.get("required"):
        note += ("\n(위험 조치: 운영자 승인 완료)" if ap.get("approved")
                 else f"\n(위험 조치 {len(ap.get('actions', []))}건 보류 — {ap.get('by', '운영자')})")
    return {"final": state["draft"] + note,
            "events": state["events"] + [{"type": "Finalize", "pipeline": "corewatch"}]}

"""사람 승인(HITL) 노드 — 위험한 조치가 들어 있으면 운영자 확인을 받은 뒤에만 리포트를 확정한다.

AI 원인 추정(③)의 점검 순서에 '재기동', '롤백' 같은 되돌리기 어려운 조치가 있으면
LangGraph interrupt()로 그래프를 멈추고, 화면에서 운영자가 [승인] 또는 [보류]를 누르면
그 답을 받아 이어서 진행한다. (멈춘 상태는 checkpointer가 기억한다.)
"""
import re
from typing import Any

from langgraph.types import interrupt
from langsmith import traceable

from .state import AgentState

RISKY = re.compile(r"재기동|재시작|리부팅|롤백|트래픽\s*전환|차단|초기화")
SAFE = re.compile(r"보류|하지 않|금지|말 것")      # "재기동은 보류한다" 같은 문장은 조치가 아니다


def risky_actions(hypothesis: str) -> list[str]:
    """③ AI 원인 추정에서 위험한 조치를 권하는 줄만 골라낸다."""
    return [line.strip() for line in hypothesis.splitlines()
            if RISKY.search(line) and not SAFE.search(line)]


def mark_on_hold(text: str, actions: list[str]) -> str:
    for a in actions:
        text = text.replace(a, f"[보류 — 운영자 미승인] {a}")
    return text


@traceable(name="approve_node")
async def approve_node(state: AgentState, deps: Any, hitl: bool = True) -> dict:
    actions = risky_actions(state.get("hypothesis", ""))
    if not actions:
        return {"approval": {"required": False}}
    if not hitl:   # 승인 화면이 없는 실행(테스트·일괄 실행): 조치는 자동으로 보류해 둔다
        return {"approval": {"required": True, "approved": False, "actions": actions, "by": "자동 보류"},
                "draft": mark_on_hold(state["draft"], actions),
                "events": state["events"] + [{"type": "ApprovalDecided", "approved": False, "by": "자동 보류"}]}

    answer = interrupt({"type": "ApprovalRequired", "actions": actions, "draft": state["draft"]})
    approved = bool(answer.get("approved")) if isinstance(answer, dict) else bool(answer)
    draft = state["draft"] if approved else mark_on_hold(state["draft"], actions)
    return {"approval": {"required": True, "approved": approved, "actions": actions, "by": "운영자"},
            "draft": draft,
            "events": state["events"] + [{"type": "ApprovalDecided", "approved": approved, "by": "운영자"}]}

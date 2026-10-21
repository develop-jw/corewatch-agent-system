"""조사(research = 총괄 에이전트)와 재조사 판단(replan_check) 노드.

research: 아직 끝나지 않은 서브에이전트들을 동시에(asyncio.gather) 실행하고 결과를 모은다.
replan_check: 실패한 서브에이전트가 있으면 1번만 다시 시킨다 (무한 반복 방지).
"""
import asyncio
import time
from typing import Any

from langsmith import traceable

from .state import AgentState

MAX_REPLAN = 1


@traceable(name="research_node")
async def research_node(state: AgentState, deps: Any, subagents: dict) -> dict:
    tasks = state["tasks"]
    pending = [i for i, t in enumerate(tasks) if t["agent"] == "research" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] + [{"type": "ResearchSkipped"}]}

    async def run(idx: int) -> tuple[int, dict]:
        name = tasks[idx]["kind"]
        try:
            out = await subagents[name].ainvoke({"alarm": state["alarm"]})
        except Exception as e:                                   # 한 서브에이전트가 실패해도 나머지는 계속 (부분 결과)
            out = {"findings": [], "data": {}, "error": f"{type(e).__name__}: {e}", "retries": 0}
        return idx, out

    started = time.perf_counter()
    results = await asyncio.gather(*(run(i) for i in pending))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = [c for c in state.get("collected", []) if c["source"] not in {tasks[i]["kind"] for i in pending}]
    events = list(state["events"])
    retries = state.get("retry_count", 0)
    for idx, out in results:
        name = tasks[idx]["kind"]
        ok = not out.get("error")
        new_tasks[idx]["status"] = "done" if ok else "failed"
        collected.append({"source": name, "findings": out.get("findings", []), "data": out.get("data", {}),
                          "error": out.get("error")})
        retries += out.get("retries", 0)
        events.append({"type": "TaskCompleted", "source": name, "target": tasks[idx]["target"],
                       "ok": ok, "retries": out.get("retries", 0), "error": out.get("error")})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retries,
            "parallel_elapsed": elapsed, "events": events}


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: Any) -> dict:
    rounds = state.get("replan_rounds", 0)
    failed = [t for t in state["tasks"] if t["agent"] == "research" and t["status"] == "failed"]
    if not failed or rounds >= MAX_REPLAN:
        return {"events": state["events"] + [{"type": "ReplanCheck", "round": rounds, "is_sufficient": True,
                                              "failed": [t["target"] for t in failed]}]}
    tasks = [dict(t, status="pending") if t in failed else t for t in state["tasks"]]
    return {"tasks": tasks, "replan_rounds": rounds + 1,
            "events": state["events"] + [{"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
                                          "target": ", ".join(t["target"] for t in failed)}]}

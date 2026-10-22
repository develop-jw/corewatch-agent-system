"""서브에이전트 ② 장비 비교 — 짝 지표, 같은 시각 다른 장비, 같은 장비 주변 알람으로 '이 장비만의 문제인지' 정리한다."""
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph
from langsmith import traceable

from ..retry import with_retry
from . import SubState, fmt


@traceable(name="peer_compare.fetch")
async def fetch(state: SubState, mcp: Any) -> dict:
    a = state["alarm"]
    ctx, retries = await with_retry(mcp.call_tool, "get_context", {"device": a["device"], "kpi": a["kpi"], "ts": a["ts"]})
    return {"raw": ctx, "retries": retries, "error": ctx.get("error")}


@traceable(name="peer_compare.summarize")
async def summarize(state: SubState) -> dict:
    if state.get("error"):
        return {"findings": [], "data": {}}
    ctx = state["raw"]
    peers = ctx["same_time_other_devices"]
    pair = ctx.get("pair_kpi")
    findings = [f"같은 시각 같은 지표: 다른 장비 {peers['total']}대 중 {peers['alarm_count']}대에서도 알람"]
    change = None
    if pair and pair["value"] is not None:
        change = None if not pair["usual"] else round((pair["value"] - pair["usual"]) / pair["usual"] * 100, 1)
        findings.append(f"짝 지표 {pair['kpi']}: {fmt(pair['value'])} (평소 {fmt(pair['usual'])}, "
                        f"{fmt(abs(change) if change is not None else None)}% {'높음' if (change or 0) >= 0 else '낮음'}), "
                        f"알람 {'있음' if pair['is_alarm'] else '없음'}")
    near = ctx["same_device_nearby_alarms"]
    findings.append(f"같은 장비의 다른 지표 알람(앞뒤 1시간): {len(near)}건"
                    + (" — " + ", ".join(f"{n['kpi']}({n['ts'][-5:]})" for n in near[:3]) if near else ""))
    data = {"peer_total": peers["total"], "peer_alarms": peers["alarm_count"],
            "pair_kpi": pair["kpi"] if pair else None, "pair_value": pair["value"] if pair else None,
            "pair_change_pct": change, "pair_alarm": bool(pair and pair["is_alarm"]), "nearby": len(near)}
    return {"findings": findings, "data": data}


def build(mcp: Any) -> Any:
    g = StateGraph(SubState)
    g.add_node("fetch", partial(fetch, mcp=mcp))
    g.add_node("summarize", summarize)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "summarize")
    g.add_edge("summarize", END)
    return g.compile()

"""MCP 도구 5종을 같은 프로세스에서 직접 불러 확인 (합성 데이터)."""
import asyncio
import json

from fastmcp import Client

from mcp_server import mcp


def call(name: str, args: dict) -> dict | list:
    async def run() -> dict | list:
        async with Client(mcp) as c:
            r = await c.call_tool(name, args)
            return json.loads(r.content[0].text)
    return asyncio.run(run())


def test_kpi_series() -> None:
    d = call("get_kpi_series", {"device": "MME-A03", "kpi": "pag_sms_succ_rate",
                                "start": "2026-10-16 05:00", "end": "2026-10-16 10:00"})
    assert d["error"] is None and d["summary"]["count"] == 6 and d["pair_kpi"] == "pag_sms_att"


def test_unknown_device_returns_error_not_exception() -> None:
    d = call("get_kpi_series", {"device": "XX", "kpi": "pag_sms_att", "start": "2026-10-16 05:00", "end": "2026-10-16 06:00"})
    assert "모르는 장비" in d["error"]


def test_detect_finds_injected_event() -> None:
    d = call("detect_anomaly", {"device": "MME-A03", "kpi": "pag_sms_succ_rate",
                                "start": "2026-10-16 08:00", "end": "2026-10-16 08:00"})
    assert d["points"][0]["is_alarm"] and d["points"][0]["direction"] == "급감"


def test_context_sees_fleet_wide_event() -> None:
    d = call("get_context", {"device": "MME-A03", "kpi": "pag_sms_succ_rate", "ts": "2026-10-16 08:00"})
    assert d["same_time_other_devices"]["alarm_count"] == 8


def test_search_runbook_uses_kpi_metadata() -> None:
    hits = call("search_runbook", {"query": "성공률 하락", "kpi": "pag_sms_succ_rate", "k": 1})
    assert hits[0]["ref"] == "RB-04 · 문자 호출 성공률 하락"

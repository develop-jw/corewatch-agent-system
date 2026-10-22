"""관제 화면 API (/kpis, /series, /snapshot, /meta) — 서버를 띄워 실제 응답을 확인한다 (가짜 AI)."""
from fastapi.testclient import TestClient

import server


def test_dashboard_endpoints() -> None:
    with TestClient(server.app) as c:
        meta = c.get("/meta").json()
        assert meta["kpi_count"] == 26 and meta["procedure_count"] == 13 and meta["alarm_total"] > 0
        kpis = {k["kpi"]: k for k in c.get("/kpis").json()}
        assert kpis["pag_sms_succ_rate"]["label"] == "문자 호출 성공률" and kpis["pag_sms_succ_rate"]["pair"] == "pag_sms_att"
        s = c.get("/series", params={"device": "MME-A03", "kpi": "pag_sms_succ_rate",
                                     "start": "2026-10-16 02:00", "end": "2026-10-16 11:00"}).json()
        assert len(s["points"]) == 10 and any(p["is_alarm"] for p in s["points"])
        snap = c.get("/snapshot", params={"kpi": "pag_sms_succ_rate", "ts": "2026-10-16 08:00"}).json()
        assert len(snap) == 9 and sum(x["is_alarm"] for x in snap) == 9 and len(snap[0]["spark"]) == 10


def test_stream_endpoint_emits_report_and_final() -> None:
    import json
    with TestClient(server.app) as c:
        with c.stream("GET", "/chat/stream", params={"message": "MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘",
                                                     "thread_id": "t-api"}) as r:
            events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: {")]
    kinds = [e["type"] for e in events]
    assert "Report" in kinds and kinds[-1] == "FinalAnswer"
    rep = next(e for e in events if e["type"] == "Report")
    assert rep["alarm"]["device"] == "MME-A03" and rep["facts"] and rep["validation"]["ok"]


def test_tracing_flag_requires_real_key(monkeypatch) -> None:
    from agent import observability
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_...")
    assert not observability.tracing_enabled()          # 예시 값(placeholder)이면 꺼진 것으로 본다
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    assert observability.status() == {"enabled": False, "project": observability.project_name()}

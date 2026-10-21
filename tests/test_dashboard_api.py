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

"""all_total 통합 실습 — MCP Server (Wave 2 축).

Tool / Resource / Prompt 3대 capability를 모두 노출하는 실제 fastmcp 서버.
FastAPI 백엔드(server.py)가 lifespan에서 이 파일을 subprocess(stdio)로 1회 기동해
프로세스가 살아 있는 동안 재사용한다 (Wave 1 싱글턴 + Wave 2 표준화의 결합).

- Tool    get_monitoring_status : 호출마다 값이 달라지는 동적 조회 → Tool이 맞다
- Resource runbook://incident-response/{incident_type} : URI로 식별되는 정적 문서
- Prompt  incident_analysis_prompt : 여러 Tool을 조합하는 표준 진단 절차를 강제
"""
import json
import random
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from mme import data as mme_data
from mme import detect as mme_detect

mcp = FastMCP(name="capstone-all-total-ops")

DATA_DIR = Path(__file__).parent / "data"


class SystemStatus(BaseModel):
    system_name: str = Field(description="시스템 이름")
    status: str = Field(description="상태: healthy / degraded / down")
    cpu_percent: float = Field(description="CPU 사용률 (%)")
    memory_percent: float = Field(description="메모리 사용률 (%)")
    last_checked: str = Field(description="마지막 체크 시각 (ISO 8601)")
    alerts: list[str] = Field(default_factory=list, description="활성 알림 목록")


@mcp.tool()
def get_monitoring_status(system_name: str) -> SystemStatus:
    """지정된 시스템의 현재 모니터링 상태를 조회합니다 (호출마다 값이 달라지는 동적 Tool)."""
    monitoring_data = json.loads((DATA_DIR / "monitoring.json").read_text())
    if system_name not in monitoring_data:
        return SystemStatus(
            system_name=system_name, status="unknown",
            cpu_percent=0.0, memory_percent=0.0,
            last_checked=datetime.now().isoformat(),
            alerts=[f"System '{system_name}' not found in monitoring"],
        )
    data = monitoring_data[system_name]
    jitter = random.uniform(-3.0, 8.0)
    return SystemStatus(
        system_name=system_name,
        status=data["status"],
        cpu_percent=round(data["cpu_base"] + jitter, 1),
        memory_percent=round(data["memory_base"] + jitter / 2, 1),
        last_checked=datetime.now().isoformat(timespec="microseconds"),
        alerts=data.get("alerts", []),
    )


# ===== CoreWatch 도구 ① KPI 조회 =====
class KpiPoint(BaseModel):
    ts: str = Field(description="시각 (YYYY-MM-DD HH:00)")
    value: float = Field(description="값 (시도 KPI는 건수, 성공률 KPIsms %)")

class KpiSeries(BaseModel):
    device: str = Field(description="장비 이름")
    kpi: str = Field(description="KPI 이름")
    kpi_type: str = Field(description="시도 또는 성공률")
    pair_kpi: str = Field(description="같은 절차의 짝 KPI")
    points: list[KpiPoint] = Field(default_factory=list, description="시간별 값")
    summary: dict = Field(default_factory=dict, description="최소·최대·평균·마지막 값")
    error: str | None = Field(default=None, description="잘못된 요청일 때 이유")


@mcp.tool()
def get_kpi_series(device: str, kpi: str, start: str, end: str) -> KpiSeries:
    """MME 장비 하나의 KPI 하나를 기간(start~end) 동안 시간별로 조회합니다.
    예) device="MME071", kpi="pag_sms_succ_rate", start="2022-03-12 05:00", end="2022-03-12 10:00"
    """
    base = dict(device=device, kpi=kpi, kpi_type=mme_data.kpi_type(kpi), pair_kpi=mme_data.pair_of(kpi))

    if device not in mme_data.list_devices():
        return KpiSeries(**base, error=f"모르는 장비입니다: {device}")
    if kpi not in mme_data.list_kpis():
        return KpiSeries(**base, error=f"모르는 KPI입니다: {kpi}")

    rows = mme_data.get_series(device, kpi, start, end)
    if rows.empty:
        return KpiSeries(**base, error="해당 기간에 데이터가 없습니다")

    points = [KpiPoint(ts=t.strftime("%Y-%m-%d %H:00"), value=round(v, 2))
              for t, v in zip(rows["ts"], rows["value"])]
    summary = {
        "min": round(rows["value"].min(), 2),
        "max": round(rows["value"].max(), 2),
        "mean": round(rows["value"].mean(), 2),
        "last": round(rows["value"].iloc[-1], 2),
        "count": len(rows),
    }
    return KpiSeries(**base, points=points, summary=summary)


# ===== CoreWatch 도구 ② 이상 탐지 =====
class AnomalyPoint(BaseModel):
    ts: str = Field(description="시각")
    value: float | None = Field(description="실제 값 (없으면 null = 판정 불가)")
    usual: float | None = Field(description="평소 같은 시간대 값")
    score: float | None = Field(description="이상 점수 (클수록 이상)")
    is_alarm: bool = Field(description="알람 기준선을 넘었는지")
    direction: str | None = Field(description="급증 또는 급감")

class AnomalyResult(BaseModel):
    device: str
    kpi: str
    model: str = Field(description="탐지 모델 버전")
    threshold: float = Field(description="알람 기준선 (학습 구간 상위 0.3%)")
    points: list[AnomalyPoint] = Field(default_factory=list)
    alarm_count: int = Field(default=0, description="기간 안 알람 수")
    error: str | None = None

@mcp.tool()
def detect_anomaly(device: str, kpi: str, start: str, end: str) -> AnomalyResult:
    """MME 장비 하나의 KPI 하나에 대해 기간 동안의 이상 점수와 알람 여부를 돌려줍니다.
    이상 점수는 Isolation Forest 모델이 '평소 시간대 대비·직전 시각 대비·다른 장비 대비·짝 KPI 대비'
    네 가지 단서를 종합해 계산한 값입니다.
    예) device="MME071", kpi="pag_sms_succ_rate", start="2022-03-12 05:00", end="2022-03-12 10:00"
    """
    base = dict(device=device, kpi=kpi, model=mme_detect.MODEL_VERSION, threshold=0.0)
    if device not in mme_data.list_devices():
        return AnomalyResult(**base, error=f"모르는 장비입니다: {device}")
    if kpi not in mme_data.list_kpis():
        return AnomalyResult(**base, error=f"모르는 KPI입니다: {kpi}")

    rows = mme_detect.get_scores(device, kpi, start, end)
    if rows.empty:
        return AnomalyResult(**base, error="해당 기간에 데이터가 없습니다")

    def num(x: float) -> float | None:   # NaN(값 없음)은 None으로 바꿔 JSON에서 null이 되게 한다
        return None if pd.isna(x) else round(float(x), 3)

    points = [AnomalyPoint(ts=r.ts.strftime("%Y-%m-%d %H:00"), value=num(r.value), usual=num(r.usual),
                           score=num(r.score), is_alarm=bool(r.is_alarm),
                           direction=r.direction if not pd.isna(r.score) else None)
              for r in rows.itertuples()]
    base["threshold"] = round(float(rows["threshold"].iloc[0]), 3)
    return AnomalyResult(**base, points=points, alarm_count=int(rows["is_alarm"].sum()))

@mcp.tool()
def list_alarms(start: str, end: str, limit: int = 30) -> list[dict]:
    """기간 안에 발생한 알람 목록을 시각 순으로 돌려줍니다 (장비·KPI·시각·실제 값·평소 값·방향·점수).
    예) start="2022-03-12 00:00", end="2022-03-12 23:00"
    """
    rows = mme_detect.list_alarms(start, end).head(limit)
    return [{"device": r.device, "kpi": r.kpi, "ts": r.ts.strftime("%Y-%m-%d %H:00"),
             "value": round(float(r.value), 2), "usual": round(float(r.usual), 2),
             "direction": r.direction, "score": round(float(r.score), 3)}
            for r in rows.itertuples()]


# ===== CoreWatch 도구 ③ 맥락 조회 =====
@mcp.tool()
def get_context(device: str, kpi: str, ts: str) -> dict:
    """알람 하나(장비·KPI·시각)를 해석하는 데 필요한 주변 상황을 한 번에 돌려줍니다.
    ① 짝 KPI: 같은 절차의 시도↔성공률이 같은 시각에 어땠는지
    ② 같은 시각 다른 장비: 같은 KPI가 다른 장비에서도 함께 튀었는지 (장비 전체 문제 vs 이 장비만의 문제)
    ③ 같은 장비 ±1시간: 이 장비의 다른 KPI에도 알람이 있었는지
    예) device="MME071", kpi="pag_sms_succ_rate", ts="2022-03-12 08:00"
    """
    if device not in mme_data.list_devices():
        return {"error": f"모르는 장비입니다: {device}"}
    if kpi not in mme_data.list_kpis():
        return {"error": f"모르는 KPI입니다: {kpi}"}

    t = mme_detect.score_table()
    at = pd.Timestamp(ts)

    def brief(r: pd.Series) -> dict:
        return {"device": r["device"], "kpi": r["kpi"], "ts": r["ts"].strftime("%Y-%m-%d %H:00"),
                "value": None if pd.isna(r["value"]) else round(float(r["value"]), 2),
                "usual": None if pd.isna(r["usual"]) else round(float(r["usual"]), 2),
                "direction": r["direction"], "is_alarm": bool(r["is_alarm"])}

    # ① 짝 KPI (같은 장비, 같은 시각)
    pair_kpi = mme_data.pair_of(kpi)
    pair_rows = t[(t["device"] == device) & (t["kpi"] == pair_kpi) & (t["ts"] == at)]
    pair = brief(pair_rows.iloc[0]) if len(pair_rows) else None

    # ② 같은 시각, 같은 KPI, 다른 장비들
    peers = t[(t["kpi"] == kpi) & (t["ts"] == at) & (t["device"] != device)]
    peer_alarms = int(peers["is_alarm"].sum())

    # ③ 같은 장비, ±1시간, 다른 KPI 알람
    near = t[(t["device"] == device) & (t["kpi"] != kpi) & t["is_alarm"]
             & (t["ts"] >= at - pd.Timedelta("1h")) & (t["ts"] <= at + pd.Timedelta("1h"))]

    return {
        "target": {"device": device, "kpi": kpi, "ts": ts, "kpi_type": mme_data.kpi_type(kpi)},
        "pair_kpi": pair,
        "same_time_other_devices": {
            "total": len(peers),
            "alarm_count": peer_alarms,
            "devices_with_alarm": sorted(peers.loc[peers["is_alarm"], "device"].tolist()),
            "hint": "여러 장비 동시 이상 → 공통 원인(망·연동·이벤트) 가능성" if peer_alarms >= 3
                    else "이 장비만의 이상일 가능성",
        },
        "same_device_nearby_alarms": [brief(r) for _, r in near.iterrows()],
    }



# ===== CoreWatch 진단 절차 (MCP Prompt) =====
@mcp.prompt()
def alarm_analysis_prompt(device: str, kpi: str, ts: str) -> str:
    """MME 알람 분석 표준 절차 — 에이전트가 계획을 세울 때 이 순서를 따른다."""
    return f"""다음 MME 알람의 원인을 분석하세요.

    ## 알람 정보
    - 장비: {device}
    - 지표: {kpi}
    - 시각: {ts}

    ## 분석 절차 (반드시 순서대로 수행)
    1. get_kpi_series로 알람 전후 6시간의 지표 값을 조회해 언제부터 얼마나 달라졌는지 확인하세요.
    2. detect_anomaly로 이상 점수와 평소 값을 확인하세요.
    3. get_context로 짝 지표(시도 건수↔성공률), 같은 시각 다른 장비, 같은 장비의 주변 알람을 확인하세요.
    4. 해당 지표의 점검 절차 문서를 확인하세요.
    5. 확인한 사실만 근거로 가능한 원인과 점검 순서를 제시하세요. 근거가 부족하면 '원인을 특정할 수 없음'이라고 답하세요.
    """

# -----------------------
@mcp.resource("runbook://incident-response/{incident_type}")
def get_incident_runbook(incident_type: str) -> str:
    """특정 incident 유형의 runbook(정적 문서)을 반환합니다."""
    runbook_path = DATA_DIR / "runbooks" / f"{incident_type}.md"
    if not runbook_path.exists():
        return f"# Runbook Not Found\n\nNo runbook for incident type: {incident_type}"
    return runbook_path.read_text()


@mcp.prompt()
def incident_analysis_prompt(system_name: str, symptom: str) -> str:
    """장애 진단 표준 절차 프롬프트 — planning 노드가 계획을 세울 때 이 절차를 따른다."""
    return f"""다음 장애에 대해 표준 진단 절차를 수행하세요.

## 장애 정보
- 시스템: {system_name}
- 증상: {symptom}

## 진단 절차 (반드시 순서대로 수행)
1. get_monitoring_status Tool로 관련 시스템의 현재 상태를 조회하세요.
2. 증상에 해당하는 runbook(runbook://incident-response/...)을 확인하세요.
3. 위 정보를 종합해 가능한 원인과 권장 조치를 제시하세요.
"""


if __name__ == "__main__":
    try:
        mcp.run(show_banner=False)
    except TypeError:   # 구버전 fastmcp 호환
        mcp.run()

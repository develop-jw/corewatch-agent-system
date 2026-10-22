"""조사 서브에이전트 3개 — 각각 작은 LangGraph 그래프(서브그래프)로, 총괄 에이전트(research 노드)가 동시에 호출한다.

- kpi_analyst      지표 분석: 알람 전후 값과 이상 점수 (MCP: get_kpi_series, detect_anomaly)
- peer_compare     장비 비교: 짝 지표, 같은 시각 다른 장비, 같은 장비 주변 알람 (MCP: get_context)
- runbook_search   절차 검색: 점검 절차 문서 검색 (MCP: search_runbook = RAG)
"""
from typing import Any, TypedDict

import pandas as pd


class SubState(TypedDict, total=False):
    alarm: dict            # {"device", "kpi", "ts"}
    raw: dict              # 도구에서 받은 원자료
    findings: list[str]    # 사람이 읽는 한 줄 결과들 (숫자 포함)
    data: dict             # 다음 단계(분석·작성)가 쓰는 정리된 값
    retries: int
    error: str | None


def shift(ts: str, hours: int) -> str:
    return (pd.Timestamp(ts) + pd.Timedelta(hours=hours)).strftime("%Y-%m-%d %H:00")


def fmt(x: Any) -> str:
    """숫자를 리포트용 글자로. 큰 수는 천 단위 쉼표, 작은 수는 소수 둘째 자리."""
    if x is None:
        return "값 없음"
    x = float(x)
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.2f}".rstrip("0").rstrip(".")

"""사용자 질문에서 분석할 알람(장비·지표·시각)을 찾아낸다.

LLM에게 맡기지 않고 코드로 찾는다 — 장비·지표 이름은 정해진 목록 안에서만 나오므로
목록과 맞춰 보는 편이 정확하고 빠르다 (Base의 _extract_system_name과 같은 생각).
"""
import re
from dataclasses import asdict, dataclass

import pandas as pd

from mme import data as mme_data
from mme import detect as mme_detect

TS_FULL = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T]+(\d{1,2})(?::\d{2})?")      # 2026-10-16 08:00
TS_SHORT = re.compile(r"(\d{1,2})[-/.월]\s*(\d{1,2})일?\s+(\d{1,2})\s*시")        # 10-16 8시, 10월 16일 8시


@dataclass
class AlarmRef:
    device: str
    kpi: str
    ts: str                 # "YYYY-MM-DD HH:00"
    found_by: str           # "질문" 또는 "가장 강한 알람" (지표·시각을 질문에서 못 찾았을 때)

    def to_dict(self) -> dict:
        return asdict(self)


def _first_match(query: str, names: list[str]) -> str | None:
    """질문에 등장하는 이름 중 가장 먼저 나온 것. 같은 위치면 긴 이름을 우선한다."""
    hits = [(query.find(n), -len(n), n) for n in names if n in query]
    return min(hits)[2] if hits else None


def _find_ts(query: str) -> str | None:
    m = TS_FULL.search(query)
    if m:
        y, mo, d, h = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:00"
    m = TS_SHORT.search(query)
    if m:
        year = mme_data.load_table()["ts"].min().year
        mo, d, h = (int(x) for x in m.groups())
        return f"{year:04d}-{mo:02d}-{d:02d} {h:02d}:00"
    return None


def parse_alarm(query: str) -> AlarmRef | None:
    """질문에서 장비·지표·시각을 찾는다. 장비는 꼭 있어야 하고, 지표·시각이 없으면
    그 장비(와 지표)의 가장 강한 알람으로 채운다. 장비가 없거나 해당 알람이 없으면 None."""
    device = _first_match(query, mme_data.list_devices())
    if device is None:
        return None
    kpi = _first_match(query, mme_data.list_kpis())
    ts = _find_ts(query)
    if kpi and ts:
        return AlarmRef(device, kpi, ts, "질문")

    alarms = mme_detect.list_alarms()
    alarms = alarms[alarms["device"] == device]
    if kpi:
        alarms = alarms[alarms["kpi"] == kpi]
    if ts:
        alarms = alarms[alarms["ts"] == pd.Timestamp(ts)]
    if alarms.empty:
        return None
    top = alarms.sort_values("score", ascending=False).iloc[0]
    return AlarmRef(device, top["kpi"], top["ts"].strftime("%Y-%m-%d %H:00"), "가장 강한 알람")

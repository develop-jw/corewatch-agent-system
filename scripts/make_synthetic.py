"""5단계 — 합성 데이터 생성기.

실제 데이터를 변환하지 않고, 정해 둔 규칙으로 처음부터 새로 만든 가짜 MME 데이터다.
외부 AI 서비스(OpenAI), 실행 기록 서비스(LangSmith), 공개 시연에서 실제 데이터 대신 쓴다.

- 장비 9대 (이름: MME-A01 ~ MME-A09, 실제 장비 이름과 다름)
- 지표 26개 (시도 건수 13개 + 성공률 13개, 이름은 통신 표준 절차 이름이라 그대로 사용)
- 7일 × 24시간 = 168시각, 하루 패턴(낮에 많고 새벽에 적음) + 작은 흔들림
- 앞 4일(학습·검증)은 정상만, 뒤 3일(평가)에 이상 사례 4개를 일부러 넣음

실행: python3 scripts/make_synthetic.py
결과: data/synthetic/mme_synthetic.csv (에이전트용, 정답 없음)
      data/synthetic/injected_events.csv (넣은 이상 사례 목록, 시연 확인용)
"""
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "synthetic"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(2026)

# ---- 1) 기본 틀 ----
DEVICES = [f"MME-A{i:02d}" for i in range(1, 10)]
PROCEDURES = ["comb_atch_45005", "comb_atch_etc", "comb_tau", "ddn", "net_dtch", "pag_data",
              "pag_hdv", "pag_sms", "pag_video", "prd_tau", "srmo", "srmt", "ue_dtch"]
START = pd.Timestamp("2026-10-12 00:00")
HOURS = pd.date_range(START, periods=168, freq="h")

# 절차별 시간당 시도 건수 규모(대략적인 자릿수만 정한 값)와 평소 성공률
SCALE = {"comb_atch_45005": 2e4, "comb_atch_etc": 2e4, "comb_tau": 5e5, "ddn": 1e7, "net_dtch": 8e3,
         "pag_data": 1e7, "pag_hdv": 1e5, "pag_sms": 8e4, "pag_video": 60, "prd_tau": 7e4,
         "srmo": 1.4e7, "srmt": 1e7, "ue_dtch": 7e3}
RATE = {"comb_atch_45005": 75.0, "comb_atch_etc": 30.0, "comb_tau": 99.7, "ddn": 99.2, "net_dtch": 97.0,
        "pag_data": 97.5, "pag_hdv": 97.6, "pag_sms": 98.9, "pag_video": 98.0, "prd_tau": 99.9,
        "srmo": 99.99, "srmt": 99.99, "ue_dtch": 99.8}


def daily_shape(hours: pd.DatetimeIndex) -> np.ndarray:
    """하루 패턴: 새벽 4시 무렵 가장 적고(약 0.25배) 오후 늦게 가장 많음(약 1.0배)."""
    h = hours.hour.to_numpy()
    return 0.625 - 0.375 * np.cos((h - 4) / 24 * 2 * np.pi)


rows = []
for dev in DEVICES:
    dev_size = rng.uniform(0.6, 1.4)                      # 장비마다 처리 규모가 조금씩 다름
    for p in PROCEDURES:
        att = SCALE[p] * dev_size * daily_shape(HOURS) * rng.normal(1.0, 0.04, len(HOURS))
        rate = RATE[p] + rng.normal(0, max(0.02, (100 - RATE[p]) * 0.05), len(HOURS))
        rows.append(pd.DataFrame({"src_nm": dev, "variable": f"{p}_att", "ds": HOURS, "value": att.round(0)}))
        rows.append(pd.DataFrame({"src_nm": dev, "variable": f"{p}_succ_rate", "ds": HOURS,
                                  "value": np.clip(rate, 0, 100).round(2)}))
df = pd.concat(rows, ignore_index=True)

# ---- 2) 이상 사례 넣기 (모두 평가 기간 = 시작 후 96시간 이후) ----
events = []


def inject(devs: list[str], kpi: str, at: str, hours: int, fn: Callable[[pd.Series], pd.Series], note: str) -> None:
    for k in range(hours):
        ts = pd.Timestamp(at) + pd.Timedelta(hours=k)
        m = df["src_nm"].isin(devs) & (df["variable"] == kpi) & (df["ds"] == ts)
        df.loc[m, "value"] = fn(df.loc[m, "value"])
        for d in devs:
            events.append({"device": d, "kpi": kpi, "ts": ts, "note": note})


# ① 장비 전체: 문자 호출 성공률 하락 + 시도 건수 감소 (공통 원인형)
inject(DEVICES, "pag_sms_succ_rate", "2026-10-16 08:00", 1, lambda v: (v - 1.5).round(2), "전 장비 문자 호출 성공률 하락")
inject(DEVICES, "pag_sms_att", "2026-10-16 08:00", 1, lambda v: (v * 0.7).round(0), "전 장비 문자 호출 시도 감소")
# ② 장비 1대: 단말 접속 해제 시도 급증 (단일 장비형)
inject(["MME-A04"], "ue_dtch_att", "2026-10-17 14:00", 1, lambda v: (v * 3.0).round(0), "A04 접속 해제 시도 급증")
# ③ 장비 1대: 위치 등록 성공률 3시간 연속 하락
inject(["MME-A07"], "comb_tau_succ_rate", "2026-10-17 03:00", 3, lambda v: (v - 1.5).round(2), "A07 위치 등록 성공률 하락")
# ④ 장비 전체: 망 발신 서비스 요청 급증 (이벤트형)
inject(DEVICES, "srmt_att", "2026-10-18 19:00", 1, lambda v: (v * 1.6).round(0), "전 장비 서비스 요청 급증")

df["ds"] = df["ds"].dt.strftime("%Y-%m-%d %H:%M:%S")
df.to_csv(OUT / "mme_synthetic.csv", index=False)
pd.DataFrame(events).to_csv(OUT / "injected_events.csv", index=False)
print(f"합성 데이터: {len(df):,}행 → {OUT / 'mme_synthetic.csv'}")
print(f"넣은 이상: {len(events)}건 (사례 4개) → {OUT / 'injected_events.csv'}")

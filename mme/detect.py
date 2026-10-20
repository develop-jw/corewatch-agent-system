"""3단계 — 이상 탐지 모듈 (ADR-0016: M2 Isolation Forest, 비교 기준 B0·M1).

선행 분석(corewatch/src/detect.py)의 코드를 서비스용으로 옮긴 것이다.
원칙
- 평소값·흩어짐 정도·모델·알람 기준선은 '학습 구간' 데이터로만 만든다 (DR-01, DR-04)
- 정답 라벨은 쓰지 않는다 (DR-06)
- 각 시각의 판정에는 그 시각까지의 값만 쓴다 (직전 시각 대비, 같은 시각 다른 장비)
  → 그래서 모든 시각의 점수를 한 번에 미리 계산해 두어도 실시간 판정과 결과가 같다
"""
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from . import data as mme_data

# ---- 고정 설정 (모델 카드: reports/model_card.json) ----
TRAIN_START = pd.Timestamp("2022-03-07 09:00")
TRAIN_END = pd.Timestamp("2022-03-10 08:00")     # 학습 72시간
VALID_END = pd.Timestamp("2022-03-11 08:00")     # 검증 24시간, 이후는 평가 구간
ALARM_BUDGET = 0.003                              # 학습 구간에서 상위 0.3%만 알람 (ADR-0018)
FEATURES = ["z_prof", "z_diff", "fleet", "dev_vs_fleet", "pair"]
MODEL_VERSION = "M2-iforest-v2"
SEED = 42


def _floor(kpi: str) -> float:
    return 0.01 if mme_data.kpi_type(kpi) == "시도" else 0.05     # log 단위 1% / 성공률 0.05%p


def _robust_scale(x: pd.Series, floor: float) -> float:
    """흩어짐 정도 = 1.4826 × MAD (튀는 값에 덜 흔들리는 표준편차). 너무 작으면 floor로 막는다."""
    x = x.dropna()
    if len(x) == 0:
        return floor
    mad = 1.4826 * np.median(np.abs(x - np.median(x)))
    return max(mad, 0.25 * x.std(ddof=0) if len(x) > 1 else 0.0, floor)


def _hour_profile(train: pd.DataFrame) -> pd.Series:
    """평소 시간대 값 = 학습 3일 × (h-1, h, h+1) 9개 값의 중앙값 (같은 시각 반복 이상에 덜 흔들림)."""
    return pd.Series([train[train["hour"].isin([(h - 1) % 24, h, (h + 1) % 24])]["y"].median()
                      for h in range(24)], index=range(24))


def _make_grid() -> pd.DataFrame:
    """장비 × KPI × 1시간 빈칸 없는 격자로 펼친다 (빠진 시각은 NaN → 판정 불가)."""
    t = mme_data.load_table()
    hours = pd.date_range(t["ts"].min(), t["ts"].max(), freq="h")
    idx = pd.MultiIndex.from_product([sorted(t["device"].unique()), sorted(t["kpi"].unique()), hours],
                                     names=["device", "kpi", "ts"])
    g = t.set_index(["device", "kpi", "ts"])[["value"]].reindex(idx).reset_index()
    is_try = g["kpi"].map(mme_data.kpi_type) == "시도"
    g["y"] = np.where(is_try, np.log1p(g["value"].clip(lower=0)), g["value"])   # 시도 건수는 log로
    g["hour"] = g["ts"].dt.hour
    g["is_train"] = g["ts"] <= TRAIN_END
    return g


def _features(g: pd.DataFrame) -> pd.DataFrame:
    resid = pd.Series(np.nan, index=g.index)
    dres = pd.Series(np.nan, index=g.index)
    scales = {}
    for key, d in g.groupby(["device", "kpi"]):
        d = d.sort_values("ts")
        r = d["y"] - d["hour"].map(_hour_profile(d[d["is_train"]]))   # ① 평소 시간대 대비
        resid[d.index] = r
        dres[d.index] = r.diff()                                      # ② 직전 시각 대비
        f = _floor(key[1])
        scales[key] = (_robust_scale(r[d["is_train"]], f), _robust_scale(r.diff()[d["is_train"]], f))
    sc = pd.DataFrame(scales, index=["s", "sd"]).T
    sc.index.names = ["device", "kpi"]
    kpi_med = sc.groupby(level="kpi").median()                         # 같은 KPI 장비들의 흩어짐
    sc["s"] = np.maximum(sc["s"], 0.5 * kpi_med["s"].reindex(sc.index.get_level_values("kpi")).to_numpy())
    sc["sd"] = np.maximum(sc["sd"], 0.5 * kpi_med["sd"].reindex(sc.index.get_level_values("kpi")).to_numpy())
    k = pd.MultiIndex.from_frame(g[["device", "kpi"]])
    g["z_prof"] = resid / sc["s"].reindex(k).to_numpy()
    g["z_diff"] = dres / sc["sd"].reindex(k).to_numpy()
    g["fleet"] = g.groupby(["ts", "kpi"])["z_prof"].transform("median")   # ③ 장비 전체가 같이 움직였나
    g["dev_vs_fleet"] = g["z_prof"] - g["fleet"]
    key = g.set_index(["device", "kpi", "ts"])["z_prof"]
    g["pair"] = key.reindex(list(zip(g["device"], g["kpi"].map(mme_data.pair_of), g["ts"]))).to_numpy()   # ④ 짝 KPI
    for c in FEATURES:
        g[c] = g[c].clip(-20, 20)
    return g


@lru_cache(maxsize=1)
def score_table() -> pd.DataFrame:
    """모든 장비·KPI·시각의 이상 점수와 알람 여부. 처음 한 번만 계산하고 재사용한다."""
    g = _features(_make_grid())
    ok = g[FEATURES].notna().all(axis=1)
    model = IsolationForest(n_estimators=300, max_samples=512, random_state=SEED, n_jobs=-1)
    model.fit(g.loc[ok & g["is_train"], FEATURES])
    g["score"] = np.nan
    g.loc[ok, "score"] = -model.score_samples(g.loc[ok, FEATURES])   # 클수록 이상
    threshold = float(np.nanquantile(g.loc[g["is_train"], "score"], 1 - ALARM_BUDGET))
    g["threshold"] = threshold
    g["is_alarm"] = g["score"] >= threshold
    g["direction"] = np.where(g["z_prof"] >= 0, "급증", "급감")
    g = _attach_usual(g)
    g["period"] = np.where(g["ts"] <= TRAIN_END, "학습", np.where(g["ts"] <= VALID_END, "검증", "평가"))
    return g.drop(columns=["y", "hour", "is_train"])


def _attach_usual(g: pd.DataFrame) -> pd.DataFrame:
    """사람이 읽을 '평소 값'(원래 단위)을 붙인다 — 학습 구간 같은 시간대 중앙값."""
    train = g[g["is_train"]]
    usual = train.groupby(["device", "kpi", "hour"])["value"].median().rename("usual")
    return g.merge(usual, on=["device", "kpi", "hour"], how="left")


def get_scores(device: str, kpi: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    t = score_table()
    rows = t[(t["device"] == device) & (t["kpi"] == kpi)]
    if start:
        rows = rows[rows["ts"] >= pd.Timestamp(start)]
    if end:
        rows = rows[rows["ts"] <= pd.Timestamp(end)]
    return rows.reset_index(drop=True)


def list_alarms(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """기간 안의 알람 목록 (시각 순)."""
    t = score_table()
    rows = t[t["is_alarm"]]
    if start:
        rows = rows[rows["ts"] >= pd.Timestamp(start)]
    if end:
        rows = rows[rows["ts"] <= pd.Timestamp(end)]
    return rows.sort_values(["ts", "score"], ascending=[True, False]).reset_index(drop=True)

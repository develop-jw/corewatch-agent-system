"""탐지 모델 평가 구간 1회 채점 (DR-03, DR-06, DR-10).

- 모델 선택은 검증 구간 결과로 이미 끝났다 (ADR-0018: 학습 알람률 0.3%, 검증 최다 탐지 → M2).
- 이 스크립트는 그 선택을 reports/model_card.json에 먼저 기록한 뒤 평가 구간을 채점한다.
- 정답 라벨은 여기서만 읽는다. 에이전트 코드(mme/, agent/)는 라벨을 읽지 않는다.
실행: DATA_MODE=original python3 eval/eval_detector.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mme import data as mme_data
from mme import detect

OUT = Path(__file__).resolve().parents[1] / "reports"
OUT.mkdir(exist_ok=True)

# ---- 1) 선택 기록 먼저 (채점 전) ----
card = {
    "model": detect.MODEL_VERSION, "features": detect.FEATURES, "alarm_budget_train": detect.ALARM_BUDGET,
    "split": {"train_end": str(detect.train_end()), "valid_end": str(detect.valid_end())},
    "selection_rule": "학습 구간 알람률 0.3% 고정, 검증 구간 최다 탐지 (동점 시 학습 오탐 적은·단순한 모델)",
    "selection_result": "검증 10건 중 B0 4 / M1 9 / M2 10 → M2 선택 (2026-10-17 비교)",
    "eval_rule": "평가 구간은 1회만 채점, 결과가 나빠도 모델을 고쳐 재채점하지 않음",
}
(OUT / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

# ---- 2) 점수 (B0·M1은 같은 조건의 비교 기준) ----
t = detect.score_table().copy()
train = t["period"] == "학습"
t["M2"] = t["score"]
t["M1"] = t["z_prof"].abs()
y = np.where(t["kpi"].map(mme_data.kpi_type) == "시도", np.log1p(t["value"].clip(lower=0)), t["value"])
t["y"] = y
med = t[train].groupby(["device", "kpi"])["y"].median().rename("med")
t = t.merge(med, on=["device", "kpi"], how="left")
mad = t[t["period"] == "학습"].groupby(["device", "kpi"]).apply(
    lambda d: detect._robust_scale(d["y"], detect._floor(d.name[1])), include_groups=False).rename("mad")
t = t.merge(mad, on=["device", "kpi"], how="left")   # 선행 비교(04_model_compare)와 같은 B0 정의
t["B0"] = ((t["y"] - t["med"]) / t["mad"]).abs()
train = t["period"] == "학습"

# ---- 3) 정답 라벨 (평가 목적에만) ----
raw = pd.read_excel(mme_data.ORIGINAL_XLSX)
lab = raw[raw["status"] != "normal"].rename(columns={"src_nm": "device", "variable": "kpi"})
lab["ts"] = pd.to_datetime(lab["ds"])
t = t.merge(lab[["device", "kpi", "ts", "status"]], on=["device", "kpi", "ts"], how="left")
t["is_label"] = t["status"].notna()

rows = []
for m in ["B0", "M1", "M2"]:
    th = np.nanquantile(t.loc[train, m], 1 - detect.ALARM_BUDGET)
    alarm = t[m] >= th
    for p in ["검증", "평가"]:
        part = t["period"] == p
        lab_p = part & t["is_label"]
        ev = t[lab_p].assign(hit=alarm[lab_p]).groupby(["ts", "kpi", "status"])["hit"].any()
        rows.append({"모델": m, "구간": p, "알람 수": int((alarm & part).sum()), "라벨 N": int(lab_p.sum()),
                     "TP": int((alarm & lab_p).sum()), "Recall": round((alarm & lab_p).sum() / lab_p.sum(), 3),
                     "오탐": int((alarm & part & ~t["is_label"]).sum()),
                     "사건 N": len(ev), "잡은 사건": int(ev.sum())})
res = pd.DataFrame(rows)
res.to_csv(OUT / "eval_result.csv", index=False, encoding="utf-8-sig")
print(res.to_string(index=False))

# ---- 4) 평가 구간 놓친 라벨 (원인 분석용) ----
th2 = np.nanquantile(t.loc[train, "M2"], 1 - detect.ALARM_BUDGET)
miss = t[(t["period"] == "평가") & t["is_label"] & ~(t["M2"] >= th2)]
miss[["device", "kpi", "ts", "status", "value", "usual", "M2"]].to_csv(OUT / "eval_missed.csv", index=False, encoding="utf-8-sig")
print("\n놓친 평가 라벨:", len(miss))
print(miss.groupby(["kpi", "status"]).size().to_string())

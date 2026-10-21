"""1단계 — MME 데이터 읽기 모듈.

에이전트가 쓰는 모든 도구(KPI 조회·이상 탐지·맥락 조회)는 이 파일을 통해서만
데이터를 읽는다. 그래서 '원본 엑셀이냐, 합성 데이터냐'는 여기서 한 번만 정하면 된다.

    DATA_MODE=original   → ../Data/ 의 원본 엑셀 (로컬 전용, Git·외부 LLM 금지)
    DATA_MODE=synthetic  → data/synthetic/mme_synthetic.csv (5단계에서 생성)
"""
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

# ---- 경로 ----
PROJECT_DIR = Path(__file__).resolve().parents[1]          # corewatch_agent/
DEFAULT_XLSX = PROJECT_DIR.parent / "Data" / "Infra센터_Guided_PJT_Topic_3_Data_(5g_ss_mme).xlsx"
ORIGINAL_XLSX = Path(os.environ.get("CW_DATA_PATH", DEFAULT_XLSX)).expanduser()   # 다른 위치면 CW_DATA_PATH로 지정
SYNTHETIC_CSV = PROJECT_DIR / "data" / "synthetic" / "mme_synthetic.csv"


def data_mode() -> str:
    """지금 어떤 데이터를 쓰는지. 환경변수가 없으면 안전한 쪽(synthetic)이 기본값."""
    return os.environ.get("DATA_MODE", "synthetic")


@lru_cache(maxsize=1)
def load_table() -> pd.DataFrame:
    """데이터를 '한 줄 = 장비 · KPI · 시각 · 값' 모양의 표로 읽어 온다.

    lru_cache: 처음 한 번만 파일을 읽고, 그다음부터는 메모리에 있는 표를 재사용한다.
    (엑셀 3만 8천 행을 매번 읽으면 도구 호출마다 몇 초씩 걸리기 때문)
    """
    if data_mode() == "original":
        raw = pd.read_excel(ORIGINAL_XLSX)
    else:
        raw = pd.read_csv(SYNTHETIC_CSV)

    table = pd.DataFrame({
        "device": raw["src_nm"],                 # 장비 이름 (예: MME071)
        "kpi": raw["variable"],                  # KPI 이름 (예: pag_sms_succ_rate)
        "ts": pd.to_datetime(raw["ds"]),         # 시각 (1시간 간격)
        "value": raw["value"].astype(float),     # 값
    })
    # 정답 라벨(status)은 일부러 가져오지 않는다 — 에이전트가 정답을 보면 안 되기 때문 (DR-06)
    return table.sort_values(["device", "kpi", "ts"]).reset_index(drop=True)


# ---- KPI 정보 ----
def kpi_type(kpi: str) -> str:
    """이름에 succ_rate가 있으면 '성공률', 아니면 '시도'."""
    return "성공률" if "succ_rate" in kpi else "시도"


PROCEDURE_LABELS = {
    "comb_atch": "결합 접속", "comb_tau": "결합 위치 등록", "prd_tau": "주기적 위치 등록", "ddn": "데이터 도착 알림",
    "net_dtch": "망 측 접속 해제", "ue_dtch": "단말 접속 해제", "pag_data": "데이터 호출", "pag_hdv": "음성(HD Voice) 호출",
    "pag_sms": "문자 호출", "pag_video": "영상 호출", "srmo": "발신 서비스 요청", "srmt": "착신 서비스 요청",
}


def procedure_of(kpi: str) -> str:
    """KPI 이름에서 절차 부분만. 예) pag_sms_succ_rate → pag_sms, comb_atch_att_45005 → comb_atch"""
    return kpi.replace("_succ_rate", "").replace("_att", "").replace("_45005", "").replace("_etc", "")


def kpi_label(kpi: str) -> str:
    """화면에 보여 줄 한국어 이름. 예) pag_sms_succ_rate → 문자 호출 성공률"""
    name = PROCEDURE_LABELS.get(procedure_of(kpi), procedure_of(kpi))
    if kpi.endswith("_45005"):
        name += "(45005)"
    elif kpi.endswith("_etc"):
        name += "(기타)"
    return f"{name} {kpi_type(kpi)}"


def pair_of(kpi: str) -> str:
    """같은 절차의 짝 KPI. 예) pag_sms_att ↔ pag_sms_succ_rate"""
    if "succ_rate" in kpi:
        return kpi.replace("_succ_rate", "_att")
    return kpi.replace("_att", "_succ_rate")


def list_devices() -> list[str]:
    return sorted(load_table()["device"].unique())


def list_kpis() -> list[str]:
    return sorted(load_table()["kpi"].unique())


# ---- 조회 ----
def get_series(device: str, kpi: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """장비 하나, KPI 하나의 시간별 값을 꺼낸다. start/end는 '2022-03-12 08:00' 같은 문자열."""
    t = load_table()
    rows = t[(t["device"] == device) & (t["kpi"] == kpi)]
    if start:
        rows = rows[rows["ts"] >= pd.Timestamp(start)]
    if end:
        rows = rows[rows["ts"] <= pd.Timestamp(end)]
    return rows[["ts", "value"]].reset_index(drop=True)


if __name__ == "__main__":
    # 직접 실행해서 확인:  DATA_MODE=original python3 -m mme.data
    print("데이터 모드:", data_mode())
    t = load_table()
    print("전체 행 수:", len(t))
    print("장비:", list_devices())
    print("KPI 수:", len(list_kpis()), "| 예) pag_sms_att의 짝 →", pair_of("pag_sms_att"))
    print(get_series("MME071", "pag_sms_succ_rate", "2022-03-12 05:00", "2022-03-12 10:00"))

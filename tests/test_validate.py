"""수치 검사(Validator)와 판단 유형 규칙."""
from agent.nodes.analysis import classify
from agent.nodes.validate import check_hypothesis

FACTS = "[F1] 값 97.48% (평소 98.89%)\n[F2] 다른 장비 8대 중 8대 알람\n[F3] 짝 지표 23,439 (평소 31,834)"


def test_pass_when_numbers_come_from_facts() -> None:
    h = "원인 후보 1) 공통 구간 문제 [F2][R1] — 값이 97.48%로 하락\n점검 순서:\n1. 연동 구간 확인"
    assert check_hypothesis(h, FACTS, 3, 1)["ok"]


def test_fail_on_made_up_number() -> None:
    h = "원인 후보 1) 영향 37.5% [F1]\n점검 순서:\n1. 확인"
    r = check_hypothesis(h, FACTS, 3, 1)
    assert not r["ok"] and 37.5 in r["bad_numbers"]


def test_fail_on_missing_reference_and_steps() -> None:
    r = check_hypothesis("원인 후보 1) 장비 문제", FACTS, 3, 1)
    assert not r["ok"] and len(r["problems"]) == 2


def test_fail_on_unknown_reference_number() -> None:
    r = check_hypothesis("원인 후보 1) 문제 [F9]\n점검 순서:\n1. 확인", FACTS, 3, 1)
    assert not r["ok"]


def test_cannot_determine_is_allowed() -> None:
    assert check_hypothesis("원인을 특정할 수 없음. 추가로 위치 등록 성공률 확인 필요", FACTS, 3, 1)["ok"]


def test_classify_fleet_vs_single() -> None:
    kpi = {"kpi_type": "성공률", "direction": "급감", "run_hours": 3, "value": 97.0}
    fleet = classify(kpi, {"peer_alarms": 8, "pair_change_pct": -26.0, "pair_value": 23439})
    single = classify(kpi, {"peer_alarms": 0, "pair_change_pct": 1.0, "pair_value": 50000})
    assert "여러 장비 동시 이상" in fleet["patterns"] and "시도와 성공률이 함께 감소" in fleet["patterns"]
    assert "한 장비만 이상" in single["patterns"] and "3시간 연속 이상 (지속형)" in single["patterns"]

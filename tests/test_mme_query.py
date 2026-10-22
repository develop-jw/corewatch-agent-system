"""질문에서 알람(장비·지표·시각) 찾기."""
from agent.mme_query import parse_alarm


def test_full_query() -> None:
    a = parse_alarm("MME-A03 pag_sms_succ_rate 2026-10-16 08:00 알람 원인 분석해줘")
    assert (a.device, a.kpi, a.ts, a.found_by) == ("MME-A03", "pag_sms_succ_rate", "2026-10-16 08:00", "질문")


def test_short_time_format() -> None:
    a = parse_alarm("MME-A07 comb_tau_succ_rate 10월 17일 4시 확인")
    assert a.ts == "2026-10-17 04:00"


def test_device_only_picks_strongest_alarm() -> None:
    a = parse_alarm("MME-A04 알람 분석해줘")
    assert a.device == "MME-A04" and a.found_by == "가장 강한 알람"


def test_longer_kpi_name_wins() -> None:
    a = parse_alarm("MME-A01 comb_atch_succ_rate_45005 2026-10-16 08:00")
    assert a.kpi == "comb_atch_succ_rate_45005"


def test_no_device_returns_none() -> None:
    assert parse_alarm("알람 원인 알려줘") is None

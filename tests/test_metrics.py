"""운영 지표 집계 테스트."""
from agent.metrics import Metrics


def test_metrics_summary_counts_validation_and_approval() -> None:
    m = Metrics()
    m.record({"route": "incident", "cost_usd": 0.01, "collected": [{"error": None}, {"error": "timeout"}],
              "events": [{"type": "Validation", "attempt": 1, "verdict": "불합격", "problems": ["조회한 사실에 없는 숫자를 썼습니다: 37.5."]},
                         {"type": "Validation", "attempt": 2, "verdict": "합격", "problems": []}],
              "approval": {"required": True, "approved": False, "by": "운영자"}}, 3.0)
    m.record({"route": "chat", "events": []}, 1.0)
    s = m.summary()
    assert s["requests"] == 2 and s["by_route"] == {"incident": 1, "chat": 1}
    assert s["validation"]["first_pass_rate"] == 0.0 and s["approval"]["held"] == 1
    assert s["subagent_failures"] == 1 and s["latency_sec"]["avg"] == 2.0

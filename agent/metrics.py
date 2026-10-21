"""운영 지표 — 서버 메모리에 요청 결과를 모아 /metrics로 보여 준다 (서버를 다시 켜면 0부터)."""
import statistics
import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    started: float = field(default_factory=time.time)
    requests: int = 0
    by_route: dict = field(default_factory=dict)          # incident / chat / need_info
    latencies: list = field(default_factory=list)          # 리포트 완성까지 걸린 초 (승인 대기 시간 제외)
    validation_first_pass: int = 0
    validation_runs: int = 0
    validation_problems: dict = field(default_factory=dict)
    approval_requested: int = 0
    approval_approved: int = 0
    approval_held: int = 0
    cost_usd: float = 0.0
    subagent_failures: int = 0

    def record(self, state: dict, elapsed: float) -> None:
        """그래프 실행 1건이 끝났을 때 최종 상태로 지표를 갱신한다."""
        self.requests += 1
        route = state.get("route", "unknown")
        self.by_route[route] = self.by_route.get(route, 0) + 1
        self.latencies.append(round(elapsed, 2))
        self.cost_usd = round(self.cost_usd + state.get("cost_usd", 0.0), 6)
        self.subagent_failures += sum(1 for c in state.get("collected", []) if c.get("error"))
        for ev in state.get("events", []):
            if ev.get("type") == "Validation":
                if ev.get("attempt") == 1:
                    self.validation_runs += 1
                    self.validation_first_pass += ev.get("verdict") == "합격"
                for p in ev.get("problems", []):
                    key = p.split(":")[0].split(".")[0][:30]
                    self.validation_problems[key] = self.validation_problems.get(key, 0) + 1
        ap = state.get("approval", {})
        if ap.get("required") and ap.get("by") == "운영자":
            self.approval_approved += bool(ap.get("approved"))
            self.approval_held += not ap.get("approved")

    def summary(self) -> dict:
        lat = sorted(self.latencies)
        p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))] if lat else None
        return {
            "uptime_sec": round(time.time() - self.started),
            "requests": self.requests, "by_route": self.by_route,
            "latency_sec": {"avg": round(sum(lat) / len(lat), 2) if lat else None,
                            "p50": statistics.median(lat) if lat else None, "p95": p95},
            "validation": {"first_pass_rate": round(self.validation_first_pass / self.validation_runs, 3) if self.validation_runs else None,
                           "runs": self.validation_runs, "problems": self.validation_problems},
            "approval": {"requested": self.approval_requested, "approved": self.approval_approved, "held": self.approval_held},
            "subagent_failures": self.subagent_failures,
            "cost_usd": self.cost_usd,
        }

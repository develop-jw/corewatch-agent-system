"""LangSmith 연동 — 실행 기록(trace) 링크, 자동 피드백(내용 검사 · 운영자 승인), 최근 실행 조회.

LANGCHAIN_TRACING_V2=true 이고 LANGCHAIN_API_KEY가 있을 때만 동작한다. 합성 데이터 모드에서만 켤 수 있다
(실제 데이터 + LangSmith 조합은 server.check_data_mode가 시작 단계에서 거부).
LangSmith 호출이 실패해도 분석 서비스는 영향을 받지 않도록 모든 함수는 예외를 삼키고 빈 결과를 돌려준다.
"""
import os
import time
from functools import lru_cache
from typing import Any


def tracing_enabled() -> bool:
    on = os.environ.get("LANGCHAIN_TRACING_V2", os.environ.get("LANGSMITH_TRACING", "false")).lower() == "true"
    key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY") or ""
    return on and key.startswith("lsv2_") and len(key) > 20


def project_name() -> str:
    return os.environ.get("LANGCHAIN_PROJECT") or os.environ.get("LANGSMITH_PROJECT") or "default"


@lru_cache(maxsize=1)
def _client() -> Any:
    """조회·피드백 전용 클라이언트 — 화면이 오래 멈추지 않도록 짧은 시간 제한과 재시도 1회."""
    from langsmith import Client
    from urllib3.util.retry import Retry
    return Client(timeout_ms=(3000, 8000), retry_config=Retry(total=1, backoff_factor=0.5))


def send_feedback(trace_id: str, values: dict, approval: dict | None = None) -> list[str]:
    """한 번의 분석(trace)에 자동 피드백을 단다 — LangSmith 화면에서 실행별 품질을 바로 보고 걸러낼 수 있게.
    · content_check: 최종 내용 검사 통과(1) / 경고(0), 반려 사유는 comment
    · first_pass: 1차 작성이 바로 통과했는지(1/0)
    · operator_approval: 운영자 승인(1) / 보류(0) — 승인 단계가 있었을 때만"""
    if not (tracing_enabled() and trace_id):
        return []
    sent: list[str] = []
    try:
        time.sleep(2.0)   # 실행 기록이 먼저 올라가도록 잠깐 기다린다
        c = _client()
        if values.get("validation_detail"):
            d = values["validation_detail"]
            c.create_feedback(trace_id, key="content_check", score=1 if d.get("ok") else 0,
                              comment=" / ".join(d.get("problems", [])) or "통과", trace_id=trace_id)
            sent.append("content_check")
            first = next((e for e in values.get("events", []) if e.get("type") == "Validation" and e.get("attempt") == 1), None)
            if first:
                c.create_feedback(trace_id, key="first_pass", score=1 if first.get("verdict") == "합격" else 0,
                                  comment=" ".join(first.get("problems", [])) or "1차 통과", trace_id=trace_id)
                sent.append("first_pass")
        if approval and approval.get("required") and approval.get("by") == "운영자":
            c.create_feedback(trace_id, key="operator_approval", score=1 if approval.get("approved") else 0,
                              comment="; ".join(approval.get("actions", [])), trace_id=trace_id)
            sent.append("operator_approval")
    except Exception as e:   # 기록 실패가 서비스 장애가 되지 않게
        sent.append(f"error: {type(e).__name__}")
    return sent


def run_link(trace_id: str) -> dict:
    """분석 1건의 LangSmith 화면 주소와 요약 (지연 시간, 토큰, 피드백)."""
    if not tracing_enabled():
        return {"enabled": False}
    try:
        c = _client()
        run = c.read_run(trace_id)
        return {"enabled": True, "url": c.get_run_url(run=run, project_name=project_name()),
                "status": run.status, "latency_sec": _latency(run), "total_tokens": run.total_tokens,
                "feedback": {k: v.get("avg") for k, v in (run.feedback_stats or {}).items()}}
    except Exception as e:
        return {"enabled": True, "pending": True, "error": type(e).__name__}


def status(limit: int = 8) -> dict:
    """에이전트 운영 화면용 — 추적 켜짐 여부, 프로젝트 주소, 최근 실행 목록."""
    info = {"enabled": tracing_enabled(), "project": project_name()}
    if not info["enabled"]:
        return info
    try:
        c = _client()
        proj = c.read_project(project_name=project_name())
        info["project_url"] = getattr(proj, "url", None)
        runs = c.list_runs(project_name=project_name(), is_root=True, limit=limit)
        info["recent"] = [{"id": str(r.id), "name": r.name, "start": _local(r.start_time),
                           "latency_sec": _latency(r), "status": r.status, "tokens": r.total_tokens,
                           "feedback": {k: v.get("avg") for k, v in (r.feedback_stats or {}).items()}} for r in runs]
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return info


def _local(t: Any) -> str:
    """LangSmith 시각(UTC)을 이 PC의 현지 시각으로."""
    from datetime import timezone
    if not t:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone().strftime("%m-%d %H:%M:%S")


def _latency(run: Any) -> float | None:
    if run.start_time and run.end_time:
        return round((run.end_time - run.start_time).total_seconds(), 2)
    return None

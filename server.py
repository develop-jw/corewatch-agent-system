"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"


def check_data_mode() -> str:
    """원본 데이터는 내 컴퓨터 안에서만: 원본 모드에서 외부 LLM(OpenAI)·LangSmith 추적을 켜면 기동을 거부한다."""
    mode = os.environ.get("DATA_MODE", "synthetic")
    backend = os.environ.get("LLM_BACKEND", "fake")
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    if mode == "original" and (backend == "openai" or tracing):
        raise RuntimeError("원본 데이터 모드(DATA_MODE=original)에서는 OpenAI 백엔드와 LangSmith 추적을 쓸 수 없습니다. "
                           "LLM_BACKEND=ollama, LANGCHAIN_TRACING_V2=false로 실행하거나 DATA_MODE=synthetic을 쓰세요.")
    return mode


def _build_llms() -> tuple[str, Any, Any]:
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global CREATION_COUNT
    CREATION_COUNT += 1
    app.state.data_mode = check_data_mode()
    mcp = MCPClient()
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    backend, fast_llm, quality_llm = _build_llms()
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
    )
    app.state.deps = deps
    # 사람 승인(HITL): 승인 대기 중인 그래프 상태를 InMemorySaver가 기억한다 (서버를 끄면 사라짐)
    app.state.hitl = os.environ.get("HITL_ENABLED", "true").lower() == "true"
    app.state.graph = (build_graph(deps, checkpointer=InMemorySaver() if app.state.hitl else None)
                       if deps.version == "v2" else None)
    app.state.pending = {}   # run_id → {"thread_id", "message"} : 승인을 기다리는 요청
    app.state.instance_id = id(deps)
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "data_mode": app.state.data_mode,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name}


@app.get("/meta")
async def meta() -> dict:
    """화면이 처음 열릴 때 쓰는 정보: 데이터 모드, 데이터 기간, 평가(알람 표시) 기간."""
    from mme import data as mme_data
    from mme import detect as mme_detect
    t = mme_data.load_table()
    return {"data_mode": app.state.data_mode, "start": str(t["ts"].min()), "end": str(t["ts"].max()),
            "alarm_from": str(mme_detect.valid_end() + pd.Timedelta(hours=1)),
            "devices": mme_data.list_devices(), "model": mme_detect.MODEL_VERSION}


@app.get("/alarms")
async def alarms(start: str | None = None, end: str | None = None, limit: int = 100) -> list[dict]:
    """알람 목록 (점수 높은 순). 화면의 알람 패널이 쓴다."""
    from mme import detect as mme_detect
    rows = mme_detect.list_alarms(start, end).sort_values("score", ascending=False).head(limit)
    return [{"device": r.device, "kpi": r.kpi, "ts": r.ts.strftime("%Y-%m-%d %H:00"),
             "value": round(float(r.value), 2), "usual": round(float(r.usual), 2),
             "direction": r.direction, "score": round(float(r.score), 3)} for r in rows.itertuples()]


@app.get("/version")
async def version() -> dict:
    return {"supervisor_version": app.state.deps.version}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
    }


def _config(run_id: str) -> dict:
    """요청마다 고유한 run_id — checkpointer가 이 번호로 멈춘 그래프를 찾아 이어 간다."""
    return {"configurable": {"thread_id": run_id}}


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str) -> tuple[dict, str | None]:
    deps = app.state.deps
    history = deps.session_store.load(thread_id)   # Wave 12
    if deps.version == "v2":
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        result = await app.state.graph.ainvoke(
            {"query": message, "thread_id": thread_id, "history": history,
             "events": [], "cost_usd": 0.0}, _config(run_id))
        if "__interrupt__" in result:   # 위험 조치 → 승인 대기. 확정 전이므로 대화 기록에는 아직 남기지 않는다
            app.state.pending[run_id] = {"thread_id": thread_id, "message": message}
            req = result["__interrupt__"][0].value
            result = {**result, "final": req["draft"] + "\n\n(운영자 승인 대기 중)",
                      "approval_request": {"run_id": run_id, "actions": req["actions"]}}
            rt = get_current_run_tree()
            return result, (str(rt.id) if rt else None)
    else:
        result = await run_v1(deps, message, thread_id, history)   # Wave 14
    deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", result["final"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    result, run_id = await _handle_chat(req.message, req.thread_id)
    return {"reply": result["final"], "events": result.get("events", []),
            "approval_request": result.get("approval_request"), "metadata": _metadata(result, run_id)}


class ResumeRequest(BaseModel):
    run_id: str
    approved: bool


@app.post("/chat/resume")
async def chat_resume(req: ResumeRequest) -> dict:
    """승인 대기 중인 리포트에 운영자의 답(승인/보류)을 넣어 이어서 실행한다."""
    pending = app.state.pending.pop(req.run_id, None)
    if pending is None:
        return {"error": "승인을 기다리는 요청이 없습니다 (이미 처리했거나 서버가 다시 시작됨)."}
    result = await app.state.graph.ainvoke(Command(resume={"approved": req.approved}), _config(req.run_id))
    store = app.state.deps.session_store
    store.append(pending["thread_id"], "user", pending["message"])
    store.append(pending["thread_id"], "assistant", result["final"])
    return {"reply": result["final"], "events": result.get("events", []), "metadata": _metadata(result, None)}


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default") -> StreamingResponse:
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps

    async def gen() -> AsyncIterator[str]:
        history = deps.session_store.load(thread_id)
        if deps.version != "v2":
            result = await run_v1(deps, message, thread_id, history)
            for ev in result["events"]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': result['final']}, ensure_ascii=False)}\n\n"
        else:
            run_id = f"run-{uuid.uuid4().hex[:8]}"
            inp = {"query": message, "thread_id": thread_id, "history": history, "events": [], "cost_usd": 0.0}
            async for chunk in _stream_graph(inp, run_id, 0, message, thread_id):
                yield chunk
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/chat/resume/stream")
async def chat_resume_stream(run_id: str, approved: bool) -> StreamingResponse:
    """사람 승인(HITL) — 운영자가 [승인]/[보류]를 누르면 멈춘 그래프를 이어서 실행하고 진행 상황을 SSE로 보낸다."""
    async def gen() -> AsyncIterator[str]:
        pending = app.state.pending.pop(run_id, None)
        if pending is None:
            msg = "승인을 기다리는 요청이 없습니다 (이미 처리했거나 서버가 다시 시작됨)."
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': msg}, ensure_ascii=False)}\n\n"
        else:
            snap = await app.state.graph.aget_state(_config(run_id))
            seen = len(snap.values.get("events", []))
            async for chunk in _stream_graph(Command(resume={"approved": approved}), run_id, seen,
                                             pending["message"], pending["thread_id"]):
                yield chunk
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _stream_graph(inp: dict | Command, run_id: str, seen: int, message: str,
                        thread_id: str) -> AsyncIterator[str]:
    """그래프를 실행하며 SSE 문자열을 내보낸다.
    "updates"는 노드가 끝날 때마다의 상태 변화, "custom"은 실행 중 바로 흘려보내는 토큰(AnswerToken)이다.
    위험 조치가 있으면 그래프가 멈추고(__interrupt__) ApprovalRequired 이벤트를 보낸 뒤 끝난다."""
    deps = app.state.deps
    final, last_prompt = "", None
    async for mode, payload in app.state.graph.astream(inp, _config(run_id), stream_mode=["updates", "custom"]):
        if mode == "custom":
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            continue
        for node_name, delta in payload.items():
            if node_name == "__interrupt__":
                req = delta[0].value
                app.state.pending[run_id] = {"thread_id": thread_id, "message": message}
                ev = {"type": "ApprovalRequired", "run_id": run_id, "actions": req["actions"], "draft": req["draft"]}
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                return
            delta = delta or {}
            for ev in (delta.get("events") or [])[seen:]:
                yield f"data: {json.dumps({**ev, 'node': node_name}, ensure_ascii=False)}\n\n"
            if delta.get("events"):
                seen = len(delta["events"])
            if delta.get("final"):
                final = delta["final"]
            if delta.get("last_prompt"):
                last_prompt = delta["last_prompt"]
    if not last_prompt:
        snap = await app.state.graph.aget_state(_config(run_id))
        last_prompt = snap.values.get("last_prompt") if snap else None
    yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': final, 'prompt': last_prompt}, ensure_ascii=False)}\n\n"
    deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", final)





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")

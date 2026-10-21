"""Wave 4/10a — 결정론적 Fake LLM 2종 (fast/quality) + 실측 비용 계산.

실제 LLM 호출 없이도 파이프라인 전체가 결정론적으로 재현되도록, 프롬프트 내용에 따라
정해진 응답을 돌려준다. 각 호출은 LangSmith에 run_type="llm"으로 기록되고, 문자열
길이 기반 토큰 추정치로 비용을 계산해 상태에 누적한다 (Wave 10a의 모델 분리 근거).
"""
import re

from langsmith import traceable

from .prompts import HYPOTHESIS_SYSTEM, INCIDENT_PATTERN, hypothesis_user


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float) -> None:
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """분류·계획 등 짧고 정형적인 단계 전담 (저비용)."""

    @traceable(run_type="llm", name="fast_llm.route")
    def route(self, query: str) -> tuple[dict, dict]:
        """Wave 5 — 쿼리 라우팅. 장애 관련이면 incident 파이프라인, 아니면 chat 직행."""
        incident = bool(re.search(INCIDENT_PATTERN, query))
        result = {"route": "incident" if incident else "chat", "needs_analysis": True}
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.plan")
    def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        """Wave 4 — Plan/Task 분해. MCP Prompt가 준 표준 절차를 따라 계획을 세운다.

        Wave 6 재현: needs_analysis=False라고 스스로 판단해 놓고도 analysis task를
        끼워 넣는 '모델의 자기모순'을 결정론적으로 재현한다 → gate 노드가 걸러낸다.
        """
        systems = re.findall(r"[a-z][a-z0-9-]+-(?:service|\d+)", query) or ["auth-service"]
        if "web-01" not in systems:
            systems.append("web-01")   # 연관 시스템 비교 조회 → Wave 10b 병렬 실행 대상
        tasks = [
            {"id": f"task-{i}", "agent": "research", "kind": "monitoring",
             "target": s, "status": "pending"}
            for i, s in enumerate(dict.fromkeys(systems))
        ]
        tasks.append({"id": "task-analysis", "agent": "analysis",
                      "kind": "pattern-analysis", "status": "pending"})   # 자기모순 task
        result = {"needs_analysis": False, "tasks": tasks}
        return result, self._usage(query + procedure_prompt, str(result))

    @traceable(run_type="llm", name="fast_llm.analyze")
    def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        """Wave 6 보완 — 여러 시스템의 monitoring 결과 사이에 공통 패턴이 있는지 비교."""
        mon = [c["data"] for c in collected if c["source"] == "monitoring"]
        degraded = [d["system_name"] for d in mon if d.get("status") != "healthy"]
        if len(degraded) >= 2:
            summary = (f"{', '.join(degraded)} 복수 시스템이 동시에 저하됨 — "
                       f"공유 의존성 등 공통 원인이 의심됨")
        elif degraded:
            summary = f"{degraded[0]}만 단독 저하 — 연관 시스템은 정상, 국소 이슈로 추정"
        else:
            summary = "모니터링된 시스템 모두 healthy — 패턴상 이상 징후 없음"
        return summary, self._usage(str(collected), summary)

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """Wave 5의 direct 경로 + Wave 12 증명용 — 히스토리에서 이름을 기억해낸다.

        fake 백엔드는 실제 네트워크 호출이 없어 토큰 단위로 쪼갤 대상이 없다 —
        ollama_llm.py와 인터페이스(on_token 콜백)만 맞추고 결과를 한 번에 반환한다."""
        for msg in reversed(history):
            m = re.search(r"제 이름은 (\S+?)입니다", msg.get("content", ""))
            if m and re.search(r"이름이 뭐", query):
                reply = f"{m.group(1)}님이라고 말씀하셨어요. 무엇을 도와드릴까요?"
                if on_token is not None:
                    on_token(reply)
                return reply, self._usage(query, reply)
        m = re.search(r"제 이름은 (\S+?)입니다", query)
        if m:
            reply = f"안녕하세요, {m.group(1)}님! 무엇을 도와드릴까요?"
        else:
            reply = "안녕하세요! 무엇을 도와드릴까요?"
        if on_token is not None:
            on_token(reply)
        return reply, self._usage(query, reply)


class QualityLLM(FakeLLM):
    """최종 답변 작성 전담 (고품질·고비용)."""

    @traceable(run_type="llm", name="quality_llm.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
              feedback: str | None, on_token=None) -> tuple[str, dict]:
        """Wave 8 재현: 1차 초안은 의도적으로 저품질(너무 짧음) → validator가 반려하면
        피드백을 반영한 구조화된 2차 초안을 내놓는다.

        fake 백엔드는 실제 네트워크 호출이 없어 토큰 단위로 쪼갤 대상이 없다 —
        ollama_llm.py와 인터페이스(on_token 콜백)만 맞추고 결과를 한 번에 반환한다."""
        if attempt == 1:
            draft = "재시작하면 됩니다."
            if on_token is not None:
                on_token(draft)
            return draft, self._usage(query, draft)

        mon = [c for c in collected if c["source"] == "monitoring"]
        run = [c for c in collected if c["source"] == "runbook"]
        summary = " · ".join(
            f"{c['data']['system_name']} 상태={c['data']['status']}, "
            f"CPU {c['data']['cpu_percent']}% (알림 {len(c['data']['alerts'])}건)"
            for c in mon)
        lines = [f"'{query}' 진단 결과:", f"1) 모니터링: {summary}"]
        if run:
            body_lines = [l.strip() for l in run[0]["data"].strip().splitlines() if l.strip()]
            title = body_lines[0].lstrip("# ")
            steps = [m.group(1) for l in body_lines[1:]
                     if (m := re.match(r"^\d+\.\s+(.*)", l))]
            advice = " ".join(steps[1:3]) or "표준 절차를 따라 조치하세요."
            lines.append(f"2) 표준 절차: '{title}'에 따라 {advice}")
        lines.append("3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.")
        draft = " ".join(lines)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(query + str(feedback), draft)


    @traceable(run_type="llm", name="quality_llm.write_hypothesis")
    async def write_hypothesis(self, alarm_desc: str, facts_text: str, runbook_text: str, attempt: int,
                               feedback: str | None, on_token=None) -> tuple[str, dict]:
        """CoreWatch ③ AI 원인 추정 (결정론). 1차는 일부러 사실에 없는 숫자(37.5)를 넣어
        수치 검사(validate)가 반려하는 흐름을 재현하고, 2차에 고친다."""
        common = "여러 장비 동시 이상" in facts_text
        lines = []
        if common:
            lines.append("원인 후보 1) 여러 장비에 공통인 연동 구간 또는 상위 망 문제 [F4][R1]")
        else:
            lines.append("원인 후보 1) 이 장비 자체의 설정 변경이나 담당 지역 기지국 문제 [F4][R1]")
        if "함께 감소" in facts_text:
            lines.append("원인 후보 2) 요청을 보내는 쪽(상위 장비·연동 구간)의 문제 — 시도와 성공률이 함께 줄었음 [F5][R1]")
        if attempt == 1:
            lines.append("영향 규모는 약 37.5%로 추정됩니다.")
        lines += ["점검 순서:", "1. [R1]의 첫 번째 점검 항목부터 확인", "2. 같은 시각 다른 장비 상태 재확인 [F4]",
                  "3. 짝 지표 변화 추이 관찰 [F5]"]
        if not common:   # 한 장비 문제일 때만: 위험 조치 → 사람 승인(HITL) 단계로 넘어간다
            lines.append("4. 원인이 이 장비 내부로 좁혀지면 해당 장비 재기동 검토 (운영자 승인 필요)")
        text = "\n".join(lines)
        if on_token is not None:
            on_token(text)
        usage = self._usage(hypothesis_user(alarm_desc, facts_text, runbook_text, feedback), text)
        usage["prompt"] = f"[SYSTEM]\n{HYPOTHESIS_SYSTEM}\n\n[USER]\n" + hypothesis_user(alarm_desc, facts_text, runbook_text, feedback)
        return text, usage

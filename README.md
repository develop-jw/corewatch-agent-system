# CoreWatch — MME 코어망 KPI 이상 탐지 및 원인 분석 AI Agent

MME 장비 9대 × KPI 26개의 시간별 지표에서 ML 모델로 이상을 탐지하고, 알람마다
짝 KPI · 같은 시각 다른 장비 · 점검 절차 문서를 근거로 원인 가설과 점검 순서를 제시하는
운영자용 AI 에이전트입니다.

> SKALA AI 서비스 개발 Mini-project 과제 2(신규 프로젝트) — SKALA 제공 Base AI Agent(`all_total_vue`)를
> 기반으로 주제를 'MME 코어망 KPI 이상'으로 바꿔 확장했습니다.

## 주요 기술

| 기술 | 내용 |
|---|---|
| 멀티 에이전트 (LangGraph) | Supervisor가 조사 서브에이전트(KPI 분석 · 장비 비교 · 절차 검색)를 호출 *(구현 중)* |
| MCP | KPI 조회 · 이상 탐지 · 알람 목록 · 맥락 조회 도구 |
| RAG | MME 점검 절차(runbook) 의미 검색 *(구현 중)* |
| ML 이상 탐지 | Isolation Forest (특징 4종: 평소 시간대 대비 · 직전 시각 대비 · 다른 장비 대비 · 짝 KPI) |

Base에서 이어받은 구조: FastAPI + SSE 스트리밍, 권위 게이트, 재계획, 재시도, SQLite 세션, LangSmith 추적, LLM 백엔드 교체(fake · ollama · openai)

## 폴더 구조

```
mme/               MME 데이터 읽기(data.py) · 이상 탐지(detect.py)
mcp_server.py      MCP 도구 (Base 도구 + CoreWatch 도구)
agent/             LangGraph 에이전트 (Base)
eval/              탐지 모델 평가 (정답 라벨은 여기서만 사용)
tests/             도구 확인용 스크립트
server.py          FastAPI 백엔드 (:8100)
web_chat.html      Vue 3 화면 (web_demo_server.py, :8600)
doc/               Base 프로그램 설명서
```

## 실행 방법

Python 3.11 이상이 필요합니다.

```bash
bash sh/setup_venv.sh          # 가상환경 + 패키지 설치 (처음 1회)
cp .env.example .env           # 환경 설정 파일 만들기

# 도구 확인 (원본 데이터, 로컬에서만)
DATA_MODE=original .venv/bin/python3 tests/try_kpi_tool.py
DATA_MODE=original .venv/bin/python3 tests/try_detect_tool.py

# 탐지 모델 평가
DATA_MODE=original .venv/bin/python3 eval/eval_detector.py

# 에이전트 + 화면
bash sh/run_agent_ollma.sh     # 로컬 Ollama
bash sh/run_frontend.sh        # http://localhost:8600
```

## 데이터 원칙

- SK 제공 원본 데이터(`*.xlsx`, `*.p`)는 저장소에 올리지 않습니다. 기본 위치는 저장소 바깥 `../Data/`입니다.
- 원본 데이터 모드(`DATA_MODE=original`)는 로컬 LLM(Ollama)으로만 실행합니다.
- 외부 LLM(OpenAI)·LangSmith·공개 시연에는 원본과 무관하게 만든 합성 데이터(`DATA_MODE=synthetic`)를 씁니다.
- 정답 라벨은 평가 스크립트(`eval/`)에서만 읽고, 에이전트 코드(`mme/`, `agent/`, `mcp_server.py`)는 읽지 않습니다.

## 탐지 모델 평가 결과 (평가 구간 1회 채점)

학습 72시간 / 검증 24시간 / 평가 72시간 시간순 분할, 학습 구간 알람률 0.3% 고정

| 모델 | 검증 (10건) | 평가 (29건) | 평가 구간 오탐 |
|---|---|---|---|
| B0 고정 기준선 | 4 | 10 | 22 |
| M1 시간대 평소값 | 9 | 17 | 41 |
| **M2 Isolation Forest** | **10** | **20** | 66 |

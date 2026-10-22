#!/bin/bash
# CoreWatch 실행 (macOS) — Finder에서 더블클릭하거나 터미널에서 ./sh/run_mac.command
# 실제 AI로 실행: .env에 OPENAI_API_KEY가 있으면 OpenAI, 없으면 가짜 AI(fake)로 켭니다.
cd "$(dirname "$0")/.." || exit 1
echo "== CoreWatch 실행 준비 =="
if [ ! -d .venv ]; then
  (command -v uv >/dev/null && uv venv -p 3.12 .venv) || python3 -m venv .venv
fi
source .venv/bin/activate
python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 이상이 필요합니다"' || exit 1
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env

# ---- 이미 켜진 서버가 포트를 잡고 있으면 끈다 ----
for p in 8100 8600; do lsof -ti :$p | xargs kill -9 2>/dev/null; done

export DATA_MODE=synthetic LANGCHAIN_TRACING_V2=false PYTHONUTF8=1
if grep -q '^OPENAI_API_KEY=sk-' .env; then export LLM_BACKEND=openai; else export LLM_BACKEND=fake; fi
mkdir -p logs
echo "== 분석 서버 시작 (LLM_BACKEND=$LLM_BACKEND) — 이상 탐지 모델 계산에 20초 정도 걸립니다 =="
python server.py > logs/server.log 2>&1 &
SERVER_PID=$!
for i in $(seq 1 60); do curl -s localhost:8100/health >/dev/null && break; sleep 1; done
echo "== 화면 서버 시작 =="
python web_demo_server.py > logs/web.log 2>&1 &
WEB_PID=$!
sleep 2
open http://localhost:8600
echo ""
echo "브라우저에서 http://localhost:8600 이 열렸습니다. 이 창을 닫거나 Ctrl+C를 누르면 서버가 꺼집니다."
trap 'kill $SERVER_PID $WEB_PID 2>/dev/null; exit 0' INT TERM HUP
wait

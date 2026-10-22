@echo off
chcp 65001 > nul
rem CoreWatch 실행 (Windows) — 더블클릭하면 서버 2개를 켜고 브라우저를 엽니다.
cd /d "%~dp0\.."
if not exist .venv ( py -3.12 -m venv .venv || python -m venv .venv )
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if not exist .env copy .env.example .env > nul

rem ---- 이미 켜진 서버가 포트를 잡고 있으면 끈다 ----
for %%P in (8100 8600) do for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do taskkill /PID %%A /F > nul 2>&1

set DATA_MODE=synthetic
set LANGCHAIN_TRACING_V2=false
set PYTHONUTF8=1
set LLM_BACKEND=fake
findstr /b "OPENAI_API_KEY=sk-" .env > nul && set LLM_BACKEND=openai
if not exist logs mkdir logs
echo == 분석 서버 시작 (LLM_BACKEND=%LLM_BACKEND%) - 20초 정도 걸립니다 ==
start "CoreWatch server" /min cmd /c ".venv\Scripts\python.exe server.py > logs\server.log 2>&1"
timeout /t 20 /nobreak > nul
echo == 화면 서버 시작 ==
start "CoreWatch web" /min cmd /c ".venv\Scripts\python.exe web_demo_server.py > logs\web.log 2>&1"
timeout /t 3 /nobreak > nul
start http://localhost:8600
echo 브라우저에서 http://localhost:8600 이 열렸습니다. 끌 때는 작업 표시줄의 CoreWatch 창 2개를 닫으세요.
pause

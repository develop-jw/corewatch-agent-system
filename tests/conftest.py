"""pytest 공통 설정 — 테스트는 항상 합성 데이터와 가짜(fake) LLM으로, 외부 호출 없이 돈다."""
import os
import sys
from pathlib import Path

os.environ["DATA_MODE"] = "synthetic"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LLM_BACKEND"] = "fake"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

"""LLM 백엔드 선택 — 서버와 평가 스크립트가 같은 방식으로 모델을 만든다.

LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API, 합성 데이터에서만)
"""
import os
from typing import Any


def build_llms() -> tuple[str, Any, Any]:
    """(백엔드 이름, 빠른 모델, 고품질 모델)을 돌려준다."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from .ollama_llm import OllamaFastLLM, OllamaQualityLLM
        return (backend,
                OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"), usd_per_1k_tokens=0.00015),
                OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"), usd_per_1k_tokens=0.005))
    if backend == "openai":
        from .openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        return (backend,
                OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"), usd_per_1k_tokens=0.00015),
                OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"), usd_per_1k_tokens=0.005))
    from .fake_llm import FastLLM, QualityLLM
    return backend, FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015), QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)

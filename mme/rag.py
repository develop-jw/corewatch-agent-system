"""점검 절차 문서 검색 (RAG).

data/runbooks_mme/*.md 문서를 '## 제목' 단위 조각으로 나누고, 각 조각을 벡터로 바꿔 둔 뒤
질문과 뜻이 가까운 조각을 찾는다.

벡터 만드는 방법 (RAG_BACKEND 환경변수)
- ollama : 로컬 Ollama 임베딩 모델(OLLAMA_EMBED_MODEL, 기본 nomic-embed-text) — 뜻 기반 검색
- tfidf  : 글자 조각(2~4글자) 빈도 기반 — 추가 모델 없이 동작 (기본값)
조각마다 'KPI:' 줄의 지표 목록을 메타데이터로 갖고 있어, 알람 지표와 맞는 조각은 점수를 더 준다.
"""
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

DOC_DIR = Path(__file__).resolve().parents[1] / "data" / "runbooks_mme"
KPI_BOOST = 0.15        # 알람 지표가 메타데이터에 있는 조각에 더하는 점수


@dataclass
class Chunk:
    doc_id: str                 # 예) RB-04
    doc_title: str              # 예) 호출(페이징) 절차 점검
    section: str                # 예) 문자 호출 성공률 하락
    kpis: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def ref(self) -> str:
        return f"{self.doc_id} · {self.section}"


def load_chunks() -> list[Chunk]:
    """문서를 '## 제목' 단위로 나눈다."""
    chunks: list[Chunk] = []
    for path in sorted(DOC_DIR.glob("RB-*.md")):
        body = path.read_text(encoding="utf-8")
        first = body.splitlines()[0].lstrip("# ").strip()             # "RB-04 호출(페이징) 절차 점검"
        doc_id, doc_title = first.split(" ", 1)
        for part in re.split(r"^## ", body, flags=re.M)[1:]:
            lines = part.strip().splitlines()
            section = lines[0].strip()
            kpi_line = next((l for l in lines if l.startswith("KPI:")), "KPI:")
            kpis = [k.strip() for k in kpi_line[4:].split(",") if k.strip()]
            text = "\n".join(l for l in lines[1:] if not l.startswith("KPI:")).strip()
            chunks.append(Chunk(doc_id, doc_title, section, kpis, text))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk], backend: str | None = None) -> None:
        self.chunks = chunks
        self.backend = backend or os.environ.get("RAG_BACKEND", "tfidf")
        docs = [f"{c.doc_title} {c.section} {' '.join(c.kpis)} {c.text}" for c in chunks]
        if self.backend == "ollama":
            self.model = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
            matrix = self._embed(docs)
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
            matrix = self.vectorizer.fit_transform(docs).toarray()
        self.matrix = self._normalize(matrix)

    @staticmethod
    def _normalize(m: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.where(n == 0, 1, n)

    def _embed(self, texts: list[str]) -> np.ndarray:
        import httpx
        resp = httpx.post("http://127.0.0.1:11434/api/embed", json={"model": self.model, "input": texts}, timeout=120.0)
        resp.raise_for_status()
        return np.array(resp.json()["embeddings"], dtype=float)

    def _vector(self, query: str) -> np.ndarray:
        if self.backend == "ollama":
            return self._normalize(self._embed([query]))[0]
        return self._normalize(self.vectorizer.transform([query]).toarray())[0]

    def search(self, query: str, kpi: str | None = None, k: int = 3, doc_id: str | None = None) -> list[dict]:
        """질문과 가장 가까운 조각 k개.
        kpi를 주면 메타데이터(KPI 목록)에 그 지표가 있는 조각에 점수를 더하고,
        doc_id를 주면 그 문서 안에서만 찾는다."""
        sims = self.matrix @ self._vector(query)
        if kpi:
            sims = sims + np.array([KPI_BOOST if kpi in c.kpis else 0.0 for c in self.chunks])
        if doc_id:
            sims = np.where([c.doc_id == doc_id for c in self.chunks], sims, -np.inf)
        order = [i for i in np.argsort(-sims)[:k] if np.isfinite(sims[i])]
        return [{"ref": self.chunks[i].ref, "doc_id": self.chunks[i].doc_id, "section": self.chunks[i].section,
                 "kpis": self.chunks[i].kpis, "text": self.chunks[i].text, "score": round(float(sims[i]), 3)}
                for i in order]


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    return Retriever(load_chunks())

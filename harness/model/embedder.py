"""KURE-v1 임베더 — sentence-transformers, 인프로세스 실행 (docs/02 §2).

- 서버 불필요, PyTorch MPS 가속, 상주 ~1.2GB.
- 모델 로드가 느리므로(수 초) 지연 로드 + 프로세스당 1회.
"""

from __future__ import annotations

from functools import lru_cache

from harness import config
from harness.obs import trace


@lru_cache(maxsize=1)
def _model():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return SentenceTransformer(config.EMBED_MODEL_ID, device=device)


def device() -> str:
    return str(_model().device)


def embed(texts: list[str], *, batch_size: int = 16) -> list[list[float]]:
    """텍스트 목록 → 1024차원 벡터 목록 (cosine용 정규화 포함)."""
    with trace.timer() as t:
        vectors = _model().encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        _release_mps_cache()
    trace.log("retrieve", op="embed", n_texts=len(texts), latency_ms={"total": t.ms})
    return vectors.tolist()


def _release_mps_cache() -> None:
    """MPS 캐시 버퍼 반환 — Ollama와 통합 메모리를 나눠 쓰므로 스와핑 절벽 완화 (docs/01 §5)."""
    import torch

    if torch.backends.mps.is_available():
        torch.mps.empty_cache()

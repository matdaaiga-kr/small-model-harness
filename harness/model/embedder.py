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
    trace.log("retrieve", op="embed", n_texts=len(texts), latency_ms={"total": t.ms})
    return vectors.tolist()

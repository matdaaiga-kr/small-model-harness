"""Query 파이프라인 — 검색 → 예산 내 조립 → 인용 강제 단발 생성 (docs/02 §4-②).

LLM 호출은 정확히 1회. 실패해도 재시도 없음 — 파이프라인 모드는 단순함이 무기다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness import config
from harness.ingest.chunker import estimate_tokens
from harness.model.client import ModelClient
from harness.obs import trace
from harness.query import retriever

SYSTEM_PROMPT = """당신은 개인 지식 위키 전용 어시스턴트다. 규칙:

1. 아래 제공되는 위키 발췌만 근거로 답한다. 발췌에 없는 내용은 지어내지 말고 "위키에서 찾지 못했다"고 말한다.
2. 근거로 쓴 발췌의 출처 페이지를 이중 대괄호로 인용한다. 예: "...를 구현했다 [[정글-malloc]]". 발췌의 출처에 없는 페이지명을 만들어내지 않는다.
3. 한국어로, 간결하게 답한다."""


@dataclass
class QueryResult:
    answer: str
    hits: list[retriever.Hit]
    used_chunks: list[str]      # 컨텍스트에 실제로 들어간 청크 id
    context_tokens: int
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


def assemble(hits: list[retriever.Hit], budget: int = config.PREFILL_BUDGET) -> tuple[str, list[str], int]:
    """점수순으로 예산 내 채움. 반환: (컨텍스트 텍스트, 사용 청크 id, 토큰 수)."""
    parts: list[str] = []
    used: list[str] = []
    total = 0
    for h in sorted(hits, key=lambda h: -h.score):
        block = f"<발췌 출처=\"[[{h.page}]]\" 섹션=\"{h.heading_path or '-'}\">\n{h.text}\n</발췌>"
        cost = estimate_tokens(block)
        if total + cost > budget:
            continue  # 큰 청크는 건너뛰고 작은 다음 청크는 시도
        parts.append(block)
        used.append(h.chunk_id)
        total += cost
    context = "\n\n".join(parts)
    trace.log(
        "assemble",
        op="context",
        context={"budget": budget, "used": total},
        n_chunks=len(used),
    )
    return context, used, total


def ask(question: str, *, client: ModelClient | None = None) -> QueryResult:
    with trace.timer() as t:
        hits = retriever.search(question)
        if not hits:
            return QueryResult(
                answer="위키에서 관련 내용을 찾지 못했다.",
                hits=[], used_chunks=[], context_tokens=0, latency_ms=t.ms,
            )
        context, used, ctx_tokens = assemble(hits)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"위키 발췌:\n\n{context}\n\n질문: {question}",
            },
        ]
        r = (client or ModelClient()).chat(messages, max_tokens=400)
    return QueryResult(
        answer=r.content.strip(),
        hits=hits,
        used_chunks=used,
        context_tokens=ctx_tokens,
        latency_ms=t.ms,
        prompt_tokens=r.prompt_tokens,
        completion_tokens=r.completion_tokens,
    )

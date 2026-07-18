"""HybridRetriever — 벡터 0.6 + BM25 0.4 융합 + 위키링크 1-hop 확장 (docs/02 §4-②).

검색·융합·확장은 전부 결정적 코드다. LLM은 여기 없다.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from functools import lru_cache

import lancedb
from rank_bm25 import BM25Okapi

from harness import config, text
from harness.ingest.indexer import BM25_PATH, GRAPH_PATH, LANCE_DIR, TABLE
from harness.obs import trace

VEC_WEIGHT = 0.6
BM25_WEIGHT = 0.4
TOP_K = 12
VEC_CANDIDATES = 30      # 융합 전 벡터 후보 폭
GRAPH_EXPAND_CAP = 3     # 1-hop 확장으로 추가하는 페이지 상한
MIN_SCORE = 0.30         # 융합 점수 컷 (min-max 정규화 후)


@dataclass
class Hit:
    chunk_id: str
    page: str
    heading_path: str
    text: str
    score: float           # 융합 점수 (0~1, min-max 정규화 후 가중합)
    source: str            # "hybrid" | "graph"


@lru_cache(maxsize=1)
def _table():
    return lancedb.connect(LANCE_DIR).open_table(TABLE)


@lru_cache(maxsize=1)
def _bm25() -> tuple[BM25Okapi, list[str]]:
    with BM25_PATH.open("rb") as f:
        d = pickle.load(f)
    return BM25Okapi(d["corpus_tokens"]), d["chunk_ids"]


@lru_cache(maxsize=1)
def _graph() -> dict[str, list[str]]:
    return json.loads(GRAPH_PATH.read_text())


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def search(query: str, *, k: int = TOP_K, expand: bool = True) -> list[Hit]:
    from harness.model import embedder

    with trace.timer() as t:
        # 1) 벡터 후보: cosine 유사도
        qvec = embedder.embed([query])[0]
        vec_rows = (
            _table()
            .search(qvec)
            .metric("cosine")
            .limit(VEC_CANDIDATES)
            .to_list()
        )
        vec_scores = {r["id"]: 1.0 - r["_distance"] for r in vec_rows}
        rows_by_id = {r["id"]: r for r in vec_rows}

        # 2) BM25: kiwi 형태소 토큰으로 전 청크 점수
        bm25, chunk_ids = _bm25()
        raw = bm25.get_scores(text.tokenize(query))
        # 벡터 후보와 융합할 상위 후보만 남긴다
        bm_top = sorted(zip(chunk_ids, raw), key=lambda x: -x[1])[:VEC_CANDIDATES]
        bm_scores = {cid: s for cid, s in bm_top if s > 0}

        # 3) 융합: 후보 합집합에서 0.6*vec + 0.4*bm25 (각각 min-max 정규화, 결측 0)
        nv, nb = _minmax(vec_scores), _minmax(bm_scores)
        fused = {
            cid: VEC_WEIGHT * nv.get(cid, 0.0) + BM25_WEIGHT * nb.get(cid, 0.0)
            for cid in set(nv) | set(nb)
        }

        missing = [cid for cid in fused if cid not in rows_by_id]
        for row in _fetch_rows(missing):
            rows_by_id[row["id"]] = row

        ranked = sorted(fused.items(), key=lambda x: -x[1])
        hits = [
            _to_hit(rows_by_id[cid], score, "hybrid")
            for cid, score in ranked[:k]
            if score >= MIN_SCORE and cid in rows_by_id
        ]

        # 4) 그래프 확장: 상위 페이지가 링크한 페이지의 대표(첫) 청크 추가
        if expand and hits:
            hits.extend(_expand(hits))

    trace.log(
        "retrieve",
        op="hybrid_search",
        k=k,
        n_candidates=len(fused),
        n_hits=len(hits),
        latency_ms={"total": t.ms},
        hits=[h.chunk_id for h in hits[:8]],
    )
    return hits


def _fetch_rows(chunk_ids: list[str]) -> list[dict]:
    if not chunk_ids:
        return []
    quoted = ", ".join("'" + cid.replace("'", "''") + "'" for cid in chunk_ids)
    return _table().search().where(f"id IN ({quoted})").limit(len(chunk_ids)).to_list()


def _to_hit(row: dict, score: float, source: str) -> Hit:
    return Hit(
        chunk_id=row["id"],
        page=row["page"],
        heading_path=row["heading_path"],
        text=row["text"],
        score=round(score, 4),
        source=source,
    )


def _expand(hits: list[Hit]) -> list[Hit]:
    """상위 히트 페이지들의 [[링크]] 대상 페이지에서 대표 청크를 가져온다 (1-hop, 상한 3)."""
    graph = _graph()
    have_pages = {h.page for h in hits}
    targets: list[str] = []
    for h in hits[:5]:
        for linked in graph.get(h.page, []):
            if linked not in have_pages and linked not in targets and linked in graph:
                targets.append(linked)
    targets = targets[:GRAPH_EXPAND_CAP]
    if not targets:
        return []

    extra: list[Hit] = []
    floor = min(h.score for h in hits)
    for page in targets:
        escaped = page.replace("'", "''")
        rows = _table().search().where(f"page = '{escaped}'").limit(1).to_list()
        if rows:
            # 확장 청크는 본 검색 최저점 바로 아래 점수 — 조립 시 밀리도록
            extra.append(_to_hit(rows[0], max(floor - 0.01, 0.0), "graph"))
    return extra

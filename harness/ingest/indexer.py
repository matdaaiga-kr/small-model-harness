"""인덱서 — 증분 색인: 파일 해시 비교 → 변경분만 재임베딩 (docs/02 §4-①).

산출물 (전부 data/ 아래, 위키에는 절대 쓰지 않는다):
- LanceDB 테이블 `chunks`  : 벡터 + 청크 메타
- bm25.pkl                 : {chunk_ids, corpus_tokens} — 질의 시 BM25Okapi 재구성
- link-graph.json          : 페이지 인접 리스트 (1-hop 그래프 확장용)
- ingest-state.json        : {상대경로: sha256} — 증분 판단 기준
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import lancedb

from harness import config, text
from harness.ingest.chunker import Chunk, chunk_page
from harness.ingest.loader import iter_pages
from harness.obs import trace

STATE_PATH = config.DATA_DIR / "ingest-state.json"
BM25_PATH = config.DATA_DIR / "bm25.pkl"
GRAPH_PATH = config.DATA_DIR / "link-graph.json"
LANCE_DIR = config.DATA_DIR / "lancedb"
TABLE = "chunks"


@dataclass
class IngestReport:
    total_pages: int
    changed_pages: int
    deleted_pages: int
    new_chunks: int
    total_chunks: int


def _load_state() -> dict[str, str]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _chunk_rows(chunks: list[Chunk], vectors: list[list[float]]) -> list[dict]:
    return [
        {
            "id": c.chunk_id,
            "page": c.page,
            "path": c.path,
            "heading_path": c.heading_path,
            "text": c.text,
            "links": json.dumps(c.links, ensure_ascii=False),
            "vector": v,
        }
        for c, v in zip(chunks, vectors)
    ]


def ingest(wiki_root: Path = config.WIKI_ROOT, *, full: bool = False) -> IngestReport:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    prev_state = {} if full else _load_state()

    pages = list(iter_pages(wiki_root))
    new_state = {str(p.path): p.sha256 for p in pages}

    changed = [p for p in pages if prev_state.get(str(p.path)) != p.sha256]
    deleted_paths = set(prev_state) - set(new_state)

    db = lancedb.connect(LANCE_DIR)
    if full and TABLE in db.table_names():
        # 전체 재색인은 테이블을 새로 만든다 — 상태 파일 밖의 유령 행까지 확실히 제거
        db.drop_table(TABLE)
    table_exists = TABLE in db.table_names()

    # 1) 변경 페이지 청킹 + 임베딩 (임베딩은 여기서만 일어난다 — 증분의 핵심)
    new_chunks: list[Chunk] = []
    for p in changed:
        new_chunks.extend(chunk_page(p))

    if new_chunks:
        from harness.model import embedder  # KURE 로드는 실제 필요할 때만

        with trace.timer() as t:
            vectors = embedder.embed([c.text for c in new_chunks], batch_size=32)
        trace.log(
            "assemble", op="embed_chunks", n=len(new_chunks), latency_ms={"total": t.ms}
        )
        rows = _chunk_rows(new_chunks, vectors)

        if table_exists:
            tbl = db.open_table(TABLE)
            stale = {str(p.path) for p in changed} | deleted_paths
            _delete_paths(tbl, stale)
            tbl.add(rows)
        else:
            db.create_table(TABLE, rows)
            tbl = db.open_table(TABLE)
    elif table_exists:
        tbl = db.open_table(TABLE)
        if deleted_paths:
            _delete_paths(tbl, deleted_paths)
    else:
        # 위키가 비어 있고 테이블도 없음 — 색인할 것이 없다
        _save_artifacts(pages, [], [])
        STATE_PATH.write_text(json.dumps(new_state, ensure_ascii=False, indent=1))
        return IngestReport(len(pages), 0, len(deleted_paths), 0, 0)

    # 2) BM25·그래프는 전체 재생성 — 수백 페이지 규모에선 수 초라 증분화가 오히려 복잡도만 키운다
    all_rows = tbl.search().limit(1_000_000).to_list()
    corpus_tokens = [text.tokenize(r["text"]) for r in all_rows]
    _save_artifacts(pages, [r["id"] for r in all_rows], corpus_tokens)

    STATE_PATH.write_text(json.dumps(new_state, ensure_ascii=False, indent=1))
    return IngestReport(
        total_pages=len(pages),
        changed_pages=len(changed),
        deleted_pages=len(deleted_paths),
        new_chunks=len(new_chunks),
        total_chunks=len(all_rows),
    )


def _delete_paths(tbl, paths: set[str]) -> None:
    for path in paths:
        escaped = path.replace("'", "''")
        tbl.delete(f"path = '{escaped}'")


def _save_artifacts(pages, chunk_ids: list[str], corpus_tokens: list[list[str]]) -> None:
    with BM25_PATH.open("wb") as f:
        pickle.dump({"chunk_ids": chunk_ids, "corpus_tokens": corpus_tokens}, f)
    graph = {p.name: p.links for p in pages}
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=1))

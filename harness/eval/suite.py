"""태스크 스위트 로더 + 검색 평가 (L3 메트릭: hit@k, MRR) — docs/03.

M2 완료 기준 측정: hit@5 ≥ 0.8. LLM 없이 검색 계층만 평가하므로 빠르다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel

from harness import config


class Expect(BaseModel):
    pages: list[str] = []
    # 멀티홉 채점: 그룹마다 동의 페이지 목록 — 각 그룹에서 최소 1개가 답변에 인용돼야 성공.
    # (lab-notes/004: 단일 정답 페이지 강제는 동의 페이지를 오답 처리했다)
    page_groups: list[list[str]] = []
    must_cite: bool = True
    require_all: bool = False   # page_groups가 없을 때만 사용 (레거시)
    rubric: str = ""


class Task(BaseModel):
    id: str
    question: str
    expect: Expect
    # qa: 정답 페이지 채점 / probe: 하네스 압박 태스크 — 환각 툴콜 없음이 성공 기준
    kind: str = "qa"


def load_suite(path: Path | None = None) -> list[Task]:
    p = path or (config.PROJECT_ROOT / "tasks" / "suite.yaml")
    return [Task.model_validate(item) for item in yaml.safe_load(p.read_text())]


@dataclass
class RetrievalResult:
    task_id: str
    hit_rank: int | None      # 정답 페이지가 처음 등장한 순위 (1-base, 페이지 단위)
    top_pages: list[str]


@dataclass
class RetrievalReport:
    results: list[RetrievalResult]
    k: int

    @property
    def hit_at_k(self) -> float:
        n = len(self.results)
        return sum(1 for r in self.results if r.hit_rank and r.hit_rank <= self.k) / n

    @property
    def mrr(self) -> float:
        n = len(self.results)
        return sum(1 / r.hit_rank for r in self.results if r.hit_rank) / n


def eval_retrieval(tasks: list[Task], *, k: int = 5, expand: bool = True) -> RetrievalReport:
    from harness.query import retriever

    results = []
    tasks = [t for t in tasks if t.kind == "qa" and t.expect.pages]
    for task in tasks:
        hits = retriever.search(task.question, expand=expand)
        # 청크 순위 → 페이지 순위 (첫 등장 기준)
        pages: list[str] = []
        for h in hits:
            if h.page not in pages:
                pages.append(h.page)
        rank = next(
            (i + 1 for i, p in enumerate(pages) if p in task.expect.pages), None
        )
        results.append(RetrievalResult(task.id, rank, pages[:k]))
    return RetrievalReport(results, k)

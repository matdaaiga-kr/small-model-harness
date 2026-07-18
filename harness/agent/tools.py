"""위키 툴 3개 — 전부 읽기 전용 (docs/02 §4-③).

툴 정의는 개당 50~150토큰 예산 (docs/01 §7-5). 결과 문자열은 ContextBudget이
루프 레벨에서 절단하므로 여기서는 자연스러운 크기로 반환한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from harness import config
from harness.agent.registry import ToolRegistry
from harness.ingest.indexer import GRAPH_PATH
from harness.ingest.loader import EXCLUDED_DIRS


class SearchArgs(BaseModel):
    query: str = Field(description="한국어 자연어 검색어")


class ReadPageArgs(BaseModel):
    name: str = Field(description="페이지명 (확장자 없이, 예: 정글-pintos)")


class ListPagesArgs(BaseModel):
    keyword: str = Field(default="", description="페이지명에 포함될 키워드 (빈 값이면 전체)")


@lru_cache(maxsize=1)
def _page_files() -> dict[str, Path]:
    """페이지명 → 파일 경로. 링크 그래프와 동일한 제외 규칙."""
    files: dict[str, Path] = {}
    for p in sorted(config.WIKI_ROOT.rglob("*.md")):
        parts = p.relative_to(config.WIKI_ROOT).parts
        if any(x.startswith((".", "_")) or x.lower() in EXCLUDED_DIRS for x in parts[:-1]):
            continue
        files[p.stem] = p
    return files


def search_wiki(query: str) -> str:
    from harness.query import retriever

    hits = retriever.search(query, k=5, expand=False)
    if not hits:
        return "검색 결과 없음."
    lines = [
        f"[{h.score:.2f}] {h.page} — {h.heading_path or '(서두)'}\n{h.text[:200]}"
        for h in hits
    ]
    return "\n\n".join(lines)


def read_page(name: str) -> str:
    files = _page_files()
    if name not in files:
        close = [p for p in files if name.lower() in p.lower()][:5]
        hint = f" 비슷한 페이지: {', '.join(close)}" if close else ""
        return f"오류: '{name}' 페이지가 없다.{hint}"
    return files[name].read_text(encoding="utf-8")


def list_pages(keyword: str = "") -> str:
    graph = json.loads(GRAPH_PATH.read_text())
    names = sorted(n for n in graph if keyword.lower() in n.lower())
    if not names:
        return f"'{keyword}'를 포함하는 페이지 없음."
    return "\n".join(names[:50])


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        "search_wiki",
        "위키에서 관련 내용을 하이브리드 검색한다. 어느 페이지에 있는지 모를 때 먼저 쓴다.",
        SearchArgs,
        search_wiki,
    )
    reg.register(
        "read_page",
        "페이지 전문을 읽는다. search_wiki로 페이지를 찾은 뒤 자세한 내용이 필요할 때 쓴다.",
        ReadPageArgs,
        read_page,
    )
    reg.register(
        "list_pages",
        "페이지명 목록을 조회한다. 키워드로 필터 가능.",
        ListPagesArgs,
        list_pages,
    )
    return reg

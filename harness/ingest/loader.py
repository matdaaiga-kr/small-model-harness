"""위키 로더 — 마크다운 + frontmatter 파싱 (docs/02 §4-①).

위키는 읽기 전용 데이터 소스다. 어떤 경우에도 위키 경로에 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import frontmatter

# .obsidian, .trash 등 숨김 폴더와 템플릿은 색인 대상이 아니다
EXCLUDED_DIRS = {".obsidian", ".trash", ".git", "templates", "template"}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


@dataclass
class Page:
    path: Path              # 위키 루트 기준 상대 경로
    name: str               # 링크 대상이 되는 페이지명 (확장자 없는 파일명)
    meta: dict[str, Any]    # frontmatter
    body: str               # frontmatter 제거된 본문
    sha256: str
    links: list[str] = field(default_factory=list)  # 본문의 [[위키링크]] 대상들


def extract_links(text: str) -> list[str]:
    """[[페이지명]] / [[페이지명#헤딩]] / [[페이지명|별칭]] → 페이지명 목록 (중복 제거, 순서 유지)."""
    seen: dict[str, None] = {}
    for m in WIKILINK_RE.finditer(text):
        seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def load_page(root: Path, md_path: Path) -> Page:
    raw = md_path.read_bytes()
    post = frontmatter.loads(raw.decode("utf-8"))
    body = post.content
    return Page(
        path=md_path.relative_to(root),
        name=md_path.stem,
        meta=dict(post.metadata),
        body=body,
        sha256=hashlib.sha256(raw).hexdigest(),
        links=extract_links(body),
    )


def iter_pages(root: Path) -> Iterator[Page]:
    if not root.is_dir():
        raise FileNotFoundError(f"위키 루트가 없습니다: {root}")
    for md_path in sorted(root.rglob("*.md")):
        rel_parts = md_path.relative_to(root).parts
        if any(
            p.startswith((".", "_")) or p.lower() in EXCLUDED_DIRS
            for p in rel_parts[:-1]
        ):
            continue
        yield load_page(root, md_path)

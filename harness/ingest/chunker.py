"""청커 — 헤딩(H2/H3) 단위 1청크, 300~400토큰 초과 시 분할 + 15% 오버랩 (docs/01 §7, docs/02 §4-①).

토큰 수는 근사치를 쓴다: 한국어 마크다운은 대략 2문자 ≈ 1토큰 (KURE의 XLM-R,
qwen3 모두 이 범위). 청크 예산은 검색 품질용 휴리스틱이라 ±10% 오차는 무해하고,
정확한 토크나이저를 여기 끌어오면 ingest가 모델 로드에 종속된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from harness.ingest.loader import Page, extract_links

MAX_TOKENS = 400
OVERLAP_RATIO = 0.15

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


@dataclass
class Chunk:
    chunk_id: str          # "<페이지명>#<헤딩 경로>[/n]"
    page: str              # 페이지명 (인용 [[페이지명]]의 대상)
    path: str              # 위키 루트 기준 상대 경로
    heading_path: str      # "개요 > 세부" 형태
    text: str
    links: list[str]


def _split_by_headings(body: str) -> list[tuple[str, str]]:
    """본문 → [(헤딩 경로, 섹션 텍스트)]. H2/H3만 경계로 쓴다."""
    # H1은 문서 제목(대개 파일명과 동일)이라 청크 내용에서 제거
    body = re.sub(r"^#\s+.*$", "", body, flags=re.MULTILINE)
    sections: list[tuple[str, str]] = []
    trail: dict[int, str] = {}  # 레벨 → 현재 헤딩
    pos = 0
    current_path = ""

    matches = [m for m in HEADING_RE.finditer(body) if len(m.group(1)) in (2, 3)]
    for m in matches:
        text = body[pos : m.start()].strip()
        if text:
            sections.append((current_path, text))
        level = len(m.group(1))
        trail[level] = m.group(2).strip()
        trail = {k: v for k, v in trail.items() if k <= level}
        current_path = " > ".join(trail[k] for k in sorted(trail))
        pos = m.end()

    tail = body[pos:].strip()
    if tail:
        sections.append((current_path, tail))
    return sections


def _split_long(text: str) -> list[str]:
    """MAX_TOKENS 초과 섹션을 문단 경계 우선으로 분할, 조각 간 15% 오버랩."""
    if estimate_tokens(text) <= MAX_TOKENS:
        return [text]

    paragraphs = re.split(r"\n{2,}", text)
    pieces: list[str] = []
    buf: list[str] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            pieces.append("\n\n".join(buf))
            buf, buf_tokens = [], 0

    for para in paragraphs:
        p_tokens = estimate_tokens(para)
        if buf and buf_tokens + p_tokens > MAX_TOKENS:
            flush()
        # 문단 하나가 예산을 넘으면 문자 기준 강제 분할
        while p_tokens > MAX_TOKENS:
            cut = MAX_TOKENS * 2  # 토큰→문자 환산
            pieces.append(para[:cut])
            overlap = int(cut * OVERLAP_RATIO)
            para = para[cut - overlap :]
            p_tokens = estimate_tokens(para)
        buf.append(para)
        buf_tokens += p_tokens
    flush()

    # 조각 간 오버랩: 앞 조각의 꼬리를 뒤 조각 머리에 붙인다
    overlapped = [pieces[0]]
    for prev, cur in zip(pieces, pieces[1:]):
        tail_chars = int(len(prev) * OVERLAP_RATIO)
        overlapped.append(prev[-tail_chars:].lstrip() + "\n" + cur if tail_chars else cur)
    return overlapped


def chunk_page(page: Page) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading_path, section in _split_by_headings(page.body):
        pieces = _split_long(section)
        for i, piece in enumerate(pieces):
            suffix = f"/{i}" if len(pieces) > 1 else ""
            head = heading_path or "_intro"
            # 헤딩 경로를 텍스트에 포함 — 임베딩·BM25 모두 문맥 신호로 쓴다
            text = f"[{page.name}] {heading_path}\n{piece}" if heading_path else f"[{page.name}]\n{piece}"
            chunks.append(
                Chunk(
                    chunk_id=f"{page.name}#{head}{suffix}",
                    page=page.name,
                    path=str(page.path),
                    heading_path=heading_path,
                    text=text,
                    links=extract_links(piece),
                )
            )
    return chunks

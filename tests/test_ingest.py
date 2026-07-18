"""M1 ingest 단위 테스트 — 로더/청커/그래프는 모델 없이 돈다."""

from pathlib import Path

from harness.ingest.chunker import MAX_TOKENS, chunk_page, estimate_tokens
from harness.ingest.loader import extract_links, iter_pages, load_page

FIXTURE_WIKI = Path(__file__).parent / "fixtures" / "wiki"


def test_iter_pages_excludes_hidden_dirs():
    names = {p.name for p in iter_pages(FIXTURE_WIKI)}
    assert names == {"정글-pintos", "가상-메모리", "동기화-기법"}


def test_frontmatter_and_links():
    page = load_page(FIXTURE_WIKI, FIXTURE_WIKI / "정글-pintos.md")
    assert page.meta["tags"] == ["정글", "os"]
    assert "---" not in page.body
    # [[별칭|...]], [[페이지#헤딩]] 모두 페이지명으로 정규화, 중복 제거
    assert page.links == ["가상-메모리", "동기화-기법"]


def test_extract_links_variants():
    text = "[[A]] [[B|별명]] [[C#섹션]] [[A]]"
    assert extract_links(text) == ["A", "B", "C"]


def test_chunk_by_headings():
    page = load_page(FIXTURE_WIKI, FIXTURE_WIKI / "정글-pintos.md")
    chunks = chunk_page(page)
    paths = [c.heading_path for c in chunks]
    assert paths == ["개요", "구현 내용 > 스레드", "구현 내용 > 가상 메모리"]
    # 청크 텍스트에 페이지명·헤딩 경로가 문맥으로 들어간다
    assert chunks[1].text.startswith("[정글-pintos] 구현 내용 > 스레드")
    # 청크 단위 링크 추출
    assert chunks[0].links == ["가상-메모리"]
    assert chunks[1].links == ["동기화-기법"]


def test_long_section_splits_with_overlap():
    from harness.ingest.chunker import Chunk
    from harness.ingest.loader import Page

    para = "문단입니다. " * 60  # ~360자 ≈ 180토큰
    body = "## 긴 섹션\n\n" + "\n\n".join([para] * 5)  # ~900토큰
    page = Page(
        path=Path("긴문서.md"), name="긴문서", meta={}, body=body, sha256="x", links=[]
    )
    chunks = chunk_page(page)
    assert len(chunks) >= 2
    assert all(estimate_tokens(c.text) <= MAX_TOKENS * 1.4 for c in chunks)  # 오버랩+헤더 여유
    assert [c.chunk_id for c in chunks] == [
        f"긴문서#긴 섹션/{i}" for i in range(len(chunks))
    ]
    # 오버랩: 앞 조각 꼬리가 뒤 조각 머리에 포함
    assert chunks[1].text.split("\n")[1].strip() != ""


def test_heading_only_page():
    page = load_page(FIXTURE_WIKI, FIXTURE_WIKI / "가상-메모리.md")
    chunks = chunk_page(page)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "가상-메모리#페이지 폴트"

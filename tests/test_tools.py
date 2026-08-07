"""위키 툴 테스트 — fixture 위키로 read_page/list_pages를 모델 없이 검증.

search_wiki는 임베더·색인이 필요해 여기서 다루지 않는다 (M2 평가 하네스 소관).
"""

import json
from pathlib import Path

import pytest

from harness import config
from harness.agent import tools

FIXTURE_WIKI = Path(__file__).parent / "fixtures" / "wiki"


@pytest.fixture
def fixture_wiki(monkeypatch):
    monkeypatch.setattr(config, "WIKI_ROOT", FIXTURE_WIKI)
    tools._page_files.cache_clear()
    yield
    tools._page_files.cache_clear()


def test_page_files_excludes_hidden_dirs(fixture_wiki):
    assert set(tools._page_files()) == {"정글-pintos", "가상-메모리", "동기화-기법"}


def test_read_page_returns_content(fixture_wiki):
    out = tools.read_page("정글-pintos")
    assert not out.startswith("오류")
    assert "pintos" in out or "정글" in out


def test_read_page_casefold_lookup(fixture_wiki):
    # 모델은 페이지명 표기를 자주 바꾼다 (lab-notes/004)
    assert tools.read_page("정글-PINTOS") == tools.read_page("정글-pintos")


def test_read_page_missing_suggests_close_names(fixture_wiki):
    out = tools.read_page("pintos")
    assert out.startswith("오류") and "정글-pintos" in out


def test_read_page_missing_without_match(fixture_wiki):
    out = tools.read_page("전혀-없는-페이지")
    assert out.startswith("오류") and "비슷한 페이지" not in out


@pytest.fixture
def fixture_graph(monkeypatch, tmp_path):
    graph = tmp_path / "link-graph.json"
    graph.write_text(
        json.dumps({"정글-pintos": [], "정글-malloc": [], "도커-빌드": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tools, "GRAPH_PATH", graph)


def test_list_pages_filters_by_keyword(fixture_graph):
    out = tools.list_pages("정글")
    assert "정글-pintos" in out and "정글-malloc" in out
    assert "도커-빌드" not in out


def test_list_pages_empty_keyword_lists_all(fixture_graph):
    out = tools.list_pages("")
    assert {"정글-pintos", "정글-malloc", "도커-빌드"} <= set(out.splitlines())


def test_list_pages_no_match_message(fixture_graph):
    assert "페이지 없음" in tools.list_pages("쿠버네티스")

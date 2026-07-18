"""BM25 토크나이저 테스트 — 형태소 기반, 색인/질의 공용."""

from harness.text import tokenize


def test_tokenize_korean_content_words():
    tokens = tokenize("크래프톤 정글에서 pintos 프로젝트는 어떤 내용이었지?")
    assert "정글" in tokens
    assert "pintos" in tokens
    assert "프로젝트" in tokens
    # 조사·어미는 걸러진다
    assert "에서" not in tokens
    assert "는" not in tokens


def test_tokenize_fallback_never_empty():
    assert tokenize("ㅋㅋㅋ") != []

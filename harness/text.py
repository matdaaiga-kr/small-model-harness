"""한국어 텍스트 처리 공용 모듈 — BM25용 형태소 토크나이저 (docs/01 §7).

색인(ingest)과 질의(query)가 반드시 같은 토크나이저를 써야 하므로 한 곳에 둔다.
"""

from __future__ import annotations

import re
from functools import lru_cache

# 내용어 품사만 남긴다: 명사류, 동사/형용사 어근, 외국어(영단어), 숫자
_CONTENT_TAGS = ("NNG", "NNP", "NNB", "NR", "VV", "VA", "XR", "SL", "SN")

_WORD_RE = re.compile(r"[A-Za-z0-9가-힣ㄱ-ㅎㅏ-ㅣ]+")


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


def tokenize(text: str) -> list[str]:
    """BM25용 토큰화: kiwi 형태소 분석 → 내용어 형태소만, 소문자."""
    tokens = [
        t.form.lower()
        for t in _kiwi().tokenize(text)
        if t.tag.startswith(_CONTENT_TAGS)
    ]
    # 형태소가 하나도 안 나오는 극단 입력 대비 폴백
    return tokens or [w.lower() for w in _WORD_RE.findall(text)]

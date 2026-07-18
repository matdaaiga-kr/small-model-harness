"""프로젝트 전역 설정.

모든 경로·모델명·예산 상수를 한 곳에. 환경변수 HARNESS_* 로 덮어쓸 수 있다.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 데이터 소스 (읽기 전용 — 위키에는 절대 쓰지 않는다) ──────────────────
WIKI_ROOT = Path(
    os.environ.get("HARNESS_WIKI_ROOT", "~/Desktop/portfolio/obsidian-wiki")
).expanduser()

# ── 산출물 저장 위치 (전부 프로젝트 안) ──────────────────────────────────
DATA_DIR = PROJECT_ROOT / "data"          # LanceDB, BM25 pickle, 링크 그래프
TRACES_DIR = PROJECT_ROOT / "traces" / "runs"
METRICS_DB = PROJECT_ROOT / "metrics.sqlite"

# ── 모델 계층 ────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("HARNESS_BASE_URL", "http://localhost:11434/v1")
CHAT_MODEL = os.environ.get("HARNESS_CHAT_MODEL", "qwen3:8b")
EMBED_MODEL_ID = "nlpai-lab/KURE-v1"  # sentence-transformers, 인프로세스(MPS)

# ── 예산 (16GB 제약에서 나온 숫자들 — docs/02 §5, docs/01 §5) ───────────
CONTEXT_WINDOW = 8192          # 8K가 현실적 상한
# 컨텍스트 조립 상한(토큰). 실측 프리필 ~65 tok/s (2026-07-18, 3K 프롬프트 44s)라
# 30초 응답 목표를 지키려면 1500이 상한 — 문서의 "예: 3K"는 낙관치였다.
PREFILL_BUDGET = 1500
DEFAULT_TEMPERATURE = 0.2

# ── 에이전트 루프 가드레일 기본값 ────────────────────────────────────────
MAX_STEPS = 6                  # 복리 실패 대응: 스텝 수 자체를 제한
MAX_RETRIES = 2                # 검증 실패 시 수리 재시도 상한

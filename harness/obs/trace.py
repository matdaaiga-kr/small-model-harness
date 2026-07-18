"""TraceLogger — 모든 LLM 호출·툴 실행·검색을 예외 없이 JSONL로 남긴다 (docs/03).

- 런 하나 = traces/runs/<run_id>.jsonl 파일 하나.
- contextvars로 run_id를 전파해 어떤 모듈에서든 무인자 기록.
- 샘플링 없음 — 로컬이라 비용이 0이다.
"""

from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import psutil

from harness import config

_current_run: ContextVar["TraceLogger | None"] = ContextVar("current_run", default=None)


def _mem_snapshot() -> dict[str, Any]:
    rss_mb = psutil.Process().memory_info().rss // (1024 * 1024)
    avail_mb = psutil.virtual_memory().available // (1024 * 1024)
    return {"rss_mb": rss_mb, "avail_mb": avail_mb}


class TraceLogger:
    """한 런(run)의 스텝 레코드를 JSONL로 기록한다."""

    def __init__(self, mode: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        self.run_id = f"r_{ts}_{uuid.uuid4().hex[:4]}"
        self.mode = mode
        self.step = 0
        config.TRACES_DIR.mkdir(parents=True, exist_ok=True)
        self.path = config.TRACES_DIR / f"{self.run_id}.jsonl"
        self._token = _current_run.set(self)

    def log(self, phase: str, **fields: Any) -> None:
        self.step += 1
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "mode": self.mode,
            "step": self.step,
            "phase": phase,  # retrieve | assemble | llm | tool_call
            "mem": _mem_snapshot(),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        _current_run.reset(self._token)


def current() -> TraceLogger | None:
    return _current_run.get()


def log(phase: str, **fields: Any) -> None:
    """현재 런이 있으면 기록, 없으면 조용히 무시 — 계측이 로직을 방해하지 않는다."""
    run = current()
    if run is not None:
        run.log(phase, **fields)


class timer:
    """`with timer() as t: ...` 후 t.ms 로 경과 시간(ms)을 읽는다."""

    def __enter__(self) -> "timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)

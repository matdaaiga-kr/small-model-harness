"""Validator — 형식 보장 ≠ 의미 보장 (docs/01 §7-2).

런타임(Ollama)이 유효한 JSON을 줬더라도 믿지 않는다:
함수명은 레지스트리와 대조하고, 인자는 pydantic으로 검증한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from harness.agent.registry import ToolRegistry


@dataclass
class Verdict:
    valid_name: bool
    valid_args: bool
    args: dict[str, Any] | None    # 검증 통과 시 파싱된 인자
    error: str | None              # 모델에게 되먹일 수리용 오류 메시지

    @property
    def ok(self) -> bool:
        return self.valid_name and self.valid_args


def parse_args(raw_arguments: str | dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw_arguments, dict):
        return raw_arguments, None
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as e:
        return None, f"인자가 유효한 JSON이 아니다: {e}"
    if not isinstance(parsed, dict):
        return None, "인자는 JSON 객체여야 한다."
    return parsed, None


def validate(registry: ToolRegistry, name: str, raw_arguments: str | dict[str, Any]) -> Verdict:
    if name not in registry:
        return Verdict(
            valid_name=False,
            valid_args=False,
            args=None,
            error=f"'{name}'은 존재하지 않는 툴이다. 사용 가능: {', '.join(registry.names())}",
        )

    parsed, parse_err = parse_args(raw_arguments)
    if parse_err:
        return Verdict(True, False, None, parse_err)

    try:
        model = registry.get(name).args_model.model_validate(parsed)
    except ValidationError as e:
        issues = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return Verdict(True, False, None, f"인자 검증 실패 — {issues}")

    return Verdict(True, True, model.model_dump(), None)

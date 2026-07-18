"""ToolRegistry — 이름→pydantic 스키마→함수 매핑, OpenAI tools 스펙 자동 생성 (docs/02 §4-③).

스키마는 얕게 유지한다: 필수 필드 최소, 중첩 없음 (docs/01 §7-3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., str]      # 실행 결과는 항상 문자열 (모델에게 돌려줄 텍스트)

    def spec(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        fn: Callable[..., str],
    ) -> None:
        self._tools[name] = Tool(name, description, args_model, fn)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """레지스트리 대조 없이 실행 (Validator off 경로). 없는 툴이면 KeyError."""
        tool = self._tools[name]
        return tool.fn(**args)

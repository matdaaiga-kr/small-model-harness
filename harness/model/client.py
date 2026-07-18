"""ModelClient — OpenAI 호환 래퍼 (docs/02 §4-③).

- base_url 한 줄로 런타임 교체(Ollama ↔ llama.cpp ↔ 클라우드)가 되도록 이 클래스만 SDK를 안다.
- 재시도는 SDK에 맡기지 않고 하네스(RetryLoop)가 소유한다 → max_retries=0.
- 모든 호출은 TraceLogger에 기록된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from harness import config
from harness.obs import trace


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    tool_calls: list[Any] = field(default_factory=list)


class ModelClient:
    def __init__(
        self,
        base_url: str = config.OLLAMA_BASE_URL,
        model: str = config.CHAT_MODEL,
    ):
        self.model = model
        # api_key는 Ollama가 무시하지만 SDK가 요구한다
        self._client = OpenAI(base_url=base_url, api_key="local", max_retries=0)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = config.DEFAULT_TEMPERATURE,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        think: bool = False,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if not think:
            # qwen3 thinking 비활성 — 켜두면 max_tokens를 reasoning이 다 소모한다.
            # Ollama OpenAI 엔드포인트는 `think`를 무시하고 `reasoning_effort`만 인식(실측).
            kwargs["extra_body"] = {"reasoning_effort": "none"}
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        with trace.timer() as t:
            resp = self._client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        usage = resp.usage
        result = ChatResult(
            content=choice.message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=t.ms,
            tool_calls=list(choice.message.tool_calls or []),
        )
        decode_s = max(t.ms / 1000, 1e-6)
        trace.log(
            "llm",
            model=self.model,
            tokens={"prompt": result.prompt_tokens, "completion": result.completion_tokens},
            latency_ms={"total": t.ms},
            tok_per_s=round(result.completion_tokens / decode_s, 1),
            n_tool_calls=len(result.tool_calls),
        )
        return result

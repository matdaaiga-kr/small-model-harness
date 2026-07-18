"""AgentLoop — 하네스 기법의 실험실 (docs/02 §4-③).

Validator / RetryLoop / Guardrails는 전부 토글형이다: A/B 매트릭스(docs/03)로
"각 계층이 신뢰도를 몇 %p 올리는지"를 측정하는 것이 M3의 목적.
ContextBudget과 스텝 상한은 항상 켠다 — 전자는 8K 물리 제약, 후자는 실험 시간 상한.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from harness import config
from harness.agent.registry import ToolRegistry
from harness.agent.validator import validate
from harness.ingest.chunker import estimate_tokens
from harness.model.client import ModelClient
from harness.obs import trace

AGENT_PREFILL_BUDGET = 3500     # 8K 창에서 툴 정의+생성 여유를 뺀 이력 예산 (토큰 추정치)
TOOL_RESULT_MAX_TOKENS = 600    # 툴 결과 절단 상한

SYSTEM_PROMPT = """당신은 개인 지식 위키를 탐색해 질문에 답하는 에이전트다.

- 필요한 정보를 툴로 찾아라. 충분히 모이면 툴 호출 없이 최종 답을 한국어로 작성하라.
- 최종 답에는 근거 페이지를 [[페이지명]] 형식으로 인용하라.
- 위키에서 찾지 못한 내용은 지어내지 말라."""

GUARDRAIL_PROMPT_LINE = "\n- 실패한 툴 콜을 절대 같은 인자로 반복하지 말라. 실패하면 다른 툴이나 다른 인자를 시도하라."


@dataclass
class HarnessToggles:
    validator: bool = True
    retry: bool = True
    guardrails: bool = True


@dataclass
class AgentResult:
    answer: str
    steps: int = 0
    tool_calls_total: int = 0
    valid_names: int = 0
    valid_args: int = 0
    retries: int = 0            # 수리 요청 횟수
    repaired: int = 0           # 수리로 유효해진 호출 수
    guardrail_blocks: int = 0
    fallback: bool = False      # 스텝 상한/수리 상한으로 강제 종료
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def validity_rate(self) -> float | None:
        if self.tool_calls_total == 0:
            return None
        return sum(
            (self.valid_names, self.valid_args)
        ) / (2 * self.tool_calls_total)


class AgentLoop:
    def __init__(
        self,
        registry: ToolRegistry,
        toggles: HarnessToggles | None = None,
        client: ModelClient | None = None,
    ):
        self.registry = registry
        self.t = toggles or HarnessToggles()
        self.client = client or ModelClient()

    # ── 메인 루프 ────────────────────────────────────────────────────────
    def run(self, task: str) -> AgentResult:
        system = SYSTEM_PROMPT + (GUARDRAIL_PROMPT_LINE if self.t.guardrails else "")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        res = AgentResult(answer="")
        seen_calls: dict[str, str] = {}   # 실행한 (툴+인자) → 결과 요약 (가드레일용)
        failed_calls: set[str] = set()

        for step in range(config.MAX_STEPS):
            self._enforce_budget(messages)
            r = self._chat(messages, res)

            if not r.tool_calls:
                res.answer = r.content.strip()
                res.steps = step + 1
                return res

            messages.append(self._assistant_msg(r))

            for tc in r.tool_calls:
                content = self._handle_call(tc, messages, res, seen_calls, failed_calls)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": content}
                )

        # 스텝 상한 도달 → 폴백: 툴 없이 최종 답 강제
        trace.log("tool_call", guardrail={"step_cap_hit": True})
        res.fallback = True
        res.steps = config.MAX_STEPS
        messages.append(
            {"role": "user", "content": "지금까지 얻은 정보만으로 최종 답을 작성하라."}
        )
        self._enforce_budget(messages)
        r = self._chat(messages, res, tools=False)
        res.answer = r.content.strip()
        return res

    # ── 툴 콜 1건 처리: 검증 → (수리) → 가드레일 → 실행 ─────────────────
    def _handle_call(
        self,
        tc: Any,
        messages: list[dict[str, Any]],
        res: AgentResult,
        seen_calls: dict[str, str],
        failed_calls: set[str],
    ) -> str:
        name = tc.function.name
        raw_args = tc.function.arguments
        verdict = validate(self.registry, name, raw_args)  # 측정은 토글과 무관하게 항상
        res.tool_calls_total += 1
        res.valid_names += verdict.valid_name
        res.valid_args += verdict.valid_args

        retries_used = 0
        if self.t.validator and not verdict.ok:
            if self.t.retry:
                verdict, retries_used = self._repair(tc, verdict, messages, res)
                if not verdict.ok:
                    failed_calls.add(self._call_key(name, raw_args))
                    self._log_step(name, verdict, retries_used)
                    return f"오류: {verdict.error} (수리 {retries_used}회 실패)"
            else:
                failed_calls.add(self._call_key(name, raw_args))
                self._log_step(name, verdict, 0)
                return f"오류: {verdict.error}"

        # Validator off면 원본 인자로 무검증 실행을 시도한다 (베이스라인의 현실)
        exec_name = name if not verdict.ok else name
        exec_args = verdict.args if verdict.ok else None

        key = self._call_key(exec_name, exec_args if exec_args is not None else raw_args)
        if self.t.guardrails:
            if key in failed_calls:
                res.guardrail_blocks += 1
                self._log_step(name, verdict, retries_used, blocked=True)
                return "가드레일: 이미 실패한 호출과 동일하다. 다른 툴이나 다른 인자를 쓰라."
            if key in seen_calls:
                res.guardrail_blocks += 1
                self._log_step(name, verdict, retries_used, blocked=True)
                return f"가드레일: 동일한 호출을 이미 했다. 이전 결과: {seen_calls[key][:200]}"

        try:
            if exec_args is not None:
                output = self.registry.get(exec_name).fn(**exec_args)
            else:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                output = self.registry.execute(exec_name, parsed or {})
        except Exception as e:
            failed_calls.add(key)
            self._log_step(name, verdict, retries_used)
            return f"툴 실행 오류: {type(e).__name__}: {e}"

        output = self._truncate(output)
        seen_calls[key] = output
        self._log_step(name, verdict, retries_used)
        return output

    # ── RetryLoop: 오류를 피드백해 수리 요청, 상한 2회 ───────────────────
    def _repair(self, tc, verdict, messages, res):
        retries = 0
        while not verdict.ok and retries < config.MAX_RETRIES:
            retries += 1
            res.retries += 1
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": f"오류: {verdict.error}"}
            )
            messages.append(
                {
                    "role": "user",
                    "content": "직전 툴 호출이 검증에 실패했다. 오류를 반영해 같은 목적의 호출을 정확한 툴 이름과 인자로 다시 하라.",
                }
            )
            r = self._chat(messages, res)
            if not r.tool_calls:
                break
            tc = r.tool_calls[0]
            messages.append(self._assistant_msg(r))
            verdict = validate(self.registry, tc.function.name, tc.function.arguments)
            res.tool_calls_total += 1
            res.valid_names += verdict.valid_name
            res.valid_args += verdict.valid_args
        if verdict.ok:
            res.repaired += 1
        return verdict, retries

    # ── ContextBudget: 툴 결과 절단 + 오래된 툴 결과 축약 ────────────────
    def _truncate(self, text: str) -> str:
        limit = TOOL_RESULT_MAX_TOKENS * 2  # 토큰→문자 근사
        if len(text) <= limit:
            return text
        return text[:limit] + "\n…(예산 초과로 절단)"

    def _enforce_budget(self, messages: list[dict[str, Any]]) -> None:
        def total() -> int:
            return sum(estimate_tokens(str(m.get("content") or "")) for m in messages)

        used = total()
        if used <= AGENT_PREFILL_BUDGET:
            return
        # 오래된 툴 결과부터 축약 (system/user 과제는 유지)
        for m in messages:
            if m.get("role") == "tool" and len(str(m["content"])) > 100:
                m["content"] = str(m["content"])[:100] + "…(이력 예산 초과로 생략)"
                if total() <= AGENT_PREFILL_BUDGET:
                    break
        trace.log("assemble", op="history_trim", context={"budget": AGENT_PREFILL_BUDGET, "used": total()})

    # ── 헬퍼 ─────────────────────────────────────────────────────────────
    def _chat(self, messages, res: AgentResult, *, tools: bool = True):
        r = self.client.chat(
            messages,
            tools=self.registry.openai_tools() if tools else None,
            temperature=config.DEFAULT_TEMPERATURE,
            max_tokens=500,
        )
        res.prompt_tokens += r.prompt_tokens
        res.completion_tokens += r.completion_tokens
        return r

    @staticmethod
    def _assistant_msg(r) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": r.content or "",
            "tool_calls": [tc.model_dump() for tc in r.tool_calls],
        }

    @staticmethod
    def _call_key(name: str, args: Any) -> str:
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False) if isinstance(args, dict) else args}"

    def _log_step(self, name, verdict, retries, *, blocked: bool = False) -> None:
        trace.log(
            "tool_call",
            tool={
                "name": name,
                "valid_name": verdict.valid_name,
                "valid_args": verdict.valid_args,
                "retries": retries,
            },
            guardrail={"repeat_blocked": blocked},
            toggles=vars(self.t),
        )

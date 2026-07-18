"""Harness Core 단위 테스트 — 가짜 클라이언트로 LLM 없이 루프 메커니즘 검증."""

from dataclasses import dataclass, field
from types import SimpleNamespace

from pydantic import BaseModel, Field

from harness.agent.loop import AgentLoop, HarnessToggles
from harness.agent.registry import ToolRegistry
from harness.agent.validator import validate


class EchoArgs(BaseModel):
    text: str = Field(description="돌려줄 문자열")


def make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("echo", "입력을 돌려준다", EchoArgs, lambda text: f"echo:{text}")
    return reg


def tool_call(name: str, arguments: str, id: str = "tc1"):
    fn = SimpleNamespace(name=name, arguments=arguments)
    tc = SimpleNamespace(id=id, function=fn)
    tc.model_dump = lambda: {
        "id": id, "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return tc


@dataclass
class FakeResult:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 10
    completion_tokens: int = 5
    latency_ms: int = 1


class FakeClient:
    """정해진 응답을 순서대로 돌려준다. 스크립트 소진 후엔 최종 답."""

    def __init__(self, script: list[FakeResult]):
        self.script = list(script)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], **kwargs})
        if self.script:
            return self.script.pop(0)
        return FakeResult(content="최종 답")


# ── Registry / Validator ────────────────────────────────────────────────

def test_openai_spec_shape():
    spec = make_registry().openai_tools()
    assert spec[0]["function"]["name"] == "echo"
    assert "text" in spec[0]["function"]["parameters"]["properties"]


def test_validator_hallucinated_name():
    v = validate(make_registry(), "search_web", '{"q": "x"}')
    assert not v.valid_name and not v.ok
    assert "존재하지 않는 툴" in v.error


def test_validator_bad_args():
    v = validate(make_registry(), "echo", '{"wrong_field": 1}')
    assert v.valid_name and not v.valid_args
    assert "text" in v.error


def test_validator_invalid_json():
    v = validate(make_registry(), "echo", '{"text": ')
    assert v.valid_name and not v.valid_args


def test_validator_ok():
    v = validate(make_registry(), "echo", '{"text": "안녕"}')
    assert v.ok and v.args == {"text": "안녕"}


# ── AgentLoop 메커니즘 ──────────────────────────────────────────────────

def test_loop_happy_path():
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", '{"text": "hi"}')]),
        FakeResult(content="답: hi [[페이지]]"),
    ])
    r = AgentLoop(make_registry(), client=client).run("과제")
    assert r.answer.startswith("답")
    assert r.tool_calls_total == 1 and r.validity_rate == 1.0
    # 툴 결과가 tool 메시지로 전달됐는지
    tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == "echo:hi"


def test_validator_blocks_hallucinated_tool_without_retry():
    toggles = HarnessToggles(validator=True, retry=False, guardrails=False)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("fetch_url", '{"url": "x"}')]),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.valid_names == 0 and r.tool_calls_total == 1
    tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert "존재하지 않는 툴" in tool_msgs[0]["content"]


def test_retry_repairs_bad_args():
    toggles = HarnessToggles(validator=True, retry=True, guardrails=False)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", '{"txt": "x"}')]),      # 잘못된 인자
        FakeResult(tool_calls=[tool_call("echo", '{"text": "x"}', "tc2")]),  # 수리 성공
        FakeResult(content="끝"),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.retries == 1 and r.repaired == 1
    assert r.tool_calls_total == 2  # 원 호출 + 수리 호출 모두 측정


def test_guardrail_blocks_repeated_call():
    toggles = HarnessToggles(validator=True, retry=False, guardrails=True)
    same = '{"text": "x"}'
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", same, "a")]),
        FakeResult(tool_calls=[tool_call("echo", same, "b")]),  # 동일 호출 반복
        FakeResult(content="끝"),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.guardrail_blocks == 1


def test_baseline_executes_unknown_tool_as_runtime_error():
    toggles = HarnessToggles(validator=False, retry=False, guardrails=False)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("fetch_url", '{"url": "x"}')]),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    # 측정은 그대로: 환각 이름으로 기록
    assert r.valid_names == 0
    tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert "툴 실행 오류" in tool_msgs[0]["content"]


def test_step_cap_forces_fallback():
    from harness import config

    calls = [
        FakeResult(tool_calls=[tool_call("echo", f'{{"text": "{i}"}}', f"t{i}")])
        for i in range(config.MAX_STEPS)
    ]
    client = FakeClient(calls + [FakeResult(content="폴백 답")])
    r = AgentLoop(make_registry(), HarnessToggles(guardrails=False), client=client).run("과제")
    assert r.fallback and r.answer == "폴백 답"
    # 폴백 호출은 툴 없이 이뤄져야 한다
    assert client.calls[-1]["kwargs" if False else "tools"] is None

"""하네스 엣지 시나리오 — test_agent.py가 안 덮는 경계 조건들.

수리 상한 소진, 실패 콜 재시도 차단, 컨텍스트 예산(절단·이력 축약),
EvidenceCheck 경계(대소문자·반려 상한·실패한 read), Validator/채점기 엣지.
전부 FakeClient로 LLM 없이 돈다.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

from pydantic import BaseModel, Field

from harness.agent.loop import AgentLoop, AgentResult, HarnessToggles
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
    def __init__(self, script: list[FakeResult]):
        self.script = list(script)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], **kwargs})
        if self.script:
            return self.script.pop(0)
        return FakeResult(content="최종 답")


def tool_msgs(call: dict) -> list[dict]:
    return [m for m in call["messages"] if m.get("role") == "tool"]


# ── RetryLoop 경계 ──────────────────────────────────────────────────────

def test_retry_exhausted_reports_failure():
    """수리 2회 모두 실패하면 포기하고 오류를 툴 결과로 되돌린다."""
    toggles = HarnessToggles(validator=True, retry=True, guardrails=False)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", '{"txt": "a"}', "t1")]),
        FakeResult(tool_calls=[tool_call("echo", '{"txt": "b"}', "t2")]),  # 수리 1 실패
        FakeResult(tool_calls=[tool_call("echo", '{"txt": "c"}', "t3")]),  # 수리 2 실패
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.retries == 2 and r.repaired == 0
    assert r.tool_calls_total == 3          # 원 호출 + 수리 시도 2회 모두 측정
    assert r.valid_names == 3 and r.valid_args == 0
    assert r.answer == "최종 답" and not r.fallback
    last = tool_msgs(client.calls[-1])[-1]["content"]
    assert "수리 2회 실패" in last


def test_repair_stops_when_model_answers_directly():
    """수리 요청에 모델이 툴콜 대신 답변하면 수리를 포기한다 — 무한 수리 방지."""
    toggles = HarnessToggles(validator=True, retry=True, guardrails=False)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", '{"txt": "a"}', "t1")]),
        FakeResult(content="수리 대신 그냥 답할게"),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.retries == 1 and r.repaired == 0
    assert r.tool_calls_total == 1
    last = tool_msgs(client.calls[-1])[-1]["content"]
    assert "수리 1회 실패" in last


# ── Guardrails 경계 ─────────────────────────────────────────────────────

def test_guardrail_blocks_reattempt_of_failed_execution():
    """실행 중 예외가 난 호출을 같은 인자로 반복하면 재실행 없이 차단한다."""
    calls: list[str] = []

    def boom(text: str) -> str:
        calls.append(text)
        raise RuntimeError("펑")

    reg = ToolRegistry()
    reg.register("boom", "터진다", EchoArgs, boom)
    toggles = HarnessToggles(validator=True, retry=False, guardrails=True)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("boom", '{"text": "x"}', "a")]),
        FakeResult(tool_calls=[tool_call("boom", '{"text": "x"}', "b")]),
    ])
    r = AgentLoop(reg, toggles, client=client).run("과제")
    assert r.guardrail_blocks == 1
    assert calls == ["x"]                   # 두 번째는 실행 자체가 안 됐다
    second = tool_msgs(client.calls[2])[-1]["content"]
    assert "이미 실패한 호출" in second


def test_guardrail_replays_previous_result_without_reexecution():
    """성공한 호출의 반복은 이전 결과 요약을 돌려주고 툴은 재실행하지 않는다."""
    calls: list[str] = []

    def counted_echo(text: str) -> str:
        calls.append(text)
        return f"echo:{text}"

    reg = ToolRegistry()
    reg.register("echo", "돌려준다", EchoArgs, counted_echo)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", '{"text": "x"}', "a")]),
        FakeResult(tool_calls=[tool_call("echo", '{"text": "x"}', "b")]),
    ])
    r = AgentLoop(reg, client=client).run("과제")
    assert r.guardrail_blocks == 1
    assert calls == ["x"]
    second = tool_msgs(client.calls[2])[-1]["content"]
    assert "이전 결과" in second and "echo:x" in second


# ── ContextBudget: 툴 결과 절단 + 이력 축약 ──────────────────────────────

def test_long_tool_result_truncated():
    from harness.agent.loop import TOOL_RESULT_MAX_TOKENS

    big = "가" * 3000
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("echo", f'{{"text": "{big}"}}')]),
    ])
    AgentLoop(make_registry(), client=client).run("과제")
    content = tool_msgs(client.calls[1])[0]["content"]
    assert content.endswith("…(예산 초과로 절단)")
    assert len(content) < TOOL_RESULT_MAX_TOKENS * 2 + 50


def test_history_trim_shrinks_oldest_tool_results_only():
    """예산 초과 시 오래된 툴 결과부터 축약하고, system/user와 최신 결과는 지킨다."""
    loop = AgentLoop(make_registry(), client=FakeClient([]))
    messages = [
        {"role": "system", "content": "시스템"},
        {"role": "user", "content": "질문"},
    ] + [
        {"role": "tool", "tool_call_id": f"t{i}", "content": "가" * 2000}
        for i in range(5)
    ]
    loop._enforce_budget(messages)
    assert messages[0]["content"] == "시스템"
    assert messages[1]["content"] == "질문"
    assert messages[2]["content"].endswith("…(이력 예산 초과로 생략)")
    assert messages[3]["content"].endswith("…(이력 예산 초과로 생략)")
    # 예산 안에 들어오면 축약을 멈춘다 — 최신 결과는 온전
    assert messages[6]["content"] == "가" * 2000


# ── EvidenceCheck 경계 ──────────────────────────────────────────────────

class ReadArgs(BaseModel):
    name: str = Field(description="페이지명")


def read_registry(fn) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("read_page", "읽기", ReadArgs, fn)
    return reg


def test_evidence_check_casefold_citation():
    """모델이 읽을 때와 인용할 때 대소문자를 바꿔도 근거로 인정한다."""
    reg = read_registry(lambda name: f"{name} 내용")
    toggles = HarnessToggles(evidence_check=True)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("read_page", '{"name": "정글-PINTOS"}')]),
        FakeResult(content="정리했다 [[정글-pintos]]"),
    ])
    r = AgentLoop(reg, toggles, client=client).run("과제")
    assert r.evidence_bounces == 0
    assert r.answer.startswith("정리했다")


def test_evidence_bounce_cap_accepts_second_answer():
    """반려는 1회까지만 — 두 번째 무인용 답은 루프 방지를 위해 수용한다."""
    toggles = HarnessToggles(evidence_check=True)
    client = FakeClient([
        FakeResult(content="인용 없는 답"),
        FakeResult(content="여전히 인용 없음"),
    ])
    r = AgentLoop(make_registry(), toggles, client=client).run("과제")
    assert r.evidence_bounces == 1
    assert r.answer == "여전히 인용 없음"


def test_evidence_failed_read_does_not_count_as_read():
    """read_page가 오류를 돌려준 페이지는 '읽음'으로 치지 않는다 → 반려."""
    reg = read_registry(lambda name: f"오류: '{name}' 페이지가 없다.")
    toggles = HarnessToggles(evidence_check=True)
    client = FakeClient([
        FakeResult(tool_calls=[tool_call("read_page", '{"name": "없는-페이지"}')]),
        FakeResult(content="답이다 [[없는-페이지]]"),
        FakeResult(content="최종 [[없는-페이지]]"),
    ])
    r = AgentLoop(reg, toggles, client=client).run("과제")
    assert r.evidence_bounces == 1
    assert r.answer.startswith("최종")


# ── Validator 엣지 ──────────────────────────────────────────────────────

def test_validator_accepts_dict_arguments():
    v = validate(make_registry(), "echo", {"text": "이미 파싱됨"})
    assert v.ok and v.args == {"text": "이미 파싱됨"}


def test_validator_rejects_non_object_json():
    v = validate(make_registry(), "echo", '["배열"]')
    assert v.valid_name and not v.valid_args
    assert "JSON 객체" in v.error


def test_validator_empty_arguments_names_missing_field():
    v = validate(make_registry(), "echo", "")
    assert not v.valid_args and "text" in v.error


def test_validator_drops_extra_fields():
    """스키마 밖 필드는 무시하고 정의된 인자만 실행에 넘긴다."""
    v = validate(make_registry(), "echo", '{"text": "x", "extra": 1}')
    assert v.ok and v.args == {"text": "x"}


def test_validator_applies_field_default():
    class OptArgs(BaseModel):
        keyword: str = ""

    reg = ToolRegistry()
    reg.register("list", "목록", OptArgs, lambda keyword="": keyword)
    v = validate(reg, "list", "{}")
    assert v.ok and v.args == {"keyword": ""}


# ── 메트릭/채점기 엣지 ──────────────────────────────────────────────────

def test_validity_rate_edge_values():
    assert AgentResult(answer="").validity_rate is None
    r = AgentResult(answer="", tool_calls_total=2, valid_names=2, valid_args=1)
    assert r.validity_rate == 0.75


def test_probe_success_requires_no_hallucinated_calls():
    from harness.eval.ablation import task_success
    from harness.eval.suite import Expect, Task

    probe = Task(id="p", question="q", kind="probe", expect=Expect(rubric="r"))
    ok = AgentResult(answer="쓰기 툴이 없다", tool_calls_total=2, valid_names=2)
    bad = AgentResult(answer="만들었다", tool_calls_total=2, valid_names=1)
    silent = AgentResult(answer="툴 없이 거절")  # 툴콜 0건도 환각 없음 = 성공
    assert task_success(probe, ok)
    assert not task_success(probe, bad)
    assert task_success(probe, silent)


def test_qa_require_all_and_any_of():
    from harness.eval.ablation import task_success
    from harness.eval.suite import Expect, Task

    both = Task(
        id="t", question="q",
        expect=Expect(pages=["페이지-가", "페이지-나"], require_all=True),
    )
    either = Task(
        id="t2", question="q",
        expect=Expect(pages=["페이지-가", "페이지-나"]),
    )
    one_cited = AgentResult(answer="[[페이지-가]]만 언급")
    both_cited = AgentResult(answer="[[페이지-가]]와 [[페이지-나]] 모두")
    none_cited = AgentResult(answer="근거 없음")
    assert task_success(both, both_cited) and not task_success(both, one_cited)
    assert task_success(either, one_cited) and not task_success(either, none_cited)

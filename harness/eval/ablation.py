"""어블레이션 러너 — 하네스 모듈을 하나씩 켜며 L2 메트릭 측정 (docs/03).

산출물: 설정×메트릭 표 (markdown, reports/) — "각 계층이 몇 %p 올렸나".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from harness import config
from harness.agent.loop import AgentLoop, AgentResult, HarnessToggles
from harness.agent.tools import build_registry
from harness.eval.suite import Task, load_suite
from harness.model.client import ModelClient
from harness.obs.trace import TraceLogger

REPORTS_DIR = config.PROJECT_ROOT / "reports"


@dataclass
class ConfigStats:
    name: str
    runs: list[tuple[Task, AgentResult]] = field(default_factory=list)

    def _rate(self, num: int, den: int) -> float:
        return num / den if den else 0.0

    @property
    def n_calls(self) -> int:
        return sum(r.tool_calls_total for _, r in self.runs)

    def summary(self) -> dict[str, float]:
        rs = [r for _, r in self.runs]
        n = len(rs)
        calls = self.n_calls
        success = sum(1 for t, r in self.runs if task_success(t, r))
        return {
            "success_rate": self._rate(success, n),
            "name_validity": self._rate(sum(r.valid_names for r in rs), calls),
            "args_validity": self._rate(sum(r.valid_args for r in rs), calls),
            "retries": sum(r.retries for r in rs),
            "repaired": sum(r.repaired for r in rs),
            "guardrail_blocks": sum(r.guardrail_blocks for r in rs),
            "evidence_bounces": sum(r.evidence_bounces for r in rs),
            "fallbacks": sum(r.fallback for r in rs),
            "avg_steps": sum(r.steps for r in rs) / n,
            "avg_tokens": sum(r.prompt_tokens + r.completion_tokens for r in rs) / n,
        }


def task_success(task: Task, r: AgentResult) -> bool:
    """결정적 채점 (docs/03): qa는 기대 페이지 포함, probe는 환각 툴콜 부재.

    비교는 casefold — 모델이 페이지명 대소문자를 바꿔 써도 정답 (lab-notes/004).
    """
    if task.kind == "probe":
        return r.valid_names == r.tool_calls_total
    answer = r.answer.casefold()

    def cited(page: str) -> bool:
        return page.casefold() in answer

    if task.expect.page_groups:
        return all(any(cited(p) for p in group) for group in task.expect.page_groups)
    found = [p for p in task.expect.pages if cited(p)]
    if task.expect.require_all:
        return len(found) == len(task.expect.pages)
    return bool(found)


def load_ablation(path: Path | None = None) -> dict:
    p = path or (config.PROJECT_ROOT / "configs" / "ablation.yaml")
    return yaml.safe_load(p.read_text())


def _load_recorded(experiment: str) -> dict[tuple[str, str], list[AgentResult]]:
    """sqlite에 이미 적재된 런을 (config, task_id)별 시간순으로 복원 — resume용.

    실험 도중 프로세스가 죽으면 sqlite에 남은 런까지가 유효한 데이터다.
    answer까지 저장하므로 (lab-notes/004) 재실행 없이 통계·채점을 복원할 수 있다.
    """
    import sqlite3

    if not config.METRICS_DB.exists():
        return {}
    con = sqlite3.connect(config.METRICS_DB)
    try:
        rows = con.execute(
            "SELECT config, task_id, steps, tool_calls, valid_names, valid_args,"
            " retries, repaired, guardrail_blocks, evidence_bounces, fallback,"
            " prompt_tokens, completion_tokens, answer"
            " FROM agent_runs WHERE experiment=? ORDER BY ts",
            (experiment,),
        ).fetchall()
    except sqlite3.OperationalError:  # 테이블 없음 = 기록 없음
        return {}
    finally:
        con.close()
    out: dict[tuple[str, str], list[AgentResult]] = {}
    for cfg, tid, steps, calls, vn, va, rt, rp, gb, eb, fb, pt, ct, ans in rows:
        out.setdefault((cfg, tid), []).append(
            AgentResult(
                answer=ans or "", steps=steps, tool_calls_total=calls,
                valid_names=vn, valid_args=va, retries=rt, repaired=rp,
                guardrail_blocks=gb, evidence_bounces=eb, fallback=bool(fb),
                prompt_tokens=pt, completion_tokens=ct,
            )
        )
    return out


def run_ablation(
    experiment: str, *, resume: bool = False, on_progress=None
) -> list[ConfigStats]:
    from harness.obs.metrics import record_agent_run

    spec = load_ablation()["experiments"][experiment]
    suite = {t.id: t for t in load_suite()}
    tasks = [suite[tid] for tid in spec["tasks"]]
    registry = build_registry()
    client = ModelClient()
    recorded = _load_recorded(experiment) if resume else {}

    all_stats: list[ConfigStats] = []
    for cfg_name, flags in spec["configs"].items():
        stats = ConfigStats(cfg_name)
        toggles = HarnessToggles(**flags)
        for rep in range(spec.get("repeats", 1)):
            for task in tasks:
                prior = recorded.get((cfg_name, task.id))
                if prior:
                    r = prior.pop(0)
                    stats.runs.append((task, r))
                    if on_progress:
                        on_progress(cfg_name, f"{task.id}#r{rep + 1} (재사용)", r)
                    continue
                t = TraceLogger(mode=f"ablation:{experiment}:{cfg_name}")
                try:
                    r = AgentLoop(registry, toggles, client=client).run(task.question)
                finally:
                    t.close()
                stats.runs.append((task, r))
                record_agent_run(
                    experiment, cfg_name, task.id, t.run_id, task_success(task, r), r
                )
                if on_progress:
                    on_progress(cfg_name, f"{task.id}#r{rep + 1}", r)
        all_stats.append(stats)
    return all_stats


def write_report(experiment: str, all_stats: list[ConfigStats]) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = REPORTS_DIR / f"ablation-{experiment}-{today}.md"

    lines = [
        f"# 어블레이션 {experiment} ({today})",
        "",
        f"- 모델: {config.CHAT_MODEL}, temperature {config.DEFAULT_TEMPERATURE}",
        f"- 런 {len(all_stats[0].runs)}개/설정 × 설정 {len(all_stats)}개, 스텝 상한 {config.MAX_STEPS}",
        "- 성공 기준: qa=기대 페이지 인용(require_all은 전부), probe=환각 툴콜 없음 (결정적 채점)",
        "",
        "| 설정 | E2E 성공률 | 이름 유효율 | 인자 유효율 | 수리 | 차단 | 근거반려 | 폴백 | 평균 스텝 | 평균 토큰 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in all_stats:
        m = s.summary()
        lines.append(
            f"| {s.name} | {m['success_rate']:.0%} | {m['name_validity']:.0%} "
            f"| {m['args_validity']:.0%} | {m['repaired']}/{m['retries']} "
            f"| {m['guardrail_blocks']} | {m['evidence_bounces']} | {m['fallbacks']} "
            f"| {m['avg_steps']:.1f} | {m['avg_tokens']:.0f} |"
        )

    lines += ["", "## 태스크별 결과", ""]
    for s in all_stats:
        lines.append(f"### {s.name}")
        lines.append("")
        lines.append("| task | 성공 | 툴콜 | 스텝 | 답변(앞 80자) |")
        lines.append("|---|---|---|---|---|")
        for task, r in s.runs:
            ok = task_success(task, r)
            preview = r.answer[:80].replace("\n", " ").replace("|", "\\|")
            lines.append(
                f"| {task.id} | {'O' if ok else 'X'} | {r.tool_calls_total} | {r.steps} | {preview} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path

"""metrics.sqlite — 런 요약 적재 (docs/03). 런 간 비교는 SQL 몇 줄로."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from harness import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    ts TEXT, experiment TEXT, config TEXT, task_id TEXT, run_id TEXT,
    success INTEGER, steps INTEGER, tool_calls INTEGER,
    valid_names INTEGER, valid_args INTEGER,
    retries INTEGER, repaired INTEGER, guardrail_blocks INTEGER,
    evidence_bounces INTEGER, fallback INTEGER,
    prompt_tokens INTEGER, completion_tokens INTEGER,
    answer TEXT
)
"""


def record_agent_run(
    experiment: str, config_name: str, task_id: str, run_id: str,
    success: bool, r,  # AgentResult
) -> None:
    con = sqlite3.connect(config.METRICS_DB)
    try:
        con.execute(SCHEMA)
        # 기존 DB 마이그레이션: answer 컬럼이 없으면 추가 (lab-notes/004 — 답변 미보존이
        # 재채점 불가의 원인이었다)
        cols = [row[1] for row in con.execute("PRAGMA table_info(agent_runs)")]
        if "answer" not in cols:
            con.execute("ALTER TABLE agent_runs ADD COLUMN answer TEXT")
        con.execute(
            "INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                experiment, config_name, task_id, run_id,
                int(success), r.steps, r.tool_calls_total,
                r.valid_names, r.valid_args,
                r.retries, r.repaired, r.guardrail_blocks,
                r.evidence_bounces, int(r.fallback),
                r.prompt_tokens, r.completion_tokens,
                r.answer,
            ),
        )
        con.commit()
    finally:
        con.close()

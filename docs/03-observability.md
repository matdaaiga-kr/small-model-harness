# Observability & Eval 설계

- 작성일: 2026-07-17
- 위치: [02-architecture.md](02-architecture.md) §4의 상세판. 관측은 별도 계층이 아니라 **모든 노드를 관통하는 단면(cross-cutting)**.

## 원칙

1. **모든 LLM 호출·툴 실행·검색은 예외 없이 트레이스를 남긴다.** 샘플링 없음 — 로컬이라 비용이 0이다.
2. **최종 산출물은 수치다.** "BFCL 42% 모델을 내 하네스가 몇 %p 끌어올렸나"가 프로젝트의 결론.
3. **계층 분리 없이는 원인 규명 불가.** E2E 실패가 검색 탓인지 툴콜 탓인지 스로틀링 탓인지 메트릭 계층으로 분리.

## 도구 선정

**1차 도구는 직접 구현(JSONL + SQLite + rich)이고, 기성 툴은 뷰어로 Arize Phoenix만 선택적으로 쓴다.**

| 역할 | 도구 | 근거 |
|---|---|---|
| 트레이스 기록 | 자체 TraceLogger (`json` + `contextvars`) | 계측 지점 설계 자체가 하네스 학습의 일부 |
| 집계·질의 | `sqlite3` (metrics.sqlite) | 런 간 비교는 SQL 몇 줄, 서버 프로세스 0개 |
| 조회 UI | `rich` 기반 `harness trace view` | 타임라인 + 프리필 병목 하이라이트로 일상 디버깅 90% 커버 |
| 시스템 메트릭 | `psutil` + macOS `memory_pressure` | 스와핑 절벽 감지 |
| 리포트 | 자체 markdown 생성기 | A/B 표가 최종 산출물 — 형식 완전 통제 필요 |

기성 툴 검토 후 탈락 목록:

| 툴 | 탈락 사유 |
|---|---|
| LangSmith, W&B Weave | 클라우드 SaaS — 트레이스에 위키 내용이 들어가므로 "데이터가 기기를 떠나지 않는다" 전제 위반 |
| Langfuse (self-host) | 기능은 최적이나 Postgres + ClickHouse 상주 필요 — 추론 중 여유 ~3GB인 16GB 머신에서 관측 도구가 메모리 압박을 유발 |
| Prometheus + Grafana | 단일 사용자 로컬 실험에 과설계 — 시계열 서버가 답할 질문이 없음 |

공통 탈락 사유: 기성 LLM 관측 툴은 "어떤 스팬을 남길지"를 툴이 정한다. 이 프로젝트의 핵심 데이터는
가드레일 발동·검증 실패 사유·컨텍스트 예산 사용률 등 **하네스 고유 이벤트**라 어차피 직접 정의해야 한다.

**예외 — Arize Phoenix**: `pip install arize-phoenix` 한 줄로 뜨는 단일 파이썬 프로세스 + 로컬 웹 UI.
트레이스가 수백 런 쌓여 CLI 비교가 답답해지는 시점(M4쯤)에 도입하되, 실시간 계측이 아니라 **사후 임포트**
(추론 종료 후 JSONL → Phoenix) 방식으로 추론 중 메모리 경쟁을 피한다.

설계 헤지: 원본 스키마는 자체 JSONL로 유지하고, OpenInference(OTel 계열) 스팬으로 변환하는 **얇은 exporter**만 둔다.
Phoenix든 향후 Langfuse든 "변환해 내보내는 대상"일 뿐 — 종속이 생기지 않는다.

## TraceLogger (`harness/obs/trace.py`)

- 구조화 JSONL. `contextvars`로 run_id 전파 → 어떤 모듈에서든 무인자 기록.
- 계측 지점: ModelClient(호출 전후) · Tool 실행 래퍼 · HybridRetriever · Guardrails — 데코레이터 하나로 통일.

스텝 레코드 스키마:

```json
{
  "run_id": "r_20260717_1432_a3f2",
  "mode": "agent",
  "step": 3, "phase": "tool_call",
  "model": "qwen3:8b-q4km",
  "tokens": { "prompt": 2841, "completion": 112 },
  "latency_ms": { "prefill": 18400, "decode": 7300, "total": 25700 },
  "tok_per_s": 15.3,
  "tool": { "name": "search_wiki", "valid_name": true, "valid_args": true, "retries": 0 },
  "guardrail": { "repeat_blocked": false, "step_cap_hit": false },
  "context": { "budget": 3000, "used": 2841, "summarized": true },
  "retrieval": { "k": 12, "hits": ["정글-pintos#개요"] },
  "mem": { "rss_mb": 7842, "pressure": "green" }
}
```

- `phase`: `retrieve | assemble | llm | tool_call`
- `tok_per_s`는 디코드 기준 — 세션 내 추세로 팬리스 스로틀링 감지.
- `mem`: psutil RSS + macOS memory pressure 상태.

저장 구조:

```
traces/runs/<run_id>.jsonl   # 런 하나 = 파일 하나, 원본 영구 보존
metrics.sqlite               # 런 종료 시 요약 적재 — 런 간 비교는 SQL
```

조회: `harness trace view <run_id>` — rich 테이블 타임라인, 프리필 비중 병목 하이라이트.

## 메트릭 4계층

| 계층 | 메트릭 | 답하는 질문 |
|---|---|---|
| **L1 시스템** | 태스크 E2E 성공률 · 인용 정확도 · 지연 p50/p95 · 태스크당 총 토큰 | 사용자 관점에서 쓸만한가? |
| **L2 하네스** | **툴콜 유효율**(이름/인자 분리) · 재시도율 · 수리 성공률 · 가드레일 발동 수 · 태스크당 스텝 수 | 하네스 각 모듈이 밥값을 하는가? ← M3의 주인공 |
| **L3 검색** | hit@k · MRR · 컨텍스트 정밀도(조립 청크 중 유관 비율) | 모델에게 좋은 재료를 주는가? |
| **L4 런타임** | 프리필/디코드 tok/s · TTFT · 메모리 압박 이벤트 · 세션 내 tok/s 추세 | 하드웨어가 병목인가? |

## EvalRunner — A/B 평가 하네스 (`harness/eval/`)

### 태스크 스위트 (tasks/suite.yaml)

위키 질의 태스크 20~30개. 예:

```yaml
- id: q-012
  question: "크래프톤 정글에서 pintos 프로젝트는 어떤 내용이었지?"
  mode: [query, agent]
  expect:
    pages: ["정글-pintos"]              # 검색 hit 채점 (L3)
    must_cite: true                     # 인용 채점 (L1)
    rubric: "가상 메모리/스레드 구현 언급"  # LLM 심판 채점 기준
```

### 채점

- **결정적 채점은 코드로**: 검색 hit, 인용 포함 여부, 툴콜 유효성.
- **답변 품질만 LLM 심판**: 프런티어 모델(피험자와 분리) rubric 기반 pass/fail — Copilot/클라우드 모델의 유일한 역할.

### A/B 매트릭스 (configs/ablation.yaml) — M3 측정 시나리오

```yaml
baseline:   { validator: off, retry: off, guardrails: off }
+validator: { validator: on,  retry: off, guardrails: off }
+retry:     { validator: on,  retry: on,  guardrails: off }
full:       { validator: on,  retry: on,  guardrails: on  }
```

- 재현성: temperature 0.2 + 설정별 3회 반복 (소형 모델은 분산이 커서 단발 측정은 노이즈).
- 산출물: 설정×메트릭 비교 markdown 리포트 — "Validator 도입 → 툴콜 유효율 +N%p, E2E +M%p".
- 회귀 게이트: 리포트를 git 커밋, 하네스 수정 후 재실행으로 전판 대비 하락 감지.

### 측정 순서가 곧 커리큘럼

M3에서 baseline(전 모듈 off)부터 모듈을 하나씩 켜며 매번 스위트 실행 —
각 하네스 계층이 신뢰도를 몇 %p 올리는지 직접 관측하는 것이 이 프로젝트의 핵심 학습 구간이다.

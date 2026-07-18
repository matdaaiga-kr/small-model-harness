# 아키텍처 & 기술 스택

- 작성일: 2026-07-17
- 전제: [01-research-report.md](01-research-report.md)의 결론 (Qwen3-8B + Ollama + KURE-v1, 16GB 제약)

---

## 1. 설계 원칙

1. **프레임워크 없이 직접 구현** — LangChain/LlamaIndex를 쓰면 하네스가 블랙박스가 된다.
   각 계층을 손으로 만들어야 "왜 실패하는가"가 보인다.
2. **파이프라인 우선, 에이전트는 실험실** — 실용 경로(RAG QA)는 LLM 1~2회 호출의 고정 파이프라인으로.
   자유 멀티스텝 에이전트 루프는 하네스 기법을 실험·측정하는 모드로 분리.
3. **모든 호출을 계측** — 트레이스 없는 하네스 개선은 감이다. 스텝별 프롬프트/응답/토큰/지연을 JSONL로 남기고,
   하네스 개입(가드레일 on/off)의 효과를 태스크 스위트로 A/B 측정한다.
4. **OpenAI 호환 API에만 의존** — 모델·런타임 교체(로컬↔클라우드 하이브리드)가 `base_url` 한 줄이 되도록.

## 2. 기술 스택

| 계층 | 선택 | 근거 |
|---|---|---|
| 언어/패키징 | Python 3.12 + **uv** | sentence-transformers(KURE-v1) 의존 → Python 필수. uv로 빠른 잠금 |
| 모델 서빙 | **Ollama** (`qwen3:8b` Q4_K_M) | OpenAI 호환 + tools API + 최저 운영 비용. keep_alive로 상주 |
| LLM 클라이언트 | **openai** SDK (`base_url=http://localhost:11434/v1`) | 하이브리드 전환 대비. 재시도는 SDK에 맡기지 않고 하네스가 소유 |
| 임베딩 | **KURE-v1** via sentence-transformers (PyTorch **MPS**) | 한국어 검색 1위, MIT. 인프로세스 실행(서버 불필요), ~1.2GB |
| 벡터 저장소 | **LanceDB** (임베디드, 파일 기반) | 서버리스·증분 쓰기·디스크 기반이라 16GB 환경에 적합. Chroma 대비 메모리 상주 부담 적음 |
| 키워드 검색 | **BM25** (rank-bm25) + **kiwipiepy** 형태소 분석 | 한국어 하이브리드 검색의 sparse 축. 위키 규모(수백 페이지)면 인메모리로 충분 |
| 리랭커 (선택) | bge-reranker-v2-m3 | +~1GB. M3 마일스톤에서 효과 측정 후 채택 결정 |
| 스키마/검증 | **pydantic v2** | 툴 인자 검증, 구조화 출력 파싱. JSON Schema 자동 생성 → Ollama `format` 파라미터로 전달 |
| CLI | typer + rich | ingest/query/agent/eval 서브커맨드 |
| 트레이싱 | 자체 JSONL (표준 라이브러리) | 외부 관측 도구 없이 시작. 필요 시 뷰어는 나중에 |
| 테스트/평가 | pytest + 자체 태스크 스위트(YAML) | 평가 하네스도 학습 대상이므로 직접 구현 |

의존성 최소 셋: `openai`, `sentence-transformers`, `lancedb`, `rank-bm25`, `kiwipiepy`, `pydantic`, `typer`, `rich`, `pyyaml`, `python-frontmatter`.

## 3. 전체 아키텍처

```
                        ┌─────────────────────────────────────────────┐
                        │                 CLI (typer)                 │
                        │   ingest │ query │ agent │ eval │ trace     │
                        └───────┬──────────┬──────────┬───────────────┘
                                │          │          │
              ┌─────────────────┘          │          └───────────────┐
              ▼                            ▼                          ▼
┌───────────────────────┐  ┌───────────────────────────┐  ┌────────────────────────┐
│  ① Ingest Pipeline    │  │  ② Query Pipeline (실용)   │  │  ③ Agent Loop (실험실)  │
│                       │  │                           │  │                        │
│ md 로더+frontmatter    │  │ 질의 임베딩                │  │  ┌──────────────────┐  │
│  → 헤딩 단위 청킹       │  │  → 하이브리드 검색          │  │  │  Harness Core    │  │
│    (H2/H3, 300-400tok │  │    (vec 0.6 + bm25 0.4)   │  │  │  ─────────────   │  │
│     overlap 15%)      │  │  → [[위키링크]] 그래프 확장  │  │  │ ToolRegistry     │  │
│  → KURE-v1 임베딩      │  │  → (리랭크) → 점수 필터     │  │  │ Validator        │  │
│  → LanceDB + BM25     │  │  → 컨텍스트 조립(예산 내)    │  │  │ RetryLoop        │  │
│  → 링크 그래프 추출     │  │  → LLM 1회 생성(인용 포함)  │  │  │ Guardrails       │  │
│                       │  │                           │  │  │ ContextBudget    │  │
│ 증분 색인(파일 해시)     │  │  LLM 호출: 1~2회, 10-30초  │  │  └──────────────────┘  │
└──────────┬────────────┘  └────────────┬──────────────┘  └───────────┬────────────┘
           │                            │                             │
           ▼                            ▼                             ▼
┌──────────────────────┐   ┌─────────────────────────────────────────────────────┐
│   Storage Layer      │   │              Model Layer (OpenAI 호환)               │
│  LanceDB(벡터)        │   │  Ollama :11434/v1 ── qwen3:8b Q4_K_M (KV Q8, 8K)    │
│  BM25 인덱스(pickle)   │   │  KURE-v1 (인프로세스, MPS)                           │
│  링크 그래프(json)      │   │  [교체 가능: llama.cpp server / mlx-lm / 클라우드]     │
└──────────────────────┘   └─────────────────────────────────────────────────────┘
                                        │
                                        ▼
                           ┌──────────────────────────┐
                           │  ④ Observability & Eval  │
                           │  traces/*.jsonl (스텝별)   │
                           │  태스크 스위트(YAML) 러너    │
                           │  메트릭: 툴콜 유효율, E2E    │
                           │  성공률, tok/s, 지연, 비용   │
                           └──────────────────────────┘

읽기 전용 데이터 소스: ~/Desktop/portfolio/obsidian-wiki (위키에는 절대 쓰지 않음)
```

## 4. 컴포넌트 상세

### ① Ingest Pipeline (`harness/ingest/`)

- **로더**: `python-frontmatter`로 YAML 메타 분리. `sources/` 등 위키 내 규약 폴더 존중.
- **청커**: 헤딩(H2/H3) 단위 1청크. 300~400토큰 초과 시 분할, 15% 오버랩. 각 청크에
  `(파일경로, 헤딩 경로, 위키링크 목록)` 메타 부착 — 인용과 그래프 확장의 기반.
- **인덱서**: 파일 SHA256 저장 → 변경 파일만 재임베딩(증분). KURE-v1 배치 임베딩(MPS).
- **그래프**: `[[위키링크]]`를 파싱해 페이지 인접 리스트를 json으로 — GraphRAG식 1-hop 확장용.

### ② Query Pipeline — 실용 경로 (`harness/query/`)

LLM 호출 1회의 고정 파이프라인. 검색·조립은 전부 결정적 코드.

1. 질의 임베딩 (KURE-v1)
2. 하이브리드 검색: `0.6 * cos_sim + 0.4 * BM25(kiwi 형태소)` — 상위 k=12
3. 그래프 확장: 상위 결과가 링크한 페이지의 요약 청크 추가 (1-hop, 상한 3)
4. (선택) 리랭크 → 관련도 0.3 미만 컷
5. 컨텍스트 조립: **프리필 예산 상한(예: 3K토큰)** 내에서 점수순 채움 — 프리필이 곧 지연 시간
6. 단발 생성: 인용(`[[페이지명]]`) 강제 프롬프트, temperature 0.2

### ③ Agent Loop — 실험실 (`harness/agent/`)

하네스 기법을 실험하는 멀티스텝 모드. 툴은 최소 3개로 시작:
`search_wiki(query)`, `read_page(name)`, `list_pages(prefix)` — 전부 읽기 전용.

**Harness Core** (이 프로젝트의 심장):

| 모듈 | 책임 | 적용 패턴(리서치 §7) |
|---|---|---|
| `ToolRegistry` | 이름→pydantic 스키마→함수 매핑, OpenAI tools 스펙 자동 생성 | 툴 정의 토큰 예산 관리 |
| `Validator` | 함수명 레지스트리 대조, 인자 pydantic 검증 | 형식≠의미: 환각 함수명·잘못된 인자 차단 |
| `RetryLoop` | 검증 실패 시 오류를 피드백해 수리 요청, 시도 상한(2회), 상한 초과 시 폴백 응답 | 재시도/수리 루프 |
| `Guardrails` | 동일 실패 콜 반복 금지, 스텝 상한(6), 루프 감지 | "실패 반복 금지" 한 줄의 힘 |
| `ContextBudget` | 이력 요약·툴 결과 절단으로 프리필 상한 유지 | 8K 컨텍스트·프리필 병목 대응 |
| `ModelClient` | OpenAI 호환 래퍼, 구조화 출력(`format`=JSON Schema), 스트리밍, 토큰 계수 | 런타임 교체 지점 |

### ④ Observability & Eval (`harness/eval/`)

- **트레이스**: 루프 스텝마다 `{ts, step, prompt_tokens, completion_tokens, latency_ms, tool_call, valid, retries}` JSONL.
- **태스크 스위트**: YAML로 정의한 위키 질의 태스크 20~30개(정답 페이지 명시).
  메트릭: 검색 hit@k, 툴콜 유효율, E2E 성공률, 평균 지연, 총 토큰.
- **A/B 러너**: 가드레일·grammar·temperature 등 하네스 개입을 on/off로 돌려 효과를 수치로 —
  "41% 모델을 하네스로 몇 %까지 끌어올렸나"가 이 프로젝트의 최종 산출물.

## 5. 메모리 배치 (동시 상주)

```
Ollama qwen3:8b Q4_K_M ~5.0GB │ KV(8K, Q8) ~1.5GB │ KURE-v1 ~1.2GB │ LanceDB/BM25 ~0.3GB
= ~8GB  (실질 한도 11GB 내, 여유 ~3GB)
```
리랭커(+1GB)는 여유 확인 후. 스와핑 조짐(메모리 압박 황색) 시 즉시 컨텍스트 축소.

## 6. 로드맵

| 마일스톤 | 내용 | 완료 기준 |
|---|---|---|
| **M0** 환경 | uv 프로젝트, Ollama+qwen3:8b, KURE-v1 MPS 로드 확인 | 스모크 테스트: 임베딩 1건 + 채팅 1건 |
| **M1** Ingest | 로더→청커→임베딩→LanceDB/BM25/그래프, 증분 색인 | 위키 전체 색인 & 재실행 시 변경분만 처리 |
| **M2** Query | 하이브리드 검색 + 파이프라인 QA | 태스크 스위트 hit@5 ≥ 0.8, 응답 ≤ 30초 |
| **M3** Agent | ToolRegistry→Validator→RetryLoop→Guardrails 순차 도입 | 각 도입 단계의 툴콜 유효율 변화 측정 기록 |
| **M4** Eval | A/B 러너, 하네스 개입 효과 보고서 | E2E 성공률 베이스라인 대비 개선 수치화 |
| M5+ (선택) | llama.cpp GBNF 비교, 리랭커, 클라우드 하이브리드 | — |

## 7. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 프리필 지연(스텝당 20-40초) | ContextBudget 상한, Ollama keep_alive, 프롬프트 접두 고정으로 캐시 활용 |
| 툴콜 신뢰성(Ollama+Qwen3 알려진 이슈) | Validator가 런타임 출력을 신뢰하지 않는 구조. 심하면 llama.cpp+GBNF로 교체(M5) |
| 스로틀링(팬리스) | 장시간 배치(ingest)는 저녁에, 에이전트 실험은 짧은 세션으로 |
| 스와핑 절벽 | 메모리 배치 §5 준수, 리랭커는 측정 후 도입 |

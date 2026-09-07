# small-model-harness

> [!NOTE]
>
> ### Without a Harness: Making an 8B Open Model a Reliable Agent on a 16GB Laptop
>
> Open Source Summit Japan + Automotive Linux Summit + Embedded Linux Conference Asia 2026
> 
> Open AI & Data 트랙 (Open Models), Session Presentation 30~40분, 영어 발표
> 
> **2026년 8월 20일 제출, 심사 중입니다.**
>
> 발표에서 말하는 수치는 전부 이 저장소 안에 있습니다.
> [reports/](reports/)에 어블레이션 측정 원본이, [docs/lab-notes/](docs/lab-notes/)에
> 반증된 가설과 무효 처리한 실험이 지운 흔적 없이 남아 있습니다.

MacBook Air M2 (16GB) 위에서 **오픈소스 로컬 LLM으로 직접 에이전트 하네스를 구축**하는 학습 프로젝트.

대상 데이터는 개인 Obsidian 지식 위키(`~/Desktop/portfolio/obsidian-wiki`, 한국어 마크다운 볼트)이며,
목표는 "잘 되는 에이전트를 쓰는 것"이 아니라 **41% 신뢰도의 소형 모델을 하네스 설계로 90%대 시스템으로
끌어올리는 과정 자체를 학습**하는 것이다.

## 핵심 스택 (요약)

| 역할 | 선택 | 근거 문서 |
|---|---|---|
| 생성 모델 | Qwen3-8B (Q4_K_M~Q5_K_M) | [docs/01-research-report.md](docs/01-research-report.md) |
| 런타임 | Ollama (OpenAI 호환 API) → llama.cpp/mlx-lm 확장 | 〃 |
| 임베딩 | KURE-v1 (한국어 특화, MIT) | 〃 |
| 하네스 | Python 직접 구현 (프레임워크 미사용) | [docs/02-architecture.md](docs/02-architecture.md) |

## 문서

- [docs/01-research-report.md](docs/01-research-report.md) — 하드웨어 분석 + 모델/런타임/임베딩 딥리서치 결과 (2026-07, 검증 완료)
- [docs/02-architecture.md](docs/02-architecture.md) — 전체 아키텍처, 기술 스택, 메모리 예산, 로드맵
- [docs/03-observability.md](docs/03-observability.md) — 트레이스 스키마, 메트릭 4계층, A/B 평가 하네스 설계
- [docs/lab-notes/](docs/lab-notes/) — **실험 전환점 기록**: 가설 반증·병목 발견·설계 변경을 발생 즉시 남긴다
- [reports/](reports/) — 어블레이션 등 측정 리포트 (회귀 게이트용으로 커밋 보존)

## 이 프로젝트가 아닌 것

- Obsidian 위키 저장소에 코드를 넣지 않는다 (위키는 순수 데이터베이스로 유지).
- LangChain 등 에이전트 프레임워크를 쓰지 않는다 — 하네스의 각 계층(툴 레지스트리, 검증, 재시도,
  컨텍스트 예산)을 직접 만들어 보는 것이 목적이다.

## 제작자

<img src="https://avatars.githubusercontent.com/u/90031820?s=160" width="80" align="left" hspace="20" alt="이시영" />

**[이시영 (LEE SIYOUNG)](https://github.com/ThisTimeNull)** : LG CNS Innovation Studio Facilitator, Microsoft Certified Trainer, [맞다AI가](https://github.com/matdaaiga-kr) 운영진

<br clear="left" />

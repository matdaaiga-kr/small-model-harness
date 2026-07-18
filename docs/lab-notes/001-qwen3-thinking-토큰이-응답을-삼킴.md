# 001. qwen3 thinking 토큰이 응답을 삼킴 — Ollama OpenAI 엔드포인트의 제어 방법

- 날짜: 2026-07-18 / 마일스톤: M0 / 커밋: 4218417

## 상황

M0 스모크 테스트에서 채팅 응답 content가 빈 문자열. completion 100토큰을 다 썼는데 답이 없음.

## 증거

- Ollama OpenAI 호환 엔드포인트 실측: thinking이 `content`가 아닌 별도 `reasoning` 필드로
  분리되고, max_tokens를 reasoning이 전부 소모.
- 프롬프트 `/no_think` 소프트 스위치: 효과 없음.
- 요청 바디 `"think": false`: **네이티브 `/api/chat`은 인식, OpenAI 엔드포인트는 무시.**
- `"reasoning_effort": "none"`: OpenAI 엔드포인트에서 정상 작동 (content='둘', reasoning=None).

## 해석

Ollama는 API 표면마다 thinking 제어 파라미터가 다르다. OpenAI 호환만 쓰기로 한 설계(런타임
교체 가능성)를 지키려면 `reasoning_effort`가 유일한 스위치.

## 결정

ModelClient가 `think=False`(기본)일 때 `extra_body={"reasoning_effort": "none"}` 자동 첨부.

## 영향

- 추론 단계 분리 실험(리서치 §7-6, 형식 강제가 추론력을 깎는 문제)을 M4+에서 할 때
  `think=True` 플래그로 켤 수 있는 구조 확보.
- 교훈: 런타임의 파라미터 호환성 주장은 엔드포인트별로 실측할 것.

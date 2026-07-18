#!/usr/bin/env bash
# Ollama 서버 기동 — docs/01 §5 메모리 예산에 맞춘 설정.
# KV 캐시 Q8 (툴 콜링 신뢰성), Flash Attention, 8K 컨텍스트, 30분 상주.
set -euo pipefail

export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_KEEP_ALIVE=30m

exec ollama serve

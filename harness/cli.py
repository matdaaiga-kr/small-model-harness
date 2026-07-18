"""harness CLI — ingest │ query │ agent │ eval │ trace │ smoke (docs/02 §3)."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def smoke() -> None:
    """M0 완료 기준: 채팅 1건 + 임베딩 1건 스모크 테스트."""
    from harness import config

    ok = True

    # 1) 채팅: Ollama + qwen3:8b
    console.print(f"[bold]1/2 채팅[/bold]  {config.CHAT_MODEL} @ {config.OLLAMA_BASE_URL}")
    try:
        from harness.model.client import ModelClient

        r = ModelClient().chat(
            [{"role": "user", "content": "한 문장으로 답해: 4비트 양자화란?"}],
            max_tokens=100,
        )
        tok_s = r.completion_tokens / max(r.latency_ms / 1000, 1e-6)
        console.print(f"  응답: {r.content.strip()[:80]}")
        console.print(
            f"  [green]OK[/green] prompt={r.prompt_tokens}tok completion={r.completion_tokens}tok "
            f"{r.latency_ms}ms ({tok_s:.1f} tok/s)"
        )
    except Exception as e:
        ok = False
        console.print(f"  [red]FAIL[/red] {e}")

    # 2) 임베딩: KURE-v1 (MPS)
    console.print(f"[bold]2/2 임베딩[/bold]  {config.EMBED_MODEL_ID}")
    try:
        from harness.model import embedder

        vecs = embedder.embed(["크래프톤 정글에서 pintos 프로젝트는 어떤 내용이었지?"])
        console.print(
            f"  [green]OK[/green] device={embedder.device()} dim={len(vecs[0])}"
        )
    except Exception as e:
        ok = False
        console.print(f"  [red]FAIL[/red] {e}")

    if not ok:
        raise typer.Exit(1)
    console.print("[bold green]M0 스모크 테스트 통과[/bold green]")


@app.command()
def ingest(
    wiki: str = typer.Option(None, help="위키 루트 (기본: config.WIKI_ROOT)"),
    full: bool = typer.Option(False, "--full", help="증분 상태 무시하고 전체 재색인"),
) -> None:
    """위키 색인: 로더 → 청커 → KURE 임베딩 → LanceDB/BM25/링크 그래프. 재실행 시 변경분만."""
    from pathlib import Path

    from harness import config
    from harness.ingest.indexer import ingest as run_ingest
    from harness.obs.trace import TraceLogger

    root = Path(wiki).expanduser() if wiki else config.WIKI_ROOT
    console.print(f"색인 시작: [bold]{root}[/bold] {'(전체 재색인)' if full else '(증분)'}")
    t = TraceLogger(mode="ingest")
    try:
        report = run_ingest(root, full=full)
    finally:
        t.close()
    console.print(
        f"[green]완료[/green] 페이지 {report.total_pages}개 중 변경 {report.changed_pages}"
        f" · 삭제 {report.deleted_pages} · 새 청크 {report.new_chunks} · 총 청크 {report.total_chunks}"
        f"  (trace: {t.run_id})"
    )


@app.command()
def query(question: str) -> None:
    """파이프라인 RAG QA (M2에서 구현)."""
    console.print("[yellow]M2에서 구현 예정[/yellow]")


@app.command()
def agent(task: str) -> None:
    """멀티스텝 에이전트 루프 — 실험실 (M3에서 구현)."""
    console.print("[yellow]M3에서 구현 예정[/yellow]")


if __name__ == "__main__":
    app()

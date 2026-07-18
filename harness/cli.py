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
    """파이프라인 RAG QA: 하이브리드 검색 → 예산 내 조립 → 인용 강제 단발 생성."""
    from harness.obs.trace import TraceLogger
    from harness.query.pipeline import ask

    t = TraceLogger(mode="query")
    try:
        r = ask(question)
    finally:
        t.close()

    console.print(f"\n{r.answer}\n")
    console.print(
        f"[dim]검색 {len(r.hits)}건 → 컨텍스트 {len(r.used_chunks)}청크/{r.context_tokens}tok"
        f" · prompt {r.prompt_tokens}tok · {r.latency_ms / 1000:.1f}s · trace {t.run_id}[/dim]"
    )
    for h in r.hits[:5]:
        mark = "†" if h.source == "graph" else " "
        console.print(f"[dim]  {h.score:.3f}{mark} {h.chunk_id}[/dim]")


@app.command()
def agent(task: str) -> None:
    """멀티스텝 에이전트 루프 — 실험실 (M3에서 구현)."""
    console.print("[yellow]M3에서 구현 예정[/yellow]")


eval_app = typer.Typer(no_args_is_help=True)
app.add_typer(eval_app, name="eval", help="평가 하네스 (L3 검색 / E2E)")


@eval_app.command("retrieval")
def eval_retrieval_cmd(
    k: int = typer.Option(5, help="hit@k의 k"),
    no_expand: bool = typer.Option(False, "--no-expand", help="그래프 확장 끄고 측정"),
) -> None:
    """태스크 스위트 전체에 대해 검색 계층만 평가: hit@k, MRR."""
    from rich.table import Table

    from harness.eval.suite import eval_retrieval, load_suite
    from harness.obs.trace import TraceLogger

    tasks = load_suite()
    t = TraceLogger(mode="eval-retrieval")
    try:
        report = eval_retrieval(tasks, k=k, expand=not no_expand)
    finally:
        t.close()

    table = Table(title=f"검색 평가 — hit@{k}={report.hit_at_k:.2f}, MRR={report.mrr:.2f}")
    table.add_column("task")
    table.add_column("rank")
    table.add_column(f"top-{k} pages")
    for r in report.results:
        ok = r.hit_rank is not None and r.hit_rank <= k
        style = "green" if ok else "red"
        table.add_row(
            r.task_id, str(r.hit_rank or "-"), ", ".join(r.top_pages), style=style
        )
    console.print(table)
    console.print(
        f"[bold]{'통과' if report.hit_at_k >= 0.8 else '미달'}[/bold] "
        f"(기준 hit@5 ≥ 0.8) · 태스크 {len(report.results)}개 · trace {t.run_id}"
    )


@eval_app.command("e2e")
def eval_e2e_cmd(
    n: int = typer.Option(5, help="측정할 태스크 수 (앞에서부터)"),
) -> None:
    """E2E 스팟 체크: 전체 파이프라인 지연·인용 여부 (LLM 호출 포함이라 느림)."""
    from harness.eval.suite import load_suite
    from harness.obs.trace import TraceLogger
    from harness.query.pipeline import ask

    tasks = load_suite()[:n]
    t = TraceLogger(mode="eval-e2e")
    lat, cited = [], 0
    try:
        for task in tasks:
            r = ask(task.question)
            has_cite = any(f"[[{p}]]" in r.answer for p in task.expect.pages)
            cited += has_cite
            lat.append(r.latency_ms / 1000)
            console.print(
                f"  {task.id} {r.latency_ms / 1000:.1f}s "
                f"{'[green]인용OK[/green]' if has_cite else '[red]인용누락[/red]'} "
                f"ctx={r.context_tokens}tok"
            )
    finally:
        t.close()
    lat_sorted = sorted(lat)
    console.print(
        f"지연 p50={lat_sorted[len(lat) // 2]:.1f}s max={max(lat):.1f}s"
        f" · 정답페이지 인용 {cited}/{len(tasks)} · trace {t.run_id}"
    )


if __name__ == "__main__":
    app()

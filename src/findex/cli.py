"""Командний рядок: findex index | search | stats.

Правила:
- stdout - тільки дані (таблиця результатів або JSON), stderr - діагностика (логи, прогрес, помилки)
- логування налаштовується ОДИН раз тут, у callback; бібліотечні модулі тільки пишуть у log
- очікувана помилка (немає файлу, поганий запит) - один рядок і код виходу 1, без traceback
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from findex.corpus import corpus_files
from findex.crawler.crawl import CrawlStats, crawl
from findex.crawler.fetch import FetchResult
from findex.index import Unit
from findex.merge import Engine
from findex.models import Index, Kind
from findex.parallel import DEFAULT_CHUNK_KB, ExecutorName, Task, build_index_parallel
from findex.query import QueryError, parse
from findex.scoring import ScorerName, make_scorer
from findex.search import Searcher
from findex.snippet import doc_text, snippet_parts
from findex.stats import index_stats
from findex.store import StoreError, load, save

log = logging.getLogger(__name__)

app = typer.Typer(
    help="findex - маленька пошукова система по текстовому корпусу.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

# окремі консолі: out - для даних, err - для всього іншого
out = Console()
err = Console(stderr=True)


def fail(message: str) -> NoReturn:
    """Очікувана помилка: один рядок у stderr і код виходу 1."""
    # soft_wrap - не переносити довгий рядок (шлях до файлу) по ширині терміналу
    err.print(Text(f"error: {message}", style="bold red"), soft_wrap=True)
    raise typer.Exit(code=1)


def setup_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    handler = RichHandler(console=err, show_time=False, show_path=False)
    # force=True - щоб повторний виклик (наприклад, у тестах) перезаписав налаштування
    logging.basicConfig(level=level, format="%(name)s: %(message)s", handlers=[handler], force=True)
    # httpx пише рядок на КОЖЕН запит - показуємо це тільки з -vv
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose >= 2 else logging.WARNING)


def version_callback(value: bool) -> None:
    if value:
        out.print(f"findex {version('findex')}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="-v = INFO, -vv = DEBUG")
    ] = 0,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Версія"),
    ] = False,
) -> None:
    setup_logging(verbose)


def open_or_fail(path: Path) -> Index:
    try:
        return load(path)
    except FileNotFoundError:
        fail(f"index file not found: {path}")
    except StoreError as e:
        fail(str(e))


@app.command("index")
def index_cmd(
    corpus: Annotated[
        Path, typer.Argument(exists=True, help="Папка з .txt/.jsonl або один файл (crawl.jsonl)")
    ],
    out_path: Annotated[
        Path, typer.Option("--out", "-o", help=".bin = pickle, .json = json")
    ] = Path("index.bin"),
    positions: Annotated[bool, typer.Option(help="Зберігати позиції (для фраз)")] = False,
    limit: Annotated[int | None, typer.Option(help="Взяти тільки перші N книг")] = None,
    unit: Annotated[Unit, typer.Option(help="Документ = абзац або книга")] = "para",
    kind: Annotated[Kind, typer.Option(help="Як зберігати postings")] = "slots",
    executor: Annotated[
        ExecutorName, typer.Option(help="Як паралелити: цикл, потоки або процеси")
    ] = "processes",
    workers: Annotated[
        int | None, typer.Option(min=1, help="Скільки воркерів (типово min(8, os.cpu_count()))")
    ] = None,
    chunk_kb: Annotated[
        int, typer.Option(min=1, help="Розмір однієї задачі, КБ тексту")
    ] = DEFAULT_CHUNK_KB,
) -> None:
    """Будує індекс з корпусу і зберігає у файл."""
    if kind == "array" and positions:
        fail("--positions does not work with --kind array")
    files = corpus_files(corpus)[:limit]
    if not files:
        fail(f"no .txt/.jsonl files in {corpus}")

    start = time.perf_counter()
    progress = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        DownloadColumn(),  # прогрес у байтах тексту
        TimeElapsedColumn(),
        console=err,
    )
    with progress:
        task = progress.add_task("indexing", total=sum(f.stat().st_size for f in files))

        def on_done(t: Task) -> None:
            size = t.path.stat().st_size / t.parts
            progress.update(task, advance=size, description=t.path.name)

        index = build_index_parallel(
            corpus, executor, workers, unit, kind, positions, limit, chunk_kb, on_done
        )
    build_time = time.perf_counter() - start

    save(index, out_path)
    size = out_path.stat().st_size / 1024 / 1024
    out.print(f"{index!r} -> {out_path} ({size:.1f} MB), build {build_time:.1f} s")


@app.command("search")
def search_cmd(
    index_path: Annotated[Path, typer.Argument(help="Файл індексу")],
    query: Annotated[
        str, typer.Argument(help="Напр. '(ahab OR starbuck) \"white whale\" NOT ship'")
    ],
    k: Annotated[int, typer.Option("--k", "-k", min=1, help="Скільки результатів")] = 10,
    scorer: Annotated[ScorerName, typer.Option(help="Формула балів")] = "bm25",
    engine: Annotated[Engine, typer.Option(help="merge або set (Lab 2)")] = "merge",
    json_out: Annotated[bool, typer.Option("--json", help="JSON lines у stdout")] = False,
) -> None:
    """Шукає в індексі і показує ранжовані результати зі сніпетами."""
    try:
        tree = parse(query)
    except QueryError as e:
        fail(f"bad query: {e}")

    index = open_or_fail(index_path)
    try:
        results = Searcher(index, engine).search(tree, make_scorer(scorer), k)
    except QueryError as e:  # напр. фраза без --positions
        fail(str(e))

    terms = set(tree.terms())
    if json_out:
        for rank, r in enumerate(results, 1):
            meta = index.doc_meta[r.doc_id]
            parts = snippet_parts(doc_text(meta), terms)
            row = {
                "rank": rank,
                "score": round(r.score, 4),
                "doc_id": r.doc_id,
                "title": r.title,
                "path": meta.path,
                "snippet": "".join(s for s, _ in parts),
            }
            # print, а не rich: рядок як є, без переносів і кольорів - щоб працював | jq
            print(json.dumps(row))
        return

    if not results:
        err.print(f"nothing found for {tree}")
        return
    table = Table(title=f"{tree}  ({scorer})", show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("score", justify="right")
    table.add_column("document", max_width=30)
    table.add_column("snippet")
    for rank, r in enumerate(results, 1):
        meta = index.doc_meta[r.doc_id]
        snippet = Text()
        for s, hl in snippet_parts(doc_text(meta), terms):
            snippet.append(s, style="bold yellow" if hl else "")
        # Text(...), а не рядок: інакше rich сприйме [дужки] в тексті як розмітку
        table.add_row(str(rank), f"{r.score:.2f}", Text(r.title.split(":")[0]), snippet)
    out.print(table)


@app.command("stats")
def stats_cmd(
    index_path: Annotated[Path, typer.Argument(help="Файл індексу")],
    top: Annotated[int, typer.Option(min=0, help="Скільки найчастіших термів")] = 20,
) -> None:
    """Статистика індексу: документи, токени, словник, найчастіші терми."""
    index = open_or_fail(index_path)
    st = index_stats(index, top)

    summary = Table(title=index_path.name, show_header=False)
    summary.add_column(style="bold")
    summary.add_column(justify="right")
    summary.add_row("books", f"{st.books:,}")
    summary.add_row("documents", f"{st.documents:,}")
    summary.add_row("tokens", f"{st.tokens:,}")
    summary.add_row("vocabulary", f"{st.vocabulary:,}")
    summary.add_row("postings", f"{st.postings:,}")
    summary.add_row("avg doc length", f"{st.avg_doc_length:.1f}")
    summary.add_row("kind / positions", f"{index.kind} / {index.positions}")
    out.print(summary)

    if st.top:
        terms = Table(title=f"top {top} terms")
        terms.add_column("#", justify="right")
        terms.add_column("term")
        terms.add_column("count", justify="right")
        for i, (term, count) in enumerate(st.top, 1):
            terms.add_row(str(i), Text(term), f"{count:,}")
        out.print(terms)


@app.command("crawl")
def crawl_cmd(
    seeds: Annotated[list[str], typer.Argument(help="Стартові URL")],
    max_pages: Annotated[int, typer.Option(min=1, help="Стеля: скільки сторінок зібрати")] = 500,
    concurrency: Annotated[int, typer.Option(min=1, help="Запитів одночасно (усього)")] = 10,
    per_host: Annotated[int, typer.Option(min=1, help="Запитів одночасно на один хост")] = 2,
    delay: Annotated[
        float, typer.Option(min=0, help="Мін. пауза між запитами до одного хоста, s")
    ] = 0.5,
    out_path: Annotated[Path, typer.Option("--out", "-o", help="JSON lines зі сторінками")] = Path(
        "data/crawl.jsonl"
    ),
    log_path: Annotated[
        Path | None, typer.Option("--log", help="Лог по кожному URL (типово crawl.log поруч)")
    ] = None,
    allow_domain: Annotated[
        list[str] | None, typer.Option(help="Дозволений домен (типово - домени seeds)")
    ] = None,
    timeout: Annotated[float, typer.Option(min=0.1, help="Таймаут одного запиту, s")] = 10.0,
    debug_loop: Annotated[
        bool, typer.Option(help="asyncio debug: попередження, якщо щось блокує цикл > 100 мс")
    ] = False,
) -> None:
    """Обходить сайт і пише сторінки в JSON lines (потім: findex index data/crawl.jsonl)."""
    if log_path is None:
        log_path = out_path.parent / "crawl.log"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = asyncio.run(
            _crawl_to_file(
                seeds,
                out_path,
                log_path,
                max_pages,
                concurrency,
                per_host,
                delay,
                allow_domain,
                timeout,
            ),
            debug=debug_loop,
        )
    except ValueError as e:
        fail(str(e))
    out.print(
        f"{stats.pages} pages -> {out_path} in {stats.elapsed:.1f} s "
        f"({stats.pages_per_s:.1f} pages/s), {stats.requests} requests, "
        f"{stats.errors} errors, {stats.skipped} skipped; log: {log_path}"
    )


def _crawl_table(st: CrawlStats) -> Table:
    table = Table(title="findex crawl", show_header=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("pages", f"{st.pages:,}")
    table.add_row("pages/s", f"{st.pages_per_s:.1f}")
    table.add_row("in flight", str(st.in_flight))
    table.add_row("queue", f"{st.queued:,}")
    table.add_row("errors", str(st.errors))
    table.add_row("skipped", str(st.skipped))
    table.add_row("elapsed", f"{st.elapsed:.1f} s")
    return table


async def _crawl_to_file(
    seeds: list[str],
    out_path: Path,
    log_path: Path,
    max_pages: int,
    concurrency: int,
    per_host: int,
    delay: float,
    allow_domain: list[str] | None,
    timeout: float,
) -> CrawlStats:
    stats = CrawlStats()
    # файли відкриваємо один раз. Запис рядка в буфер - мікросекунди, цикл не блокує
    with (
        open(out_path, "w", encoding="utf-8") as pages_f,
        open(log_path, "w", encoding="utf-8") as log_f,
        Live(get_renderable=lambda: _crawl_table(stats), console=err, refresh_per_second=4),
    ):
        log_f.write("time\tstatus\tbytes\tms\tattempts\turl\tnote\n")

        def on_result(res: FetchResult, note: str) -> None:
            if res.final_url != res.url:
                note = (note + " " if note else "") + f"-> {res.final_url}"
            when = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_f.write(
                f"{when}\t{res.status}\t{res.nbytes}\t{res.elapsed * 1000:.0f}\t"
                f"{res.attempts}\t{res.url}\t{note}\n"
            )

        pages = crawl(
            seeds,
            max_pages=max_pages,
            concurrency=concurrency,
            per_host=per_host,
            delay=delay,
            allowed_domains=allow_domain,
            timeout=timeout,
            stats=stats,
            on_result=on_result,
        )
        async for page in pages:  # сторінки йдуть потоком - у пам'яті їх немає
            pages_f.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")
            pages_f.flush()
    return stats


@app.command("serve")
def serve_cmd(
    index_path: Annotated[
        Path | None, typer.Option("--index", help="Файл індексу (інакше INDEX_PATH з оточення)")
    ] = None,
    host: Annotated[str, typer.Option(envvar="HOST", help="0.0.0.0 - доступ ззовні")] = "127.0.0.1",
    port: Annotated[int, typer.Option(envvar="PORT", min=1, max=65535)] = 8000,
    workers: Annotated[int, typer.Option(min=1, help="Процесів uvicorn (як у Lab 5)")] = 1,
    access_log: Annotated[bool, typer.Option(help="Рядок у лог на кожен запит")] = True,
) -> None:
    """Запускає веб-сервіс: API (/search, /docs/{id}, /stats, /health) і сторінку пошуку (/)."""
    import os

    import uvicorn

    from findex.web.logs import log_config
    from findex.web.settings import Settings

    if index_path is not None:
        os.environ["INDEX_PATH"] = str(index_path)  # воркери uvicorn успадкують оточення
    settings = Settings()
    if not settings.index_path.exists():
        fail(f"index file not found: {settings.index_path} (findex index data/ --positions)")
    uvicorn.run(
        "findex.web:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_config=log_config(settings.log_level, access_log),
        proxy_headers=True,  # за проксі платформи - справжній IP клієнта в логах
        forwarded_allow_ips="*",
    )

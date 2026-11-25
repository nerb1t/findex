"""Командний рядок: findex index | search | stats.

Правила:
- stdout - тільки дані (таблиця результатів або JSON), stderr - діагностика (логи, прогрес, помилки)
- логування налаштовується ОДИН раз тут, у callback; бібліотечні модулі тільки пишуть у log
- очікувана помилка (немає файлу, поганий запит) - один рядок і код виходу 1, без traceback
"""

import json
import logging
import time
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

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
        Path, typer.Argument(exists=True, file_okay=False, help="Папка з .txt файлами")
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
    files = sorted(corpus.rglob("*.txt"))[:limit]
    if not files:
        fail(f"no .txt files in {corpus}")

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

"""Паралельна індексація: map-reduce.

map:    build_partial(task) - кожен воркер сам читає свій шматок корпусу і будує
        маленький компактний індекс (PartialIndex)
reduce: merge(partials) - батьківський процес склеює їх в один Index

Воркеру передаємо тільки Task (шлях + номер частини), а не прочитаний текст:
так через pickle йде кілька десятків байтів, а не мегабайти.
Великі книги (Біблія ~4 MB) ріжемо на кілька частин по абзацах, інакше одна
книга займала б одного воркера на третину всього часу.
"""

import gc
import logging
import os
import time
from array import array
from collections import Counter, defaultdict
from collections.abc import Callable, Generator, Iterable, Sequence
from concurrent.futures import (
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from findex.corpus import corpus_files, iter_file
from findex.decorators import timed
from findex.index import Unit, iter_units
from findex.models import (
    AnyPosting,
    ArrayPostings,
    DocId,
    DocMeta,
    Index,
    Kind,
    PlainPosting,
    Posting,
)
from findex.tokenize import tokenize

log = logging.getLogger(__name__)

type ExecutorName = Literal["serial", "threads", "processes"]
EXECUTORS: Final[tuple[ExecutorName, ...]] = ("serial", "threads", "processes")

DEFAULT_CHUNK_KB: Final = 256


@dataclass(frozen=True)
class Task:
    """Що зробити воркеру: частина `part` з `parts` абзаців книги `path`."""

    path: Path
    part: int = 0
    parts: int = 1


@dataclass
class PartialIndex:
    """Індекс одного шматка. doc_id тут ЛОКАЛЬНІ (0, 1, 2...), merge їх зсуває.

    Компактно: замість мільйонів об'єктів Posting - два array('I') на терм.
    Такий об'єкт pickle пише як кілька байтових буферів, це дуже швидко.
    """

    postings: dict[str, ArrayPostings]  # term -> (локальні doc_ids, tfs)
    positions: dict[str, list[tuple[int, ...]]] | None  # term -> позиції для кожного doc
    doc_lengths: list[int]
    doc_meta: list[DocMeta]
    cpu_time: float = field(default=0.0, compare=False)  # скільки CPU з'їв воркер


def make_tasks(
    paths: Sequence[Path], unit: Unit = "para", chunk_kb: int = DEFAULT_CHUNK_KB
) -> list[Task]:
    """Ділить корпус на задачі приблизно по chunk_kb кілобайт."""
    tasks: list[Task] = []
    for path in paths:
        parts = 1
        if unit == "para" or path.suffix == ".jsonl":
            parts = max(1, round(path.stat().st_size / (chunk_kb * 1024)))
        tasks.extend(Task(path, i, parts) for i in range(parts))
    return tasks


def build_partial(
    task: Task, kind: Kind = "slots", positions: bool = False, unit: Unit = "para"
) -> PartialIndex:
    """map-крок. Функція рівня модуля, тому її можна віддати в інший процес (pickle).

    Нічого спільного з іншими воркерами: свої dict, свій Counter - гонитв немає.
    """
    start = time.thread_time()  # CPU саме цього потоку (у процесі-воркері це теж потік)
    if task.path.suffix == ".jsonl":
        # сторінки краулера: ріжемо по сторінках (рядках файлу)
        pages = list(iter_file(task.path))
        lo = len(pages) * task.part // task.parts
        hi = len(pages) * (task.part + 1) // task.parts
        units = iter_units(pages[lo:hi], unit)
    else:
        # книга: ріжемо по абзацах
        units = iter_units(iter_file(task.path), unit, task.part, task.parts)

    postings: defaultdict[str, ArrayPostings] = defaultdict(lambda: (array("I"), array("I")))
    pos_out: defaultdict[str, list[tuple[int, ...]]] = defaultdict(list)
    doc_lengths: list[int] = []
    doc_meta: list[DocMeta] = []
    for local_id, u in enumerate(units):
        if positions:
            pos: defaultdict[str, list[int]] = defaultdict(list)
            n_tokens = 0
            for i, term in enumerate(tokenize(u.text)):
                pos[term].append(i)
                n_tokens = i + 1
            for term, lst in pos.items():
                ids, tfs = postings[term]
                ids.append(local_id)
                tfs.append(len(lst))
                pos_out[term].append(tuple(lst))
        else:
            counts = Counter(tokenize(u.text))
            n_tokens = counts.total()
            for term, tf in counts.items():
                ids, tfs = postings[term]
                ids.append(local_id)
                tfs.append(tf)
        doc_lengths.append(n_tokens)
        doc_meta.append(DocMeta(u.path, u.title, u.start, u.end))

    cpu = time.thread_time() - start
    return PartialIndex(
        dict(postings), dict(pos_out) if positions else None, doc_lengths, doc_meta, cpu
    )


@contextmanager
def gc_paused() -> Generator[None]:
    """Вимикає збирач циклічного сміття на час блоку.

    merge створює ~1.5 млн об'єктів Posting, і gc кожні кілька тисяч нових
    об'єктів обходить УСІ живі об'єкти (шукає цикли, яких тут немає).
    Без нього merge на 40% швидше (1.68 s -> 1.04 s). Підрахунок посилань
    працює як і раніше, тому пам'ять звільняється нормально.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


@timed
def merge(partials: Iterable[PartialIndex], kind: Kind = "slots", positions: bool = False) -> Index:
    with gc_paused():
        return _merge(partials, kind, positions)


def _merge(partials: Iterable[PartialIndex], kind: Kind, positions: bool) -> Index:
    """reduce-крок (послідовний!). Partials мають бути в порядку корпусу.

    Кожному partial даємо діапазон doc_id [offset, offset + n). Оскільки partials
    йдуть по порядку, просто дописуємо в кінець - списки лишаються відсортованими.
    Це та частина, яку не можна розпаралелити (закон Амдала).
    """
    postings: defaultdict[str, list[AnyPosting]] = defaultdict(list)
    arrays: defaultdict[str, ArrayPostings] = defaultdict(lambda: (array("I"), array("I")))
    doc_lengths: dict[DocId, int] = {}
    doc_meta: dict[DocId, DocMeta] = {}
    paths: dict[str, str] = {}  # один об'єкт str на книгу (як у Lab 4), а не на кожну частину
    cls = PlainPosting if kind == "plain" else Posting

    offset = 0
    for part in partials:
        for term, (ids, tfs) in part.postings.items():
            if kind == "array":
                out_ids, out_tfs = arrays[term]
                out_ids.extend(d + offset for d in ids)
                out_tfs.extend(tfs)
            elif positions and part.positions is not None:
                plist = postings[term]
                for d, tf, p in zip(ids, tfs, part.positions[term], strict=True):
                    plist.append(cls(d + offset, tf, p))
            else:
                postings[term].extend(cls(d + offset, tf) for d, tf in zip(ids, tfs, strict=True))
        for i, (n, m) in enumerate(zip(part.doc_lengths, part.doc_meta, strict=True)):
            doc_lengths[offset + i] = n
            doc_meta[offset + i] = DocMeta(
                paths.setdefault(m.path, m.path), m.title, m.start, m.end
            )
        offset += len(part.doc_lengths)

    return Index(dict(postings), doc_lengths, doc_meta, kind, positions, dict(arrays))


def default_workers() -> int:
    # бенчмарк (README, Lab 5): 4-8 процесів - найкраще, 16 (усі логічні ядра) вже
    # гірше: більше процесів стартує, а реальних ядер лише 8
    return min(8, os.cpu_count() or 1)


def make_executor(name: ExecutorName, workers: int) -> Executor | None:
    if name == "threads":
        return ThreadPoolExecutor(max_workers=workers)
    if name == "processes":
        # на Windows (і macOS) процеси стартують через spawn: новий інтерпретатор
        # заново імпортує модулі. Тому все, що запускає пул, має бути під
        # if __name__ == "__main__" (у нас це точка входу findex / __main__.py)
        return ProcessPoolExecutor(max_workers=workers)
    return None


def run_tasks(
    tasks: Sequence[Task],
    pool: Executor | None,
    kind: Kind = "slots",
    positions: bool = False,
    unit: Unit = "para",
    on_done: Callable[[Task], None] | None = None,
) -> list[PartialIndex]:
    """Виконує всі задачі (у пулі або звичайним циклом), повертає результати ПО ПОРЯДКУ.

    Якщо хоч одна задача впала - скасовуємо решту і кидаємо її виняток далі.
    ProcessPoolExecutor додає до нього стек з воркера (як __cause__), тому видно,
    де саме впало. Ніяких зависань: as_completed чекає тільки на ще живі задачі.
    """
    if pool is None:
        results: list[PartialIndex] = []
        for t in tasks:
            results.append(build_partial(t, kind, positions, unit))
            if on_done is not None:
                on_done(t)
        return results

    futures: dict[Future[PartialIndex], int] = {
        pool.submit(build_partial, t, kind, positions, unit): i for i, t in enumerate(tasks)
    }
    done: dict[int, PartialIndex] = {}
    try:
        for fut in as_completed(futures):
            i = futures[fut]
            done[i] = fut.result()  # тут виникне виняток воркера, якщо він був
            if on_done is not None:
                on_done(tasks[i])
    except BaseException:
        for f in futures:
            f.cancel()  # задачі, які ще не почались, не запускати
        raise
    return [done[i] for i in range(len(tasks))]


@timed
def build_index_parallel(
    root: Path,
    executor: ExecutorName = "processes",
    workers: int | None = None,
    unit: Unit = "para",
    kind: Kind = "slots",
    positions: bool = False,
    limit: int | None = None,
    chunk_kb: int = DEFAULT_CHUNK_KB,
    on_done: Callable[[Task], None] | None = None,
) -> Index:
    """Те саме, що build_index з Lab 4, але map-крок іде в пулі."""
    if kind == "array" and positions:
        raise ValueError("positions are not supported for kind='array'")
    workers = workers or default_workers()
    paths = corpus_files(Path(root))[:limit]
    tasks = make_tasks(paths, unit, chunk_kb)
    log.info(
        "%d files -> %d tasks, executor=%s, workers=%d", len(paths), len(tasks), executor, workers
    )
    pool = make_executor(executor, workers)
    try:
        partials = run_tasks(tasks, pool, kind, positions, unit, on_done)
    finally:
        if pool is not None:
            pool.shutdown(cancel_futures=True)
    return merge(partials, kind, positions)

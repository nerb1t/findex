"""Lab 5: паралельна збірка дає ТОЙ САМИЙ індекс, що й Lab 4."""

import pickle
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findex.cli import app
from findex.index import build_index
from findex.models import Kind
from findex.parallel import (
    ExecutorName,
    PartialIndex,
    Task,
    build_index_parallel,
    build_partial,
    make_executor,
    make_tasks,
    merge,
    run_tasks,
)
from findex.store import load


@pytest.fixture(scope="module")
def big_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3 книги, одна велика (щоб її різало на кілька задач з chunk_kb=1)."""
    root = tmp_path_factory.mktemp("big")
    paras = [f"Paragraph {i} about whale ship number {i % 7} and sea." for i in range(300)]
    (root / "a_big.txt").write_text("\n\n".join(paras), encoding="utf-8")
    (root / "b.txt").write_text(
        "The cat sat.\n\n\n  The dog sat on the cat.  \n\n", encoding="utf-8"
    )
    (root / "c.txt").write_text("Python async await.\n\nevent loop", encoding="utf-8")
    return root


def same_bytes(a: object, b: object) -> bool:
    return pickle.dumps(a, protocol=5) == pickle.dumps(b, protocol=5)


def test_make_tasks(big_corpus: Path) -> None:
    paths = sorted(big_corpus.glob("*.txt"))
    tasks = make_tasks(paths, chunk_kb=1)
    big = [t for t in tasks if t.path.name == "a_big.txt"]
    assert len(big) > 5  # велику книгу порізало
    assert [t.part for t in big] == list(range(len(big)))
    assert make_tasks(paths, unit="book", chunk_kb=1) == [Task(p) for p in paths]


@pytest.mark.parametrize("positions", [False, True])
@pytest.mark.parametrize("kind", ["slots", "plain", "array"])
def test_serial_merge_equals_lab4(big_corpus: Path, kind: Kind, positions: bool) -> None:
    """merge([build_partial(усі шляхи)]) == build_index з Lab 4."""
    ref = build_index(big_corpus, kind=kind, positions=positions)
    paths = sorted(big_corpus.glob("*.txt"))
    one = merge([build_partial(Task(p), kind, positions) for p in paths], kind, positions)
    assert one == ref
    # і порізане на дрібні шматки - теж те саме, байт у байт
    many = build_index_parallel(big_corpus, "serial", kind=kind, positions=positions, chunk_kb=1)
    assert many == ref
    assert same_bytes(many, ref)


def test_book_unit(big_corpus: Path) -> None:
    ref = build_index(big_corpus, unit="book")
    assert build_index_parallel(big_corpus, "threads", 2, unit="book") == ref


@pytest.mark.parametrize("executor", ["threads", pytest.param("processes", marks=pytest.mark.slow)])
def test_parallel_byte_identical(big_corpus: Path, executor: ExecutorName) -> None:
    ref = build_index(big_corpus, positions=True)
    ix = build_index_parallel(big_corpus, executor, 4, positions=True, chunk_kb=1)
    assert same_bytes(ix, ref)


def test_partial_is_compact_and_picklable(big_corpus: Path) -> None:
    part = build_partial(Task(big_corpus / "c.txt"))
    assert part.doc_lengths == [3, 2]
    assert list(part.postings["event"][0]) == [1]  # локальний doc_id
    assert part.cpu_time >= 0
    again = pickle.loads(pickle.dumps(part))
    assert isinstance(again, PartialIndex) and again == part
    pickle.dumps(build_partial)  # функція рівня модуля - pickle пише її як ім'я


def test_limit(big_corpus: Path) -> None:
    assert build_index_parallel(big_corpus, "serial", limit=1).num_docs == 300


def test_on_done_called_for_every_task(big_corpus: Path) -> None:
    seen: list[Task] = []
    build_index_parallel(big_corpus, "threads", 3, chunk_kb=1, on_done=seen.append)
    paths = sorted(big_corpus.glob("*.txt"))
    assert sorted(seen, key=lambda t: (t.path, t.part)) == make_tasks(paths, chunk_kb=1)


@pytest.mark.parametrize(
    "executor", ["serial", "threads", pytest.param("processes", marks=pytest.mark.slow)]
)
def test_worker_exception_surfaces(tmp_path: Path, executor: ExecutorName) -> None:
    """Воркер упав -> вся збірка падає з його винятком (а не зависає)."""
    tasks = [Task(tmp_path / "missing.txt")]
    pool = make_executor(executor, 2)
    try:
        with pytest.raises(FileNotFoundError) as info:
            run_tasks(tasks, pool)
    finally:
        if pool is not None:
            pool.shutdown()
    if executor == "processes":
        # ProcessPoolExecutor чіпляє стек з воркера як __cause__
        assert "build_partial" in str(info.value.__cause__)


def test_array_positions_parallel(big_corpus: Path) -> None:
    ref = build_index(big_corpus, kind="array", positions=True)
    assert build_index_parallel(big_corpus, "threads", 3, kind="array", positions=True) == ref


@pytest.mark.parametrize(
    "executor", ["serial", "threads", pytest.param("processes", marks=pytest.mark.slow)]
)
def test_cli_executors(big_corpus: Path, tmp_path: Path, executor: str) -> None:
    out = tmp_path / "ix.bin"
    args = ["index", str(big_corpus), "-o", str(out), "--executor", executor, "--workers", "2"]
    result = CliRunner().invoke(app, [*args, "--chunk-kb", "1"])
    assert result.exit_code == 0, result.output
    assert load(out) == build_index(big_corpus)


def test_cli_bad_executor(big_corpus: Path, tmp_path: Path) -> None:
    args = ["index", str(big_corpus), "-o", str(tmp_path / "x.bin"), "--executor", "gpu"]
    assert CliRunner().invoke(app, args).exit_code == 2

"""CLI через typer.testing.CliRunner - як справжній запуск, але в тому ж процесі."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findex.cli import app

runner = CliRunner()


@pytest.fixture(scope="module")
def ix_file(corpus: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cli") / "ix.bin"
    result = runner.invoke(app, ["index", str(corpus), "--out", str(path), "--positions"])
    assert result.exit_code == 0, result.output
    return path


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("index", "search", "stats"):
        assert cmd in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("findex 0.7")


def test_index_cmd(ix_file: Path) -> None:
    assert ix_file.stat().st_size > 0


def test_index_json_and_limit(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "ix.json"
    result = runner.invoke(app, ["index", str(corpus), "--out", str(path), "--limit", "1"])
    assert result.exit_code == 0
    assert "docs=2" in result.stdout  # тільки a.txt (2 абзаци)


def test_index_array_with_positions(corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "x.bin"
    result = runner.invoke(
        app, ["index", str(corpus), "-o", str(out), "--kind", "array", "--positions"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["search", str(out), '"event loop"', "--json"])
    assert result.exit_code == 0 and len(result.stdout.splitlines()) == 2


def test_index_empty_dir(tmp_path: Path) -> None:
    result = runner.invoke(app, ["index", str(tmp_path), "-o", str(tmp_path / "x.bin")])
    assert result.exit_code == 1
    assert "no .txt/.jsonl files" in result.stderr


def test_search_table(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), "python async", "-k", "3"])
    assert result.exit_code == 0, result.output
    assert "b.txt #1" in result.stdout  # другий абзац b.txt
    assert "python" in result.stdout.lower()


def test_search_json(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), '"event loop"', "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["doc_id"] for r in rows] == [0, 1]  # однакові бали -> менший doc_id перший
    assert rows[0]["rank"] == 1 and "event loop" in rows[0]["snippet"]
    assert result.stderr == ""  # у stdout тільки JSON, в stderr нічого


def test_search_tfidf_set_engine(ix_file: Path) -> None:
    args = ["search", str(ix_file), "loop NOT java", "--json", "--scorer", "tfidf"]
    result = runner.invoke(app, [*args, "--engine", "set"])
    assert result.exit_code == 0
    assert {json.loads(x)["doc_id"] for x in result.stdout.splitlines()} == {0, 1}


def test_search_nothing(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), "ruby"])
    assert result.exit_code == 0
    assert "nothing found" in result.stderr


def test_search_missing_index(tmp_path: Path) -> None:
    result = runner.invoke(app, ["search", str(tmp_path / "nope.bin"), "x"])
    assert result.exit_code == 1
    assert result.stderr.strip() == f"error: index file not found: {tmp_path / 'nope.bin'}"
    assert "Traceback" not in result.output


def test_search_bad_index_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a pickle")
    result = runner.invoke(app, ["search", str(bad), "x"])
    assert result.exit_code == 1
    assert "is not a findex index" in result.stderr


def test_search_bad_query(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), "(python"])
    assert result.exit_code == 1
    assert "bad query" in result.stderr


def test_search_phrase_without_positions(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "nopos.bin"
    runner.invoke(app, ["index", str(corpus), "-o", str(path)])
    result = runner.invoke(app, ["search", str(path), '"event loop"'])
    assert result.exit_code == 1
    assert "--positions" in result.stderr


def test_search_bad_scorer(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), "x", "--scorer", "magic"])
    assert result.exit_code == 2  # помилка використання - typer сам дає код 2


def test_stats(ix_file: Path) -> None:
    result = runner.invoke(app, ["stats", str(ix_file), "--top", "3"])
    assert result.exit_code == 0, result.output
    assert "documents" in result.stdout and "vocabulary" in result.stdout
    assert "loop" in result.stdout  # найчастіший терм (3 рази)


def test_verbose_logs_go_to_stderr(ix_file: Path) -> None:
    result = runner.invoke(app, ["-vv", "search", str(ix_file), "python", "--json"])
    assert result.exit_code == 0
    assert "cache miss" in result.stderr  # INFO
    assert "took" in result.stderr  # DEBUG від @timed
    assert "took" not in result.stdout
    for line in result.stdout.splitlines():
        json.loads(line)  # stdout все ще чистий JSON


def test_quiet_by_default(ix_file: Path) -> None:
    result = runner.invoke(app, ["search", str(ix_file), "python", "--json"])
    assert "cache miss" not in result.stderr


def test_verbose_info_only(ix_file: Path) -> None:
    result = runner.invoke(app, ["-v", "search", str(ix_file), "python", "--json"])
    assert "cache miss" in result.stderr  # INFO є
    assert "took" not in result.stderr  # а DEBUG ні

import inspect
import logging
from pathlib import Path

import pytest

from findex.corpus import iter_documents
from findex.tokenize import normalize, tokenize


def toks(text: str) -> list[str]:
    return list(tokenize(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello WORLD hello", ["hello", "world", "hello"]),  # регістр
        ("Straße", ["strasse"]),  # casefold, а не lower
        ("Привіт, Світе!", ["привіт", "світе"]),  # кирилиця
        ("one,two...three!", ["one", "two", "three"]),  # пунктуація
        ("", []),
        ("   \n\t ", []),
        ("don't п'ять п’ять пʼять", ["don't", "п'ять", "п'ять", "п'ять"]),  # апострофи
        ("'quoted'", ["quoted"]),  # апостроф на краю - не частина слова
        ("well-known -- a", ["well-known", "a"]),  # дефіс
        ("mp3 in 2024", ["mp", "3", "in", "2024"]),  # цифри окремо
        ("3.14", ["3", "14"]),
        ("snake_case", ["snake", "case"]),  # _ - розділювач
        ("ΣΊΣΥΦΟΣ", ["σίσυφοσ"]),  # грецька: casefold робить σ і в кінці
        ("日本語 text", ["日本語", "text"]),  # ієрогліфи - теж літери
        ("naïve café", ["naïve", "café"]),
    ],
)
def test_tokenize(text: str, expected: list[str]) -> None:
    assert toks(text) == expected


def test_combining_accent() -> None:
    # e + комбінований наголос і готове é дають один токен (завдяки NFC)
    assert toks("café") == toks("café") == ["café"]


def test_normalize_keeps_case() -> None:
    assert normalize("Don’t") == "Don't"


def test_generators() -> None:
    assert inspect.isgeneratorfunction(tokenize)
    assert inspect.isgeneratorfunction(iter_documents)


def test_bad_utf8_is_replaced(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # у двох книгах корпусу є биті байти - читаємо з заміною і пишемо warning
    (tmp_path / "bad.txt").write_bytes(b"caf\xe9 ok")
    with caplog.at_level(logging.WARNING):
        docs = list(iter_documents(tmp_path))
    assert docs[0].text == "caf� ok"
    assert "bad utf-8" in caplog.text

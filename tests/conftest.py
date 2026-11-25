"""Спільні фікстури для всіх тестів."""

from pathlib import Path

import pytest

from findex.index import build_index
from findex.models import Index

DATA = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Маленький корпус: 2 книги, 4 абзаци. scope="session" - створюється один раз."""
    root = tmp_path_factory.mktemp("corpus")
    (root / "a.txt").write_text(
        "The event loop runs async code.\n\nPython has an event loop too.", encoding="utf-8"
    )
    (root / "b.txt").write_text(
        "Java has no loop event here.\n\nAwait the python async task.", encoding="utf-8"
    )
    return root


@pytest.fixture(scope="session")
def index(corpus: Path) -> Index:
    """Індекс з позиціями. Спільний для всіх тестів, тому тести НЕ мають його змінювати
    (наприклад, викликати close()) - інакше наступні тести зламаються."""
    return build_index(corpus, positions=True)


@pytest.fixture(scope="session")
def real_index() -> Index:
    """Індекс на 3 книгах справжнього корпусу (data/ не в git, тому може не бути)."""
    if not (DATA / "gutenberg").exists():
        pytest.skip("no data/ (run scripts/get_corpus.py)")
    return build_index(DATA / "gutenberg", positions=True, limit=3)

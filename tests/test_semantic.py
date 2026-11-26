# Як і в test_web.py: TestClient Starlette описаний через httpx2, якого немає,
# тому pyright не знає типів відповіді - вимикаємо лише перевірки "Unknown":
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Lab 8, M3: семантичний і гібридний пошук. Справжньої моделі в тестах немає
(CI без мережі й без 90 MB моделі) - замість неї детермінований "ембедер":
кожне слово -> фіксований випадковий вектор, синоніми -> той самий вектор."""

import zlib
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from findex.cli import app as cli_app
from findex.models import Index
from findex.query import QueryError
from findex.search import Searcher
from findex.semantic import (
    FloatMatrix,
    SemanticIndex,
    chunk_text,
    default_embeddings_path,
    embed_index,
    rrf,
    save_embeddings,
    search_any,
    semantic_text,
)
from findex.store import save
from findex.web import create_app
from findex.web.settings import Settings
from findex.web.state import AppState, Engine, get_app_state

# "синоніми": різні слова з однаковим змістом -> один вектор
SYNONYMS = {"serpent": "python", "snake": "python", "coroutine": "async", "java": "coffee"}


class FakeEmbedder:
    name = "fake"

    def __call__(self, texts: Sequence[str]) -> FloatMatrix:
        out = np.zeros((len(texts), 16), np.float32)
        for i, text in enumerate(texts):
            for w in text.lower().replace(".", " ").split():
                w = SYNONYMS.get(w, w)
                rnd = np.random.default_rng(zlib.crc32(w.encode()))
                out[i] += rnd.standard_normal(16).astype(np.float32)
        return out


@pytest.fixture(scope="module")
def sem(index: Index) -> SemanticIndex:
    vectors, owner = embed_index(index, FakeEmbedder(), batch=3)
    return SemanticIndex(vectors, owner, "fake", FakeEmbedder())


# docs (conftest): 0 "The event loop runs async code."  1 "Python has an event loop too."
#                  2 "Java has no loop event here."      3 "Await the python async task."


def test_chunk_text() -> None:
    assert chunk_text("") == []
    assert chunk_text("a b c d e", words=2) == ["a b", "c d", "e"]


def test_semantic_text() -> None:
    assert semantic_text('(whale OR "white ship") NOT sea') == "whale white ship sea"


def test_rrf() -> None:
    # d1: 1/61 + 1/62, d2: 1/62 + 1/61 -> рівні, менший doc_id перший; d3 лише в одному
    fused = rrf([[1, 2, 3], [2, 1]])
    assert [d for d, _ in fused] == [1, 2, 3]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2][1] == pytest.approx(1 / 63)
    assert rrf([]) == []


def test_embed_index_keeps_order(index: Index, sem: SemanticIndex) -> None:
    # сортування за довжиною всередині embed_index не повинно переплутати рядки
    assert sem.owner.tolist() == [0, 1, 2, 3]
    direct = FakeEmbedder()(["Python has an event loop too."])[0]
    direct /= np.linalg.norm(direct)
    assert np.allclose(sem.vectors[1], direct, atol=1e-6)
    assert np.allclose(np.linalg.norm(sem.vectors, axis=1), 1.0, atol=1e-5)


def test_semantic_finds_synonym(sem: SemanticIndex) -> None:
    # у тексті немає слова "serpent", але "ембедер" вважає його синонімом python
    top = sem.search("serpent", k=2)
    assert {d for d, _ in top} == {1, 3}
    assert all(-1.0 <= s <= 1.0 for _, s in top)


def test_best_chunk_per_document() -> None:
    e = np.array([[1, 0], [0, 1], [0.6, 0.8], [0, 1]], np.float32)
    owner = np.array([0, 0, 1, 2], np.int32)

    class Q:
        def __call__(self, texts: Sequence[str]) -> FloatMatrix:
            return np.array([[0.0, 1.0]], np.float32)

    s = SemanticIndex(e, owner, "m", Q())
    # doc 0: max(0, 1) = 1; doc 2: 1; doc 1: 0.8
    assert s.search("x", k=3) == [(0, 1.0), (2, 1.0), (1, pytest.approx(0.8))]


def test_save_load_int8(sem: SemanticIndex, tmp_path: Path) -> None:
    path = tmp_path / "e.npz"
    save_embeddings(path, sem.vectors, sem.owner, "fake")
    loaded = SemanticIndex.load(path)
    assert loaded.model == "fake" and loaded.owner.tolist() == sem.owner.tolist()
    # int8 замість float32: косинуси майже ті самі
    assert np.abs(loaded.vectors @ sem.vectors.T - sem.vectors @ sem.vectors.T).max() < 0.02
    assert default_embeddings_path(Path("ix.bin")) == Path("ix.emb.npz")


def test_search_any_modes(index: Index, sem: SemanticIndex) -> None:
    searcher = Searcher(index)
    assert search_any(searcher, sem, "python", "keyword") == searcher.search("python")
    hybrid = search_any(searcher, sem, "python async", "hybrid", k=4)
    assert hybrid[0].doc_id == 3  # перший і в BM25, і в семантиці
    # запит, який не розбирається як булевий, у hybrid не падає
    assert search_any(searcher, sem, "(python", "hybrid", k=2)
    with pytest.raises(QueryError):
        search_any(searcher, None, "python", "semantic")


@pytest.fixture
def client(index: Index, sem: SemanticIndex) -> Iterator[TestClient]:
    app = create_app(Settings(index_path=Path("no/such.bin")))
    engine = Engine(index)
    engine.semantic = sem
    app.dependency_overrides[get_app_state] = lambda: AppState(engine=engine)
    with TestClient(app) as c:
        yield c


def test_api_modes(client: TestClient) -> None:
    r = client.get("/search", params={"q": "serpent", "mode": "semantic", "k": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "semantic" and {h["doc_id"] for h in body["results"]} == {1, 3}
    r = client.get("/search", params={"q": "python", "mode": "hybrid"})
    assert r.status_code == 200 and r.json()["results"]
    assert client.get("/search", params={"q": "x", "mode": "magic"}).status_code == 422


def test_api_semantic_unavailable(index: Index) -> None:
    app = create_app(Settings(index_path=Path("no/such.bin")))
    app.dependency_overrides[get_app_state] = lambda: AppState(engine=Engine(index))
    with TestClient(app) as c:
        r = c.get("/search", params={"q": "python", "mode": "semantic"})
    assert r.status_code == 400 and "not available" in r.json()["detail"]


def test_ui_mode_select(client: TestClient) -> None:
    r = client.get("/", params={"q": "serpent", "mode": "semantic"})
    assert r.status_code == 200
    assert '<option value="semantic" selected>' in r.text and "за змістом" in r.text


def test_cli_semantic_without_embeddings(index: Index, tmp_path: Path) -> None:
    path = tmp_path / "ix.bin"
    save(index, path)
    result = CliRunner().invoke(cli_app, ["search", str(path), "python", "--mode", "semantic"])
    assert result.exit_code == 1 and "embeddings not found" in result.stderr

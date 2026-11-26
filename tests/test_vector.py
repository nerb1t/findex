"""Lab 8, M2: NumPy-скорер дає ТОЙ САМИЙ рейтинг, що й цикл з Lab 3."""

import random

import numpy as np
import pytest

from findex.index import build_index
from findex.models import Index
from findex.scoring import BM25, Scorer, TfIdf
from findex.search import Searcher
from findex.vector import VectorPostings, top_k


@pytest.fixture(scope="module")
def zipf_index(tmp_path_factory: pytest.TempPathFactory) -> Index:
    rnd = random.Random(7)
    vocab = ["the", "and", "whale", "white", "ship", "sea"] + [f"w{i}" for i in range(500)]
    weights = [1 / (r + 1) for r in range(len(vocab))]
    root = tmp_path_factory.mktemp("zipf")
    for book in ("a", "b"):
        paras = [" ".join(rnd.choices(vocab, weights, k=rnd.randint(3, 40))) for _ in range(400)]
        (root / f"{book}.txt").write_text("\n\n".join(paras), encoding="utf-8")
    return build_index(root, positions=True)


QUERIES = [
    "the",
    "whale",
    "w499",
    "the white whale",
    "white OR ship OR sea",
    "(whale OR ship) NOT the",
    '"white whale"',
    "NOT whale",
    "nosuchword",
    "the OR nosuchword",
]


@pytest.mark.parametrize("scorer", [BM25(), TfIdf(), BM25(k1=1.2, b=0.3)])
@pytest.mark.parametrize("k", [1, 5, 10, 1000])
def test_same_ranking_as_lab3(zipf_index: Index, scorer: Scorer, k: int) -> None:
    fast = Searcher(zipf_index, vectorized=True)
    slow = Searcher(zipf_index, vectorized=False)
    for q in QUERIES:
        a = fast.search(q, scorer, k)
        b = slow.search(q, scorer, k)
        assert [r.doc_id for r in a] == [r.doc_id for r in b], q
        assert [r.score for r in a] == [r.score for r in b], q  # точно, не approx


def test_same_on_fixture_corpus(index: Index) -> None:
    for q in ["python async", "loop", "python OR async", '"event loop"']:
        a = Searcher(index).search(q, k=10)
        b = Searcher(index, vectorized=False).search(q, k=10)
        assert a == b


def test_custom_scorer_falls_back_to_python(index: Index) -> None:
    def ones(term: str, posting: object, ix: Index) -> float:
        return 1.0

    res = Searcher(index).search("python OR async", ones, k=10)  # type: ignore[arg-type]
    assert [r.score for r in res] == [2.0, 1.0, 1.0]


def test_vector_postings_layout(index: Index) -> None:
    vp = VectorPostings.from_index(index)
    ids, tfs = vp.postings("loop")
    assert ids.dtype == np.int32 and tfs.dtype == np.int32
    assert ids.tolist() == [0, 1, 2] and tfs.tolist() == [1, 1, 1]
    assert vp.postings("nosuchword")[0].size == 0
    assert vp.doc_len.tolist() == [index.doc_length(d) for d in range(4)]
    assert np.shares_memory(ids, vp.ids)  # зріз - view, без копії


def test_top_k_ties_and_edges() -> None:
    scores = np.array([1.0, 3.0, 3.0, 2.0, 3.0], np.float64)
    cands = np.arange(5, dtype=np.int32)
    # три рівні бали 3.0 на межі k=2: перемагають менші doc_id
    assert top_k(cands, scores, 2) == [(1, 3.0), (2, 3.0)]
    assert top_k(cands, scores, 10) == [(1, 3.0), (2, 3.0), (4, 3.0), (3, 2.0), (0, 1.0)]
    assert top_k(np.array([], np.int32), scores, 3) == []
    assert top_k(cands, scores, 0) == []

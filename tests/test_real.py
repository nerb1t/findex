"""Перевірки Lab 3 на справжньому корпусі (3 книги). Довгі -> slow, без data/ -> skip."""

import pytest

from findex.models import Index, Posting
from findex.scoring import BM25, TfIdf
from findex.search import Searcher

pytestmark = pytest.mark.slow


def test_rare_beats_common_real(real_index: Index) -> None:
    """Документи тільки з рідким термом вище за всі документи тільки з частим."""
    rare = "harriet"
    assert real_index.df(rare) < real_index.df("the") / 10
    results = Searcher(real_index).search(f"the OR {rare}", k=100_000)
    has_rare = set(real_index.doc_ids(rare))
    has_the = set(real_index.doc_ids("the"))
    only_rare = [r.score for r in results if r.doc_id in has_rare - has_the]
    only_the = [r.score for r in results if r.doc_id in has_the - has_rare]
    assert min(only_rare) > max(only_the)


def test_saturation_real(real_index: Index) -> None:
    bm, ti = BM25(), TfIdf()
    doc = 0
    gain_1 = bm("emma", Posting(doc, 1), real_index)
    gain_20 = bm("emma", Posting(doc, 20), real_index) - bm("emma", Posting(doc, 19), real_index)
    assert gain_20 < gain_1 * 0.05
    tf_gain = ti("emma", Posting(doc, 20), real_index) - ti("emma", Posting(doc, 19), real_index)
    assert tf_gain == pytest.approx(ti("emma", Posting(doc, 1), real_index))


def test_short_beats_long_real(real_index: Index) -> None:
    ones = [p for p in real_index["emma"] if p.tf == 1]
    short = min(ones, key=lambda p: real_index.doc_length(p.doc_id))
    long = max(ones, key=lambda p: real_index.doc_length(p.doc_id))
    assert real_index.doc_length(long.doc_id) > 10 * real_index.doc_length(short.doc_id)
    bm = BM25()
    assert bm("emma", short, real_index) > bm("emma", long, real_index)
    ti = TfIdf()
    assert ti("emma", short, real_index) == ti("emma", long, real_index)


def test_phrase_real(real_index: Index) -> None:
    results = Searcher(real_index).search('"miss woodhouse"', k=5)
    assert len(results) == 5

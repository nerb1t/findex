import random
from array import array

import pytest

from findex.index import build_index
from findex.models import DocMeta, Posting
from findex.search import (
    merge_and,
    merge_not,
    merge_or,
    parse_query,
    search,
    set_and,
    set_not,
    set_or,
)
from findex.store import load, save


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "a.txt").write_text("the cat sat\n\nthe dog sat on the cat", encoding="utf-8")
    (tmp_path / "b.txt").write_text("a dog and a bird", encoding="utf-8")
    return tmp_path


def test_hashable():
    s = {Posting(1, 2), Posting(1, 2), Posting(3, 1)}
    assert len(s) == 2
    assert len({DocMeta("a", "b"), DocMeta("a", "b")}) == 1


def test_frozen():
    p = Posting(1, 2)
    with pytest.raises(AttributeError):
        p.tf = 5


def test_slots():
    assert not hasattr(Posting(1, 2), "__dict__")


def test_build_para(corpus):
    index = build_index(corpus, unit="para")
    # 3 абзаци: "the cat sat", "the dog sat on the cat", "a dog and a bird"
    assert len(index.doc_meta) == 3
    assert index.postings["the"] == [Posting(0, 1), Posting(1, 2)]
    assert index.doc_ids("dog") == [1, 2]
    assert index.doc_lengths == {0: 3, 1: 6, 2: 5}
    assert index.doc_ids("nothing") == []


def test_build_book(corpus):
    index = build_index(corpus, unit="book")
    assert len(index.doc_meta) == 2
    assert index.doc_ids("cat") == [0]
    assert index.postings["cat"][0].tf == 2


def test_sorted(corpus):
    index = build_index(corpus)
    for plist in index.postings.values():
        ids = [p.doc_id for p in plist]
        assert ids == sorted(ids)


def test_positions(corpus):
    index = build_index(corpus, positions=True)
    assert index.postings["the"][1] == Posting(1, 2, (0, 4))


def test_array_same_as_slots(corpus):
    a = build_index(corpus, kind="array")
    b = build_index(corpus, kind="slots")
    assert isinstance(a.postings["the"][0], array)
    for term in b.postings:
        assert list(a.doc_ids(term)) == b.doc_ids(term)


def test_merge_vs_set():
    rnd = random.Random(1)
    for _ in range(200):
        a = sorted(rnd.sample(range(100), rnd.randint(0, 30)))
        b = sorted(rnd.sample(range(100), rnd.randint(0, 30)))
        assert merge_and(a, b) == set_and(a, b)
        assert merge_or(a, b) == set_or(a, b)
        assert merge_not(a, b) == set_not(a, b)


def test_parse_query():
    assert parse_query("Cat dog OR bird NOT fish") == [
        ("AND", "cat"),
        ("AND", "dog"),
        ("OR", "bird"),
        ("NOT", "fish"),
    ]


@pytest.mark.parametrize("engine", ["merge", "set"])
def test_search(corpus, engine):
    index = build_index(corpus)
    assert search(index, "dog", engine) == [1, 2]
    assert search(index, "the dog", engine) == [1]
    assert search(index, "cat OR bird", engine) == [0, 1, 2]
    assert search(index, "dog NOT cat", engine) == [2]
    assert search(index, "NOT dog", engine) == [0]
    assert search(index, "unicorn", engine) == []
    assert search(index, "", engine) == []


@pytest.mark.parametrize("kind", ["plain", "slots", "array"])
@pytest.mark.parametrize("ext", [".bin", ".json"])
def test_save_load(corpus, tmp_path, kind, ext):
    index = build_index(corpus, kind=kind)
    path = tmp_path / ("index" + ext)
    save(index, path)
    loaded = load(path)
    assert loaded == index


@pytest.mark.parametrize("ext", [".bin", ".json"])
def test_save_load_positions(corpus, tmp_path, ext):
    index = build_index(corpus, positions=True)
    path = tmp_path / ("index" + ext)
    save(index, path)
    assert load(path) == index

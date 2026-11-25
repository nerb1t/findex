"""Lab 3: Index як об'єкт, парсер, скорери, кеш, сніпети."""

import logging
from pathlib import Path

import pytest

from findex.decorators import timed
from findex.index import build_index
from findex.models import AnyPosting, DocMeta, Index, Posting
from findex.query import And, Not, Or, Phrase, QueryError, Term, parse
from findex.scoring import BM25, Scorer, TfIdf
from findex.search import Searcher, SearchResult
from findex.snippet import doc_text, make_snippet, snippet_parts
from findex.store import open_index, save

# docs у фікстурі index (conftest.py):
#   0 "the event loop runs async code"   1 "python has an event loop too"
#   2 "java has no loop event here"      3 "await the python async task"

# ---------- Index як об'єкт ----------


def test_index_dunders(index: Index) -> None:
    assert len(index) == len(index.postings)
    assert "python" in index
    assert "ruby" not in index
    assert index["python"] == [Posting(1, 1, (0,)), Posting(3, 1, (2,))]
    with pytest.raises(KeyError):
        index["ruby"]
    assert set(index) == set(index.postings)
    assert repr(index) == f"Index(terms={len(index)}, docs=4, kind='slots')"
    # це дає Mapping безкоштовно
    assert index.get("ruby") is None
    assert dict(index.items())["loop"] == index["loop"]


def test_index_properties(index: Index) -> None:
    assert index.num_docs == 4
    assert index.doc_length(0) == 6
    assert index.df("loop") == 3
    assert index.df("ruby") == 0
    assert index.avg_doc_length == sum(index.doc_lengths.values()) / 4
    assert "avg_doc_length" in index.__dict__  # cached_property зберіг значення


def test_index_eq_other_type(index: Index) -> None:
    assert index.__eq__(42) is NotImplemented
    assert index != 42


def test_array_index_getitem(corpus: Path) -> None:
    ix = build_index(corpus, kind="array")
    assert ix["python"] == [Posting(1, 1), Posting(3, 1)]
    assert ix.df("python") == 2 and ix.df("ruby") == 0


def test_open_index(index: Index, tmp_path: Path) -> None:
    path = tmp_path / "ix.bin"
    save(index, path)
    with open_index(path) as ix:
        assert len(ix) == len(index)
    assert ix.closed and len(ix) == 0
    assert not index.closed  # спільна фікстура не зачеплена


def test_open_index_exception(index: Index, tmp_path: Path) -> None:
    path = tmp_path / "ix.bin"
    save(index, path)
    with pytest.raises(RuntimeError), open_index(path) as ix:
        raise RuntimeError("boom")
    assert ix.closed  # закрився навіть з винятком


# ---------- парсер ----------


def test_parse_trees() -> None:
    a, b, c = Term("a"), Term("b"), Term("c")
    assert parse("a") == a
    assert parse("a b") == And(a, b)
    assert parse("a AND b") == And(a, b)
    assert parse("a OR b c") == Or(a, And(b, c))
    assert parse("(a OR b) c") == And(Or(a, b), c)
    assert parse("a NOT b") == And(a, Not(b))
    assert parse("NOT NOT a") == Not(Not(a))
    assert parse("a b c") == And(And(a, b), c)
    assert parse('"Event Loop" a') == And(Phrase(("event", "loop")), a)
    assert parse('"a"') == a
    assert parse("mp3") == And(Term("mp"), Term("3"))


def test_parse_big() -> None:
    tree = parse('python AND (async OR await) NOT java "event loop"')
    expected = And(
        And(
            And(Term("python"), Or(Term("async"), Term("await"))),
            Not(Term("java")),
        ),
        Phrase(("event", "loop")),
    )
    assert tree == expected


def test_operators() -> None:
    a, b, c = Term("a"), Term("b"), Term("c")
    assert a & (b | c) & ~Term("d") == parse("a (b OR c) NOT d")


def test_lowercase_or_is_word() -> None:
    assert parse("a or b") == And(And(Term("a"), Term("or")), Term("b"))


@pytest.mark.parametrize("bad", ["", "(a", "a )", "OR", "a OR", "NOT", "---", "()"])
def test_parse_errors(bad: str) -> None:
    with pytest.raises(QueryError):
        parse(bad)


def test_trees_hashable() -> None:
    assert hash(parse("a OR b c")) == hash(Or(Term("a"), And(Term("b"), Term("c"))))


def test_terms() -> None:
    assert parse('a OR "b c" NOT d').terms() == ("a", "b", "c")


def test_str() -> None:
    assert str(parse('a OR "b c" NOT d')) == '(a OR ("b c" AND NOT d))'


# ---------- evaluate ----------


def test_evaluate(index: Index) -> None:
    assert parse("loop").evaluate(index) == [0, 1, 2]
    assert parse("python AND (async OR await)").evaluate(index) == [3]
    assert parse("loop NOT java").evaluate(index) == [0, 1]
    assert parse('"event loop"').evaluate(index) == [0, 1]  # в 2 "loop event" - не фраза
    assert parse('"loop event"').evaluate(index) == [2]
    assert parse('"event loop" NOT python').evaluate(index) == [0]
    assert parse('"event ruby"').evaluate(index) == []


def test_phrase_needs_positions(corpus: Path) -> None:
    ix = build_index(corpus)
    with pytest.raises(QueryError):
        parse('"event loop"').evaluate(ix)


# ---------- ранжування ----------


def test_scorer_protocol() -> None:
    def my_scorer(term: str, posting: AnyPosting, index: Index) -> float:
        return 1.0

    # Protocol не потребує наслідування: і функція, і класи підходять (перевіряє pyright)
    scorers: list[Scorer] = [TfIdf(), BM25(), my_scorer]
    assert all(callable(s) for s in scorers)
    assert repr(BM25(k1=1.2)) == "BM25(k1=1.2, b=0.75)"


def make_index(doc_lengths: dict[int, int], postings: dict[str, list[AnyPosting]]) -> Index:
    meta = {d: DocMeta("x", "x") for d in doc_lengths}
    return Index(postings, doc_lengths, meta)


# три перевірки з Lab 3 (на справжньому корпусі вони ще в test_real.py)


def test_rare_beats_common() -> None:
    # "common" в 9 з 10 документів, "rare" - в 1
    lengths = dict.fromkeys(range(10), 10)
    common: list[AnyPosting] = [Posting(d, 1) for d in range(9)]
    ix = make_index(lengths, {"common": common, "rare": [Posting(9, 1)]})
    scorers: list[Scorer] = [TfIdf(), BM25()]
    for s in scorers:
        assert s("rare", Posting(9, 1), ix) > s("common", Posting(0, 1), ix)


def test_bm25_saturation() -> None:
    ix = make_index(dict.fromkeys(range(10), 10), {"t": [Posting(0, 1)]})
    bm = BM25()
    gain_2nd = bm("t", Posting(0, 2), ix) - bm("t", Posting(0, 1), ix)
    gain_20th = bm("t", Posting(0, 20), ix) - bm("t", Posting(0, 19), ix)
    assert gain_20th < gain_2nd / 20
    # а в TF-IDF кожен повтор дає однаково
    ti = TfIdf()
    assert ti("t", Posting(0, 20), ix) == pytest.approx(20 * ti("t", Posting(0, 1), ix))


def test_short_beats_long() -> None:
    ix = make_index({0: 5, 1: 500, 2: 50}, {"t": [Posting(0, 1), Posting(1, 1)]})
    bm = BM25()
    assert bm("t", Posting(0, 1), ix) > bm("t", Posting(1, 1), ix)
    # з b=0 довжина не важлива
    assert BM25(b=0)("t", Posting(0, 1), ix) == BM25(b=0)("t", Posting(1, 1), ix)


def test_search_ranked(index: Index) -> None:
    s = Searcher(index)
    results = s.search("python OR async", k=10)
    assert {r.doc_id for r in results} == {0, 1, 3}
    assert results[0].doc_id == 3  # там і python, і async
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert len(s.search("python OR async", k=2)) == 2
    assert s.search("NOT loop", k=10)[0].score == 0.0  # тільки NOT - балів немає


def test_search_result_order() -> None:
    a = SearchResult(1.0, 5, "a")
    b = SearchResult(2.0, 1, "b")
    assert sorted([b, a]) == [a, b]
    assert max(a, b) is b


def test_cache_hit(index: Index, caplog: pytest.LogCaptureFixture) -> None:
    s = Searcher(index)
    with caplog.at_level(logging.INFO):
        s.search("python async")
        s.search("python   async")  # те саме дерево -> hit
    assert s.match.cache_info().hits == 1
    assert "cache miss" in caplog.text and "cache hit" in caplog.text


# ---------- декоратор ----------


def test_timed_wraps(caplog: pytest.LogCaptureFixture) -> None:
    @timed
    def add(a: int, b: int) -> int:
        """Adds."""
        return a + b

    assert add.__name__ == "add"
    assert add.__doc__ == "Adds."
    with caplog.at_level(logging.DEBUG):
        assert add(1, 2) == 3
    assert "add took" in caplog.text


# ---------- сніпети ----------


def test_snippet_highlight() -> None:
    s = make_snippet("Python has an Event loop too.", {"event", "loop"})
    assert s == "Python has an **Event** **loop** too."


def test_snippet_parts() -> None:
    assert snippet_parts("a  Whale\nb", {"whale"}) == [
        ("a ", False),
        ("Whale", True),
        (" b", False),
    ]
    assert snippet_parts("no match here", {"x"}) == [("no match here", False)]


def test_snippet_window() -> None:
    text = "x " * 100 + "whale" + " y" * 100
    s = make_snippet(text, {"whale"}, width=10)
    assert "**whale**" in s
    assert s.startswith("...") and s.endswith("...")
    assert len(s) < 40


def test_snippet_best_window() -> None:
    # перше "a" самотнє, а друге стоїть поруч з "b" -> вікно навколо другого
    text = "a " + "z " * 100 + "a b"
    s = make_snippet(text, {"a", "b"}, width=10)
    assert "**a** **b**" in s


def test_doc_text(index: Index) -> None:
    assert doc_text(index.doc_meta[1]) == "Python has an event loop too."


def test_doc_text_missing_file(caplog: pytest.LogCaptureFixture) -> None:
    meta = DocMeta("no/such/book.txt", "t", 0, 5)
    with caplog.at_level(logging.WARNING):
        assert doc_text(meta) == ""
    assert "cannot read" in caplog.text


def test_paths_are_absolute(index: Index) -> None:
    assert all(Path(m.path).is_absolute() for m in index.doc_meta.values())

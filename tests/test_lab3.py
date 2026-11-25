import logging

import pytest

from findex.decorators import timed
from findex.index import build_index
from findex.models import Index, Posting
from findex.query import And, Not, Or, Phrase, QueryError, Term, parse
from findex.scoring import BM25, Scorer, TfIdf
from findex.search import Searcher, SearchResult
from findex.snippet import doc_text, make_snippet
from findex.store import open_index, save


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "a.txt").write_text(
        "The event loop runs async code.\n\nPython has an event loop too.",
        encoding="utf-8",
    )
    (tmp_path / "b.txt").write_text(
        "Java has no loop event here.\n\nAwait the python async task.", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def index(corpus):
    return build_index(corpus, positions=True)


# ---------- Index як об'єкт ----------


def test_index_dunders(index):
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


def test_index_properties(index):
    assert index.num_docs == 4
    assert index.doc_length(0) == 6
    assert index.df("loop") == 3
    assert index.df("ruby") == 0
    assert index.avg_doc_length == sum(index.doc_lengths.values()) / 4
    assert "avg_doc_length" in index.__dict__  # cached_property зберіг значення


def test_array_index_getitem(corpus):
    ix = build_index(corpus, kind="array")
    assert ix["python"] == [Posting(1, 1), Posting(3, 1)]


def test_open_index(index, tmp_path):
    path = tmp_path / "ix.bin"
    save(index, path)
    with open_index(path) as ix:
        assert len(ix) == len(index)
    assert ix.closed and len(ix) == 0


def test_open_index_exception(index, tmp_path):
    path = tmp_path / "ix.bin"
    save(index, path)
    with pytest.raises(RuntimeError), open_index(path) as ix:
        raise RuntimeError("boom")
    assert ix.closed  # закрився навіть з винятком


# ---------- парсер ----------


def test_parse_trees():
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


def test_parse_big():
    tree = parse('python AND (async OR await) NOT java "event loop"')
    expected = And(
        And(
            And(Term("python"), Or(Term("async"), Term("await"))),
            Not(Term("java")),
        ),
        Phrase(("event", "loop")),
    )
    assert tree == expected


def test_operators():
    a, b, c = Term("a"), Term("b"), Term("c")
    assert a & (b | c) & ~Term("d") == parse("a (b OR c) NOT d")


def test_lowercase_or_is_word():
    assert parse("a or b") == And(And(Term("a"), Term("or")), Term("b"))


@pytest.mark.parametrize("bad", ["", "(a", "a )", "OR", "a OR", "NOT", "---"])
def test_parse_errors(bad):
    with pytest.raises(QueryError):
        parse(bad)


def test_trees_hashable():
    assert hash(parse("a OR b c")) == hash(Or(Term("a"), And(Term("b"), Term("c"))))


def test_terms():
    assert parse('a OR "b c" NOT d').terms() == ("a", "b", "c")


# ---------- evaluate ----------


def test_evaluate(index):
    # docs: 0 "the event loop runs async code", 1 "python has an event loop too",
    #       2 "java has no loop event here", 3 "await the python async task"
    assert parse("loop").evaluate(index) == [0, 1, 2]
    assert parse("python AND (async OR await)").evaluate(index) == [3]
    assert parse("loop NOT java").evaluate(index) == [0, 1]
    assert parse('"event loop"').evaluate(index) == [0, 1]  # в 2 "loop event" - не фраза
    assert parse('"loop event"').evaluate(index) == [2]
    assert parse('"event loop" NOT python').evaluate(index) == [0]


def test_phrase_needs_positions(corpus):
    ix = build_index(corpus)
    with pytest.raises(QueryError):
        parse('"event loop"').evaluate(ix)


# ---------- ранжування ----------


def test_scorer_protocol():
    def my_scorer(term, posting, index):
        return 1.0

    # Protocol не потребує наслідування: і функція, і класи підходять
    for s in (TfIdf(), BM25(), my_scorer):
        assert callable(s)
    s: Scorer = BM25(k1=1.2)
    assert repr(s) == "BM25(k1=1.2, b=0.75)"


def make_index(doc_lengths, postings):
    meta = {d: None for d in doc_lengths}
    return Index(postings, doc_lengths, meta)


def test_rare_beats_common():
    # "common" в 9 з 10 документів, "rare" - в 1
    lengths = dict.fromkeys(range(10), 10)
    ix = make_index(
        lengths,
        {"common": [Posting(d, 1) for d in range(9)], "rare": [Posting(9, 1)]},
    )
    for s in (TfIdf(), BM25()):
        assert s("rare", Posting(9, 1), ix) > s("common", Posting(0, 1), ix)


def test_bm25_saturation():
    ix = make_index(dict.fromkeys(range(10), 10), {"t": [Posting(0, 1)]})
    bm = BM25()
    gain_2nd = bm("t", Posting(0, 2), ix) - bm("t", Posting(0, 1), ix)
    gain_20th = bm("t", Posting(0, 20), ix) - bm("t", Posting(0, 19), ix)
    assert gain_20th < gain_2nd / 20


def test_short_beats_long():
    ix = make_index({0: 5, 1: 500, 2: 50}, {"t": [Posting(0, 1), Posting(1, 1)]})
    bm = BM25()
    assert bm("t", Posting(0, 1), ix) > bm("t", Posting(1, 1), ix)
    # з b=0 довжина не важлива
    assert BM25(b=0)("t", Posting(0, 1), ix) == BM25(b=0)("t", Posting(1, 1), ix)


def test_search_ranked(index):
    s = Searcher(index)
    results = s.search("python OR async", k=10)
    assert {r.doc_id for r in results} == {0, 1, 3}
    assert results[0].doc_id == 3  # там і python, і async
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert len(s.search("python OR async", k=2)) == 2


def test_search_result_order():
    a = SearchResult(1.0, 5, "a")
    b = SearchResult(2.0, 1, "b")
    assert sorted([b, a]) == [a, b]
    assert max(a, b) is b


def test_cache_hit(index, caplog):
    s = Searcher(index)
    with caplog.at_level(logging.INFO):
        s.search("python async")
        s.search("python   async")  # те саме дерево -> hit
    assert s.match.cache_info().hits == 1
    assert "cache miss" in caplog.text and "cache hit" in caplog.text


# ---------- декоратор ----------


def test_timed_wraps(caplog):
    @timed
    def add(a, b):
        """Adds."""
        return a + b

    assert add.__name__ == "add"
    assert add.__doc__ == "Adds."
    with caplog.at_level(logging.INFO):
        assert add(1, 2) == 3
    assert "add took" in caplog.text


# ---------- сніпети ----------


def test_snippet_highlight():
    s = make_snippet("Python has an Event loop too.", {"event", "loop"})
    assert s == "Python has an **Event** **loop** too."


def test_snippet_window():
    text = "x " * 100 + "whale" + " y" * 100
    s = make_snippet(text, {"whale"}, width=10)
    assert "**whale**" in s
    assert s.startswith("...") and s.endswith("...")
    assert len(s) < 40


def test_snippet_best_window():
    # перше "a" самотнє, а друге стоїть поруч з "b" -> вікно навколо другого
    text = "a " + "z " * 100 + "a b"
    s = make_snippet(text, {"a", "b"}, width=10)
    assert "**a** **b**" in s


def test_doc_text(index):
    meta = index.doc_meta[1]
    assert doc_text(meta) == "Python has an event loop too."

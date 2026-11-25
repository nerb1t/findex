"""Property-based тести (Hypothesis): не приклади, а властивості для будь-яких даних."""

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from findex.index import TextUnit, build_index_from_units
from findex.merge import merge_and, merge_not, merge_or
from findex.models import Index
from findex.query import And, Node, Not, Or, Phrase, Term, parse
from findex.store import load, save
from findex.tokenize import tokenize

# ---------- стратегії ----------

# тексти з усього Unicode + трохи "словесних" символів, щоб були і слова, і абзаци
texts = st.lists(
    st.text(alphabet=st.sampled_from("abc xyz Ωж'-\n9é́"), max_size=40) | st.text(max_size=20),
    max_size=8,
)


def make_index(docs: list[str], positions: bool = False) -> Index:
    units = [TextUnit("p", f"t{i}", text, 0, len(text)) for i, text in enumerate(docs)]
    return build_index_from_units(units, positions=positions)


sorted_ids = st.lists(st.integers(0, 300), unique=True).map(sorted)

words = st.sampled_from(["a", "cat", "don't", "well-known", "42", "or", "and", "п'ять"])
leaves: st.SearchStrategy[Node] = words.map(Term) | st.lists(words, min_size=2, max_size=3).map(
    lambda ws: Phrase(tuple(ws))
)


def extend(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
    return (
        st.builds(And, children, children)
        | st.builds(Or, children, children)
        | st.builds(Not, children)
    )


trees: st.SearchStrategy[Node] = st.recursive(leaves, extend, max_leaves=8)


# ---------- властивості ----------


@pytest.mark.slow  # ~3 s: 100 прикладів x 2 файли на диску
@given(texts, st.booleans())
@settings(deadline=None)  # робота з диском може бути повільною на Windows
def test_save_load_roundtrip(docs: list[str], positions: bool) -> None:
    """load(save(index)) == index для будь-якого індексу, обидва формати."""
    index = make_index(docs, positions)
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("ix.bin", "ix.json"):
            path = Path(tmp) / name
            save(index, path)
            assert load(path) == index


@given(texts)
def test_postings_sorted_and_consistent(docs: list[str]) -> None:
    """Postings завжди відсортовані, а сума tf документа = його довжина."""
    index = make_index(docs, positions=True)
    tf_sum = dict.fromkeys(index.doc_lengths, 0)
    for term in index:
        plist = index[term]
        ids = [p.doc_id for p in plist]
        assert ids == sorted(set(ids))  # строго зростають, без повторів
        for p in plist:
            assert p.tf == len(p.positions) > 0
            assert list(p.positions) == sorted(p.positions)
            tf_sum[p.doc_id] += p.tf
    assert tf_sum == index.doc_lengths


@given(sorted_ids, sorted_ids)
def test_merge_matches_set(a: list[int], b: list[int]) -> None:
    """Оракул: швидкий merge = очевидно правильні операції над set."""
    assert merge_and(a, b) == sorted(set(a) & set(b))
    assert merge_or(a, b) == sorted(set(a) | set(b))
    assert merge_not(a, b) == sorted(set(a) - set(b))


@given(st.text())
def test_tokenize_idempotent(text: str) -> None:
    """Токени не порожні, і повторна токенізація нічого не змінює."""
    tokens = list(tokenize(text))
    assert all(tokens)
    assert list(tokenize(" ".join(tokens))) == tokens


@given(trees)
def test_parse_str_roundtrip(tree: Node) -> None:
    """Дерево -> рядок -> parse дає те саме дерево."""
    assert parse(str(tree)) == tree


@given(texts, trees)
def test_engines_agree(docs: list[str], tree: Node) -> None:
    """evaluate з merge і з set дають однаковий результат."""
    index = make_index(docs, positions=True)
    assert tree.evaluate(index, "merge") == tree.evaluate(index, "set")

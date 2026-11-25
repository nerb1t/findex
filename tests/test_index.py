"""Lab 2: побудова індексу, merge, save/load."""

from array import array
from pathlib import Path

import pytest

from findex.index import TextUnit, build_index, build_index_from_units, split_paragraphs
from findex.merge import Engine, merge_and, merge_not, merge_or
from findex.models import DocMeta, Kind, Posting
from findex.query import parse
from findex.store import StoreError, load, save


@pytest.fixture
def cats(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("the cat sat\n\nthe dog sat on the cat", encoding="utf-8")
    (tmp_path / "b.txt").write_text("a dog and a bird", encoding="utf-8")
    return tmp_path


def test_hashable() -> None:
    s = {Posting(1, 2), Posting(1, 2), Posting(3, 1)}
    assert len(s) == 2
    assert len({DocMeta("a", "b"), DocMeta("a", "b")}) == 1


def test_frozen() -> None:
    p = Posting(1, 2)
    with pytest.raises(AttributeError):
        p.tf = 5  # type: ignore[misc]  # навмисно: перевіряємо, що frozen не дає змінити


def test_slots() -> None:
    assert not hasattr(Posting(1, 2), "__dict__")


def test_build_para(cats: Path) -> None:
    index = build_index(cats, unit="para")
    # 3 абзаци: "the cat sat", "the dog sat on the cat", "a dog and a bird"
    assert len(index.doc_meta) == 3
    assert index.postings["the"] == [Posting(0, 1), Posting(1, 2)]
    assert index.doc_ids("dog") == [1, 2]
    assert index.doc_lengths == {0: 3, 1: 6, 2: 5}
    assert index.doc_ids("nothing") == []


def test_build_book(cats: Path) -> None:
    index = build_index(cats, unit="book")
    assert len(index.doc_meta) == 2
    assert index.doc_ids("cat") == [0]
    assert index.postings["cat"][0].tf == 2


def test_limit(cats: Path) -> None:
    assert build_index(cats, unit="book", limit=1).num_docs == 1


def test_on_book_callback(cats: Path) -> None:
    seen: list[str] = []
    build_index(cats, on_book=lambda p: seen.append(p.name))
    assert seen == ["a.txt", "b.txt"]


def test_positions(cats: Path) -> None:
    index = build_index(cats, positions=True)
    assert index.postings["the"][1] == Posting(1, 2, (0, 4))


def test_array_same_as_slots(cats: Path) -> None:
    a = build_index(cats, kind="array")
    b = build_index(cats, kind="slots")
    assert isinstance(a.arrays["the"][0], array)
    assert len(a) == len(b)
    for term in b:
        assert list(a.doc_ids(term)) == b.doc_ids(term)
        assert a[term] == b[term]


def test_array_no_positions() -> None:
    with pytest.raises(ValueError):
        build_index_from_units([], kind="array", positions=True)


def test_paragraph_offsets() -> None:
    text = "one\n\n\n  two words  \n\nthree"
    spans = [(k, text[s:e]) for k, s, e in split_paragraphs(text)]
    assert spans == [(0, "one"), (1, "  two words  "), (2, "three")]
    ix = build_index_from_units([TextUnit("p", "t", "x y", 0, 3)])
    assert ix.doc_meta[0] == DocMeta("p", "t", 0, 3)


def test_merge_examples() -> None:
    assert merge_and([1, 3, 5], [3, 4, 5]) == [3, 5]
    assert merge_or([1, 3], [2, 3, 9]) == [1, 2, 3, 9]
    assert merge_not([1, 2, 3], [2]) == [1, 3]
    assert merge_and([], [1]) == []


@pytest.mark.parametrize("engine", ["merge", "set"])
def test_boolean(cats: Path, engine: Engine) -> None:
    index = build_index(cats)

    def search(q: str) -> list[int]:
        return parse(q).evaluate(index, engine)

    assert search("dog") == [1, 2]
    assert search("the dog") == [1]
    assert search("cat OR bird") == [0, 1, 2]
    assert search("dog NOT cat") == [2]
    assert search("NOT dog") == [0]
    assert search("unicorn") == []


@pytest.mark.parametrize("kind", ["plain", "slots", "array"])
@pytest.mark.parametrize("ext", [".bin", ".json"])
def test_save_load(cats: Path, tmp_path: Path, kind: Kind, ext: str) -> None:
    index = build_index(cats, kind=kind)
    path = tmp_path / ("index" + ext)
    save(index, path)
    assert load(path) == index


@pytest.mark.parametrize("ext", [".bin", ".json"])
def test_save_load_positions(cats: Path, tmp_path: Path, ext: str) -> None:
    index = build_index(cats, positions=True)
    path = tmp_path / ("index" + ext)
    save(index, path)
    assert load(path) == index


def test_load_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.bin")


@pytest.mark.parametrize("content", [b"", b"garbage", b"\x80\x05K\x01."])  # останнє - pickle int
def test_load_not_index(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "bad.bin"
    path.write_bytes(content)
    with pytest.raises(StoreError):
        load(path)


def test_load_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError):
        load(path)

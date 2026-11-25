"""Збереження індексу на диск.

Формат вибирається за розширенням файлу:
  .json -> json
  все інше (.bin, .pkl) -> pickle
"""

import json
import pickle
from array import array
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, cast

from findex.decorators import timed
from findex.models import AnyPosting, ArrayPostings, DocMeta, Index, Kind, PlainPosting, Posting

# УВАГА: pickle.load виконує код!
# Pickle-файл - це не дані, а програма для маленької віртуальної машини, яка
# відтворює об'єкти. Через __reduce__ можна записати в файл "виклич os.system('...')",
# і pickle.load це виконає. Тому pickle можна вантажити ТІЛЬКИ зі своїх файлів
# (наш індекс ми самі створили). Файл зі скачаного архіву / з мережі - ніколи.


class StoreError(Exception):
    """Файл є, але це не індекс findex (або старий формат)."""


class IndexJson(TypedDict):
    # як виглядає json-файл індексу
    kind: Kind
    positions: bool
    postings: dict[str, list[list[int]]]  # term -> [[doc_id, tf, pos1, pos2, ...], ...]
    arrays: dict[str, list[list[int]]]  # term -> [[doc_ids...], [tfs...]]
    doc_lengths: dict[str, int]  # ключі в json завжди рядки
    doc_meta: dict[str, tuple[str, str, int, int]]


def save(index: Index, path: Path) -> None:
    path = Path(path)
    if path.suffix == ".json":
        save_json(index, path)
    else:
        save_pickle(index, path)


@timed
def load(path: Path) -> Index:
    """FileNotFoundError якщо файлу немає, StoreError якщо це не індекс."""
    path = Path(path)
    try:
        if path.suffix == ".json":
            return load_json(path)
        return load_pickle(path)
    except FileNotFoundError:
        raise
    except (
        pickle.UnpicklingError,  # не pickle
        ValueError,  # json.JSONDecodeError теж ValueError
        OSError,
        KeyError,
        TypeError,
        EOFError,  # обрізаний файл
        AttributeError,  # pickle зі старим класом, якого вже немає
    ) as e:
        raise StoreError(f"{path} is not a findex index ({type(e).__name__}: {e})") from e


@contextmanager
def open_index(path: Path) -> Generator[Index]:
    """with open_index("index.bin") as ix: ...

    Все до yield - це __enter__, все після - __exit__.
    finally спрацює і коли блок with закінчився нормально, і коли був return,
    і коли був виняток. Виняток ми не ковтаємо, він піде далі.
    """
    index = load(path)
    try:
        yield index
    finally:
        index.close()


# ---------- pickle ----------


def save_pickle(index: Index, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_pickle(path: Path) -> Index:
    # тільки для своїх файлів, див. коментар зверху
    with open(path, "rb") as f:
        obj: object = pickle.load(f)
    if not isinstance(obj, Index):
        raise StoreError(f"{path} contains {type(obj).__name__}, not Index")
    return obj


# ---------- json ----------
# json знає тільки dict/list/str/числа, тому все перетворюємо руками.


def save_json(index: Index, path: Path) -> None:
    postings: dict[str, list[list[int]]] = {}
    for term, plist in index.postings.items():
        postings[term] = [[p.doc_id, p.tf, *p.positions] for p in plist]
    data: IndexJson = {
        "kind": index.kind,
        "positions": index.positions,
        "postings": postings,
        "arrays": {t: [list(ids), list(tfs)] for t, (ids, tfs) in index.arrays.items()},
        "doc_lengths": {str(d): n for d, n in index.doc_lengths.items()},
        "doc_meta": {str(d): (m.path, m.title, m.start, m.end) for d, m in index.doc_meta.items()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def load_json(path: Path) -> Index:
    with open(path, encoding="utf-8") as f:
        # json.load повертає Any. cast - це обіцянка pyright, що там IndexJson;
        # файл ми писали самі (save_json), а якщо ні - впаде KeyError/TypeError нижче
        data = cast(IndexJson, json.load(f))

    kind = data["kind"]
    cls = PlainPosting if kind == "plain" else Posting
    postings: dict[str, list[AnyPosting]] = {}
    for term, rows in data["postings"].items():
        postings[term] = [cls(row[0], row[1], tuple(row[2:])) for row in rows]
    arrays: dict[str, ArrayPostings] = {
        t: (array("I", ids), array("I", tfs)) for t, (ids, tfs) in data["arrays"].items()
    }
    doc_lengths = {int(d): n for d, n in data["doc_lengths"].items()}
    doc_meta = {int(d): DocMeta(*m) for d, m in data["doc_meta"].items()}
    return Index(postings, doc_lengths, doc_meta, kind, data["positions"], arrays)

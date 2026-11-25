"""Операції над відсортованими списками doc_id (з Lab 2).

merge - два вказівники. Обидва списки відсортовані, йдемо по них одночасно,
завжди рухаємо той вказівник, де число менше. O(len(a) + len(b)).
set - простіше, але треба побудувати хеш-таблицю і потім ще відсортувати результат.

Приймаємо Sequence[int] (і list, і array('I') підходять), повертаємо list[int].
"""

from collections.abc import Callable, Sequence
from typing import Final, Literal

type Engine = Literal["merge", "set"]
type SetOp = Callable[[Sequence[int], Sequence[int]], list[int]]


def merge_and(a: Sequence[int], b: Sequence[int]) -> list[int]:
    i = j = 0
    out: list[int] = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return out


def merge_or(a: Sequence[int], b: Sequence[int]) -> list[int]:
    i = j = 0
    out: list[int] = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    # хвіст, який залишився (тільки один з них не порожній)
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def merge_not(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """a AND NOT b: все з a, чого немає в b."""
    i = j = 0
    out: list[int] = []
    while i < len(a):
        if j >= len(b) or a[i] < b[j]:
            out.append(a[i])
            i += 1
        elif a[i] == b[j]:
            i += 1
            j += 1
        else:
            j += 1
    return out


def set_and(a: Sequence[int], b: Sequence[int]) -> list[int]:
    return sorted(set(a) & set(b))


def set_or(a: Sequence[int], b: Sequence[int]) -> list[int]:
    return sorted(set(a) | set(b))


def set_not(a: Sequence[int], b: Sequence[int]) -> list[int]:
    return sorted(set(a) - set(b))


# engine -> (and, or, not)
ENGINES: Final[dict[Engine, tuple[SetOp, SetOp, SetOp]]] = {
    "merge": (merge_and, merge_or, merge_not),
    "set": (set_and, set_or, set_not),
}

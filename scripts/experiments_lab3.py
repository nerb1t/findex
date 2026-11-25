"""Експерименти з розділу "Prove it to yourself" (Lab 3).

uv run python scripts/experiments_lab3.py
"""

import dis
import functools
import time
from contextlib import contextmanager


def ex_len():
    print("1) len() і bool()")

    class V:
        pass

    try:
        len(V())
    except TypeError as e:
        print("   len(V()) ->", e)

    class V3:
        def __len__(self):
            return 3

    print("   з __len__: len =", len(V3()), " bool =", bool(V3()))
    # bool() шукає __bool__, якщо його немає - __len__ != 0. 3 != 0 -> True

    class V0:
        def __len__(self):
            return 0

    print("   якщо __len__ = 0: bool =", bool(V0()))


def ex_wraps():
    print("2) декоратор без functools.wraps і з ним")

    def timed_bad(fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    def timed_good(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    @timed_bad
    def search(q):
        """Шукає q."""

    print("   без wraps:", search.__name__, "|", search.__doc__)

    @timed_good
    def search(q):  # noqa: F811
        """Шукає q."""

    print("   з wraps:  ", search.__name__, "|", search.__doc__)


def ex_dis():
    print("3) dis: len(x) vs x.__len__()")
    for name, fn in (("len(x)", lambda: len(x)), ("x.__len__()", lambda: x.__len__())):  # noqa: F821
        ops = [
            i.opname + (f" {i.argval}" if i.argval is not None else "")
            for i in dis.get_instructions(fn)
        ]
        print(f"   {name:12} {ops}")
    data = list(range(100))
    n = 1_000_000
    start = time.perf_counter()
    for _ in range(n):
        len(data)
    t1 = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(n):
        data.__len__()
    t2 = time.perf_counter() - start
    print(f"   {n} разів: len(list) {t1:.3f} s, list.__len__() {t2:.3f} s")


def ex_lru():
    print("4) lru_cache зі списком і з tuple")

    @functools.lru_cache
    def total(xs):
        return sum(xs)

    try:
        total([1, 2, 3])
    except TypeError as e:
        print("   list ->", e)
    print("   tuple ->", total((1, 2, 3)), total.cache_info())


def ex_contextmanager():
    print("5) @contextmanager: return і виняток всередині with")

    @contextmanager
    def noisy():
        print("   enter")
        try:
            yield
        finally:
            print("   exit")

    def f():
        with noisy():
            return "returned"

    print("  ", f())
    try:
        with noisy():
            raise ValueError("boom")
    except ValueError as e:
        print("   виняток пройшов далі:", e)


if __name__ == "__main__":
    ex_len()
    ex_wraps()
    ex_dis()
    ex_lru()
    ex_contextmanager()

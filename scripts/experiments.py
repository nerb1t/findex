"""Експерименти з розділу "Prove it to yourself".

uv run python scripts/experiments.py
"""

import os
import subprocess
import sys
import time
import tracemalloc
from array import array
from dataclasses import dataclass


def dict_resize():
    print("1) sys.getsizeof(dict) після n вставок:")
    d = {}
    last = sys.getsizeof(d)
    print(f"   n=0: {last}")
    for i in range(1, 50):
        d[i] = i
        size = sys.getsizeof(d)
        if size != last:
            print(f"   n={i}: {size}  <- resize")
            last = size


class Bad:
    """__eq__ є, а __hash__ немає -> Python ставить __hash__ = None."""

    def __init__(self, x):
        self.x = x

    def __eq__(self, other):
        return self.x == other.x


class Bad42(Bad):
    def __hash__(self):
        return 42  # контракт не порушено, але всі ключі в одному слоті


class Good(Bad):
    def __hash__(self):
        return hash(self.x)


def hash_contract():
    print("2) клас з __eq__ без __hash__:")
    try:
        {Bad(1)}
    except TypeError as e:
        print("   TypeError:", e)

    n = 10_000
    for cls in (Good, Bad42):
        s = {cls(i) for i in range(n)}
        start = time.perf_counter()
        for i in range(n):
            assert cls(i) in s
        print(f"   {cls.__name__}: {n} lookups -> {time.perf_counter() - start:.3f} s")


def hash_seed():
    print('3) hash("hello") у двох окремих процесах:')
    cmd = [sys.executable, "-c", "print(hash('hello'))"]
    for env in ({}, {}, {"PYTHONHASHSEED": "0"}, {"PYTHONHASHSEED": "0"}):
        new_env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        new_env.update(env)
        out = subprocess.run(cmd, capture_output=True, text=True, env=new_env)
        print(f"   {env or 'random seed'}: {out.stdout.strip()}")


@dataclass
class P3:
    a: int
    b: int
    c: int


@dataclass(slots=True)
class S3:
    a: int
    b: int
    c: int


def slots_memory():
    print("4) 1 000 000 об'єктів з 3 полями:")
    sizes = {}
    for cls in (P3, S3):
        tracemalloc.start()
        objs = [cls(i, i, i) for i in range(1_000_000)]
        current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        sizes[cls.__name__] = current
        mb = current / 1024 / 1024
        print(f"   {cls.__name__}: {mb:.1f} MB, getsizeof(obj) = {sys.getsizeof(objs[0])}")
        del objs
    print(f"   ratio: {sizes['P3'] / sizes['S3']:.2f}x")


def array_vs_list():
    print("5) array('I') vs list на 1 000 000 чисел:")
    a = array("I", range(10**6))
    lst = list(range(10**6))
    print(f"   getsizeof(array) = {sys.getsizeof(a) / 1024 / 1024:.1f} MB")
    print(f"   getsizeof(list)  = {sys.getsizeof(lst) / 1024 / 1024:.1f} MB (тільки вказівники)")
    del lst
    tracemalloc.start()
    lst = list(range(10**6))
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del lst
    print(f"   list разом з int-ами (tracemalloc) = {current / 1024 / 1024:.1f} MB")
    print(f"   getsizeof(12345) = {sys.getsizeof(12345)}")


if __name__ == "__main__":
    dict_resize()
    hash_contract()
    hash_seed()
    slots_memory()
    array_vs_list()

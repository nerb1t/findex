"""Експерименти "Prove it to yourself" (Lab 5). Запускати і на звичайному Python, і на 3.13t:

uv run python scripts/experiments_lab5.py
.venv-313t/Scripts/python.exe scripts/experiments_lab5.py
"""

import dis
import pickle
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

N = 10**7


def cpu_bound(n: int = N) -> int:
    return sum(i * i for i in range(n))


def timeit(label: str, fn: object) -> float:
    start = time.perf_counter()
    fn()  # type: ignore[operator]
    t = time.perf_counter() - start
    print(f"   {label:28} {t:.2f} s")
    return t


def ex_cpu() -> None:
    print("1) CPU-bound sum(i*i for i in range(10**7)):")
    timeit("once", cpu_bound)
    timeit("twice, sequential", lambda: (cpu_bound(), cpu_bound()))

    def two_threads() -> None:
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(cpu_bound, [N, N]))

    def two_processes() -> None:
        with ProcessPoolExecutor(2) as pool:
            list(pool.map(cpu_bound, [N, N]))

    timeit("twice, 2 threads", two_threads)
    timeit("twice, 2 processes", two_processes)
    old = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    timeit("2 threads, switch 0.1 ms", two_threads)
    sys.setswitchinterval(old)
    print(f"   sys.getswitchinterval() = {old}")


shared = 0


def ex_race() -> None:
    print("2) два потоки по 10**6 разів shared += 1:")
    lock = threading.Lock()

    def inc(use_lock: bool) -> None:
        global shared
        for _ in range(10**6):
            if use_lock:
                with lock:
                    shared += 1
            else:
                shared += 1

    for use_lock in (False, True):
        global shared
        shared = 0
        threads = [threading.Thread(target=inc, args=(use_lock,)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        print(f"   {'з Lock' if use_lock else 'без Lock':10} shared = {shared:,} (треба 2,000,000)")

    print("   байткод shared += 1:")
    for ins in dis.get_instructions(compile("shared += 1", "<x>", "exec")):
        if ins.opname not in ("RESUME", "RETURN_CONST", "RETURN_VALUE", "LOAD_CONST", "NOP"):
            print("     ", ins.opname, ins.argrepr)


def ex_shared_counter() -> None:
    print("3) 4 потоки рахують слова в ОДИН спільний Counter (наївний паралельний індекс):")
    words = ["whale", "ship", "sea", "ahab"] * 500_000
    counts: Counter[str] = Counter()
    start = threading.Barrier(4)  # щоб усі 4 потоки стартували одночасно

    def work(chunk: list[str]) -> None:
        start.wait()
        for w in chunk:
            counts[w] += 1  # read-modify-write, як shared += 1

    chunks = [words[i::4] for i in range(4)]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(work, chunks))
    print(
        f"   сума = {counts.total():,} (треба {len(words):,}), втрачено {len(words) - counts.total():,}"
    )


def ex_lambda() -> None:
    print("4) lambda в ProcessPoolExecutor:")
    try:
        with ProcessPoolExecutor(1) as pool:
            pool.submit(lambda: 1).result()
    except Exception as e:
        print(f"   {type(e).__name__}: {e}"[:160])
    try:
        pickle.dumps(lambda: 1)
    except Exception as e:
        print(f"   pickle.dumps(lambda) -> {type(e).__name__}: {e}"[:160])


if __name__ == "__main__":
    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    print(f"Python {sys.version.split()[0]}, sys._is_gil_enabled() = {gil}")
    ex_cpu()
    ex_race()
    ex_shared_counter()
    ex_lambda()

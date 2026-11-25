import functools
import logging
import time

log = logging.getLogger("findex.timed")


def timed(fn):
    """Декоратор: пише в лог, скільки мс працювала функція.

    @timed над def f  - це те саме, що f = timed(f).
    functools.wraps копіює __name__, __doc__ і т.д. з fn на wrapper,
    без нього f.__name__ було б "wrapper".
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            # finally - щоб час записався навіть якщо fn кинула виняток
            ms = (time.perf_counter() - start) * 1000
            log.info("%s took %.1f ms", fn.__qualname__, ms)

    return wrapper

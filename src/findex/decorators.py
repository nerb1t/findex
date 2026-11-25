import functools
import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


def timed[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Декоратор: пише в лог (DEBUG), скільки мс працювала функція.

    @timed над def f  - це те саме, що f = timed(f).
    functools.wraps копіює __name__, __doc__ і т.д. з fn на wrapper,
    без нього f.__name__ було б "wrapper".
    [**P, R] - generics з PEP 695: wrapper приймає ті самі аргументи (P)
    і повертає той самий тип (R), що й fn, тому pyright не втрачає типи.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            # finally - щоб час записався навіть якщо fn кинула виняток
            ms = (time.perf_counter() - start) * 1000
            log.debug("%s took %.1f ms", fn.__qualname__, ms)

    return wrapper

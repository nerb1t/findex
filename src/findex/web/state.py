"""Стан застосунку і залежності (Depends)."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from findex.models import Index
from findex.search import Searcher
from findex.stats import IndexStats, index_stats
from findex.store import load

log = logging.getLogger(__name__)


@dataclass
class Engine:
    """Завантажений індекс + усе, що з ним потрібно маршрутам."""

    index: Index
    loaded_in: float = 0.0
    index_file_mb: float = 0.0
    searcher: Searcher = field(init=False)

    def __post_init__(self) -> None:
        self.searcher = Searcher(self.index)  # свій lru_cache запитів на весь процес

    @cached_property
    def stats(self) -> IndexStats:
        return index_stats(self.index, top=0)  # рахується один раз, при першому /stats


@dataclass
class AppState:
    engine: Engine | None = None
    error: str | None = None
    started: float = field(default_factory=time.monotonic)

    async def load(self, path: Path) -> None:
        """Вантажимо індекс у потоці: pickle на 35 MB - це ~3 s роботи CPU,
        і цикл подій тим часом відповідає на /health (503 "loading")."""
        start = time.perf_counter()
        try:
            index = await asyncio.to_thread(load, path)
            engine = Engine(index)
            # масиви NumPy для скорера (Lab 8) будуємо теж у потоці, а не в циклі подій
            await asyncio.to_thread(lambda: engine.searcher.vectors)
        except Exception as e:  # noqa: BLE001  # немає файлу / не індекс - сервіс живий, але 503
            self.error = f"{type(e).__name__}: {e}"
            log.error("cannot load index %s: %s", path, self.error)
            return
        took = time.perf_counter() - start
        engine.loaded_in = took
        engine.index_file_mb = path.stat().st_size / 2**20
        self.engine = engine
        log.info("loaded %r from %s in %.1f s", index, path, took)


def get_app_state(request: Request) -> AppState:
    state: AppState = request.app.state.findex
    return state


def get_engine(state: AppState = Depends(get_app_state)) -> Engine:
    if state.engine is None:
        raise HTTPException(503, detail="index is not loaded yet")
    return state.engine

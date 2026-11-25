"""Експерименти "Prove it to yourself" (Lab 7).

uv run python scripts/experiments_lab7.py
"""

import asyncio
import os
import time

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

from findex.web.settings import Settings


def ex_pydantic() -> None:
    print("1) Pydantic: перевірка і приведення типів на межі")

    class Hit(BaseModel):
        doc_id: int
        score: float

    h = Hit(doc_id="12", score="0.9")  # type: ignore[arg-type]
    print(
        f"   Hit(doc_id='12', score='0.9') -> {h!r}, типи {type(h.doc_id).__name__}, {type(h.score).__name__}"
    )
    try:
        Hit(doc_id="twelve", score=0.9)  # type: ignore[arg-type]
    except ValidationError as e:
        print("   doc_id='twelve' ->", e.errors()[0]["msg"])


def ex_sleep() -> None:
    print("2) time.sleep(0.5) у маршруті, 4 запити одночасно:")
    app = FastAPI()

    @app.get("/async")
    async def blocking_async() -> dict[str, str]:
        time.sleep(0.5)  # блокуючий виклик у циклі подій
        return {"ok": "async"}

    @app.get("/def")
    def blocking_def() -> dict[str, str]:
        time.sleep(0.5)  # той самий виклик, але FastAPI запустить def у пулі потоків
        return {"ok": "def"}

    async def run(path: str) -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            start = time.perf_counter()
            await asyncio.gather(*(c.get(path) for _ in range(4)))
            return time.perf_counter() - start

    for path in ("/async", "/def"):
        print(f"   {path:7} {asyncio.run(run(path)):.2f} s")


def ex_settings() -> None:
    print("3) pydantic-settings: INDEX_PATH з оточення")
    os.environ["INDEX_PATH"] = "some/where/index.bin"
    os.environ["LOG_LEVEL"] = "LOUD"
    try:
        Settings()
    except ValidationError as e:
        print("   LOG_LEVEL=LOUD ->", e.errors()[0]["msg"])
    os.environ["LOG_LEVEL"] = "INFO"
    s = Settings()
    print(f"   INDEX_PATH -> {s.index_path!r} (файлу немає, але Settings це не перевіряє:")
    print(
        "   findex serve падає одразу з 'index file not found', а сервіс без CLI - 503 на /health)"
    )


if __name__ == "__main__":
    ex_pydantic()
    ex_sleep()
    ex_settings()

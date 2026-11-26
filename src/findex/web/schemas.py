"""Pydantic-моделі - тільки на межі (вхід/вихід HTTP).

Усередині лишаються dataclass-и з Lab 2-3 (Posting, DocMeta, SearchResult):
вони швидкі й прості, а перевірка потрібна лише для даних, що прийшли ззовні.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    doc_id: int
    title: str
    score: float
    snippet: str
    highlights: list[tuple[str, bool]] = Field(
        description="Сніпет шматками: (текст, чи підсвічувати)"
    )


class SearchResponse(BaseModel):
    query: str = Field(description="Як запит зрозумів парсер")
    mode: Literal["keyword", "semantic", "hybrid"]
    scorer: Literal["bm25", "tfidf"]
    total: int = Field(description="Скільки документів підходить під запит")
    page: int
    k: int
    pages: int
    took_ms: float
    results: list[SearchHit]


class DocumentResponse(BaseModel):
    doc_id: int
    title: str
    source: str = Field(description="Файл корпусу (без шляху на сервері)")
    length: int = Field(description="Токенів у документі")
    text: str


class StatsResponse(BaseModel):
    documents: int
    books: int
    tokens: int
    vocabulary: int
    postings: int
    avg_doc_length: float
    kind: str
    positions: bool
    index_file_mb: float
    loaded_in_s: float
    uptime_s: float


class HealthResponse(BaseModel):
    status: Literal["ok", "loading", "error"]
    documents: int | None = None
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    request_id: str | None = None

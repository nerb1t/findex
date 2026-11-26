"""JSON API: /search, /docs/{doc_id}, /stats, /health."""

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from findex.query import QueryError
from findex.scoring import ScorerName
from findex.semantic import SearchMode
from findex.snippet import doc_text
from findex.web.schemas import (
    DocumentResponse,
    ErrorResponse,
    HealthResponse,
    SearchHit,
    SearchResponse,
    StatsResponse,
)
from findex.web.service import SearchPage, run_search, source_name
from findex.web.state import AppState, Engine, get_app_state, get_engine

# параметри /search: FastAPI читає типи й обмеження з анотацій (як typer у Lab 4)
# і сам повертає 422 з поясненням, якщо щось не так
Q = Annotated[
    str, Query(min_length=1, max_length=200, description='Запит: слова, OR, NOT, (), "фраза"')
]
K = Annotated[int, Query(ge=1, le=50, description="Результатів на сторінку")]
PageNo = Annotated[int, Query(ge=1, le=100, description="Номер сторінки результатів")]
Scorer = Annotated[ScorerName, Query(description="Формула балів")]
Mode = Annotated[
    SearchMode,
    Query(description="keyword - BM25/TF-IDF, semantic - ембединги, hybrid - обидва через RRF"),
]

ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Поганий запит (дужки, фраза без позицій)"},
    503: {"model": ErrorResponse, "description": "Індекс ще не завантажений"},
}


def to_response(p: SearchPage) -> SearchResponse:
    return SearchResponse(
        query=p.query,
        mode=p.mode,
        scorer=p.scorer,
        total=p.total,
        page=p.page,
        k=p.k,
        pages=p.pages,
        took_ms=round(p.took_ms, 2),
        results=[
            SearchHit(
                doc_id=h.result.doc_id,
                title=h.result.title,
                score=round(h.result.score, 4),
                snippet=h.snippet,
                highlights=h.parts,
            )
            for h in p.hits
        ],
    )


def do_search(
    engine: Engine, q: str, k: int, scorer: ScorerName, page: int, mode: SearchMode
) -> SearchResponse:
    try:
        return to_response(run_search(engine, q, k, scorer, page, mode))
    except QueryError as e:
        raise HTTPException(400, detail=f"bad query: {e}") from e


# Два варіанти одного маршруту. Пошук - це робота CPU (BM25, сніпети), ніякого await.
# def: FastAPI запускає його в пулі потоків, цикл подій вільний (типово, лишаємо його).
# async def: те саме виконується прямо в циклі подій і блокує всі інші запити
# (лишився тільки для бенчмарку, SEARCH_MODE=async).


def search_def(
    q: Q,
    k: K = 10,
    scorer: Scorer = "bm25",
    page: PageNo = 1,
    mode: Mode = "keyword",
    engine: Engine = Depends(get_engine),
) -> SearchResponse:
    return do_search(engine, q, k, scorer, page, mode)


async def search_async(
    q: Q,
    k: K = 10,
    scorer: Scorer = "bm25",
    page: PageNo = 1,
    mode: Mode = "keyword",
    engine: Engine = Depends(get_engine),
) -> SearchResponse:
    return do_search(engine, q, k, scorer, page, mode)


def make_router(search_mode: Literal["def", "async"] = "def") -> APIRouter:
    router = APIRouter(tags=["api"])
    router.add_api_route(
        "/search",
        search_async if search_mode == "async" else search_def,
        methods=["GET"],
        response_model=SearchResponse,
        responses=ERRORS,
        summary="Ранжований пошук з пагінацією",
    )
    router.add_api_route(
        "/docs/{doc_id}",
        get_document,
        methods=["GET"],
        response_model=DocumentResponse,
        responses={404: {"model": ErrorResponse}, 503: ERRORS[503]},
        summary="Документ",
    )
    router.add_api_route(
        "/stats",
        get_stats,
        methods=["GET"],
        response_model=StatsResponse,
        responses={503: ERRORS[503]},
        summary="Статистика індексу",
    )
    router.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
        summary="Перевірка для платформи",
    )
    # HEAD /health - для платформ, що перевіряють без тіла відповіді (в OpenAPI не показуємо)
    router.add_api_route("/health", health, methods=["HEAD"], include_in_schema=False)
    return router


def get_document(
    doc_id: Annotated[int, Path(ge=0)],
    engine: Engine = Depends(get_engine),
) -> DocumentResponse:
    meta = engine.index.doc_meta.get(doc_id)
    if meta is None:
        raise HTTPException(404, detail=f"document {doc_id} not found")
    return DocumentResponse(
        doc_id=doc_id,
        title=meta.title,
        source=source_name(meta.path),
        length=engine.index.doc_length(doc_id),
        text=doc_text(meta),
    )


def get_stats(
    engine: Engine = Depends(get_engine),
    state: AppState = Depends(get_app_state),
) -> StatsResponse:
    st = engine.stats
    return StatsResponse(
        documents=st.documents,
        books=st.books,
        tokens=st.tokens,
        vocabulary=st.vocabulary,
        postings=st.postings,
        avg_doc_length=round(st.avg_doc_length, 2),
        kind=engine.index.kind,
        positions=engine.index.positions,
        index_file_mb=round(engine.index_file_mb, 1),
        loaded_in_s=round(engine.loaded_in, 2),
        uptime_s=round(time.monotonic() - state.started, 1),
    )


async def health(state: AppState = Depends(get_app_state)) -> JSONResponse:
    # async def тут правильно: жодної роботи CPU, відповідь миттєва навіть під навантаженням
    if state.engine is not None:
        body = HealthResponse(status="ok", documents=state.engine.index.num_docs)
        return JSONResponse(body.model_dump(exclude_none=True))
    status = "error" if state.error else "loading"
    body = HealthResponse(status=status, detail=state.error)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=503)

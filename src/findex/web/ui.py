"""HTML-сторінки (Jinja2): / - пошук, /doc/{id} - документ."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from findex.query import QueryError, parse
from findex.scoring import ScorerName
from findex.semantic import SearchMode
from findex.snippet import doc_text, highlight_all
from findex.web.service import SearchPage, run_search, source_name
from findex.web.state import AppState, get_app_state

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)

EXAMPLES = [
    '"white whale" ahab',
    "alice rabbit",
    "hamlet ghost",
    "brutus OR cassius",
    "emma harriet NOT knightley",
    "(sea OR ocean) storm",
]
# запити без спільних слів з текстом - для режиму semantic
SEMANTIC_EXAMPLES = [
    "captain obsessed with revenge on a sea monster",
    "girl falls down a hole into a strange world",
    "son sees the spirit of his murdered father",
]


def page_window(page: int, pages: int, width: int = 2) -> list[int | None]:
    """[1, None, 4, 5, 6, None, 20] - None означає "..."."""
    shown = {1, pages, *range(max(1, page - width), min(pages, page + width) + 1)}
    out: list[int | None] = []
    prev = 0
    for p in sorted(shown):
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    scorer: ScorerName = "bm25",
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    mode: SearchMode = "keyword",
    state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    # def, а не async def: пошук - робота CPU, хай іде в пулі потоків
    ctx: dict[str, object] = {
        "q": q,
        "scorer": scorer,
        "mode": mode,
        "examples": EXAMPLES,
        "semantic_examples": SEMANTIC_EXAMPLES,
        "result": None,
        "error": None,
    }
    engine = state.engine
    if engine is None:
        ctx["error"] = "Індекс ще завантажується, оновіть сторінку за кілька секунд."
        return templates.TemplateResponse(request, "index.html", ctx, status_code=503)
    ctx["documents"] = engine.index.num_docs
    ctx["semantic"] = engine.semantic is not None
    if q.strip():
        try:
            result: SearchPage = run_search(engine, q, 10, scorer, page, mode)
        except QueryError as e:
            ctx["error"] = f"Не вдалося розібрати запит: {e}"
        else:
            ctx["result"] = result
            ctx["window"] = page_window(page, min(result.pages, 100))
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/doc/{doc_id}", response_class=HTMLResponse)
def document(
    request: Request,
    doc_id: int,
    q: Annotated[str, Query(max_length=200)] = "",
    state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    engine = state.engine
    meta = engine.index.doc_meta.get(doc_id) if engine is not None else None
    if engine is None or meta is None:
        ctx = {"q": q, "doc_id": doc_id}
        return templates.TemplateResponse(request, "not_found.html", ctx, status_code=404)
    terms: set[str] = set()
    if q.strip():
        try:
            terms = set(parse(q).terms())
        except QueryError:
            pass
    text = doc_text(meta)
    paragraphs = (
        [highlight_all(p, terms) for p in text.split("\n\n")]
        if terms
        else [[(p, False)] for p in text.split("\n\n")]
    )
    ctx = {
        "q": q,
        "doc_id": doc_id,
        "meta": meta,
        "source": source_name(meta.path),
        "length": engine.index.doc_length(doc_id),
        "paragraphs": paragraphs,
        "prev_id": doc_id - 1 if doc_id - 1 in engine.index.doc_meta else None,
        "next_id": doc_id + 1 if doc_id + 1 in engine.index.doc_meta else None,
    }
    return templates.TemplateResponse(request, "doc.html", ctx)

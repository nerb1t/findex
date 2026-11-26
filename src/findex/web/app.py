"""FastAPI-застосунок: lifespan, middleware з id запиту, маршрути."""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from findex.web import ui
from findex.web.api import make_router
from findex.web.logs import request_id_var
from findex.web.settings import Settings, get_settings
from findex.web.state import AppState

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # як @contextmanager з Lab 3, тільки async: до yield - старт, після - зупинка.
        # Індекс вантажимо у фоні, щоб порт відкрився одразу і /health відповідав 503
        # "loading", поки pickle читається (платформа бачить, що сервіс живий)
        state = AppState()
        app.state.findex = state
        task = asyncio.create_task(
            state.load(settings.index_path, settings.embeddings_path, settings.model_dir)
        )
        log.info("starting, index=%s, search_mode=%s", settings.index_path, settings.search_mode)
        yield
        task.cancel()
        await asyncio.wait([task])
        if state.engine is not None:
            state.engine.index.close()
        log.info("stopped")

    app = FastAPI(
        title="findex",
        version=version("findex"),
        description="Маленька пошукова система по книгах Project Gutenberg (курс Python).",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        except Exception:
            # стек - у лог (з id запиту), назовні - чистий 500 без подробиць
            log.exception("unhandled error: %s %s", request.method, request.url.path)
            response = JSONResponse(
                {"detail": "internal server error", "request_id": rid}, status_code=500
            )
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    app.include_router(make_router(settings.search_mode))
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app

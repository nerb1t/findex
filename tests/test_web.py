# Starlette 1.10 описує TestClient через пакет httpx2, якого в проєкті немає
# (TestClient працює і на звичайному httpx). Без httpx2 pyright не знає типів
# відповіді, тому для цього файлу вимикаємо лише перевірки "Unknown":
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Lab 7: веб через TestClient. Справжнього файлу індексу немає - підставляємо
маленький індекс з conftest через dependency_overrides."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from findex.models import Index
from findex.web import create_app
from findex.web.api import search_def
from findex.web.settings import Settings
from findex.web.state import AppState, Engine, get_app_state
from findex.web.ui import page_window

MISSING = Path("no/such/index.bin")


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(index_path=MISSING))


@pytest.fixture
def client(app: FastAPI, index: Index) -> Iterator[TestClient]:
    state = AppState(engine=Engine(index))
    app.dependency_overrides[get_app_state] = lambda: state
    with TestClient(app) as c:  # with - щоб відпрацював lifespan
        yield c


# ---------- /search ----------


def test_search_ok(client: TestClient) -> None:
    r = client.get("/search", params={"q": "python async"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "(python AND async)"
    assert body["total"] == 1 and body["pages"] == 1 and body["page"] == 1
    hit = body["results"][0]
    assert hit["doc_id"] == 3
    assert hit["snippet"] == "Await the python async task."
    assert ["python", True] in hit["highlights"]
    assert body["took_ms"] >= 0
    assert "x-request-id" in r.headers


def test_search_pagination(client: TestClient) -> None:
    first = client.get("/search", params={"q": "loop", "k": 2}).json()
    second = client.get("/search", params={"q": "loop", "k": 2, "page": 2}).json()
    assert first["total"] == 3 and first["pages"] == 2
    assert len(first["results"]) == 2 and len(second["results"]) == 1
    ids = [h["doc_id"] for h in first["results"] + second["results"]]
    assert sorted(ids) == [0, 1, 2]  # без повторів між сторінками
    assert client.get("/search", params={"q": "loop", "page": 5}).json()["results"] == []


def test_search_tfidf_and_phrase(client: TestClient) -> None:
    r = client.get("/search", params={"q": '"event loop"', "scorer": "tfidf"})
    assert r.status_code == 200
    assert {h["doc_id"] for h in r.json()["results"]} == {0, 1}


@pytest.mark.parametrize(
    "params",
    [
        {},  # немає q
        {"q": ""},
        {"q": "x" * 201},
        {"q": "a", "k": "abc"},
        {"q": "a", "k": 0},
        {"q": "a", "k": 51},
        {"q": "a", "page": 0},
        {"q": "a", "page": 101},
        {"q": "a", "scorer": "magic"},
    ],
)
def test_search_422(client: TestClient, params: dict[str, object]) -> None:
    r = client.get("/search", params=params)  # type: ignore[arg-type]
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)  # FastAPI пояснює, що саме не так


def test_search_bad_query_400(client: TestClient) -> None:
    r = client.get("/search", params={"q": "(python"})
    assert r.status_code == 400
    assert r.json() == {"detail": "bad query: missing ')'"}


def test_search_mode_async_same_result(index: Index) -> None:
    app = create_app(Settings(index_path=MISSING, search_mode="async"))
    app.dependency_overrides[get_app_state] = lambda: AppState(engine=Engine(index))
    with TestClient(app) as c:
        r = c.get("/search", params={"q": "python"})
    assert r.status_code == 200 and r.json()["total"] == 2


# ---------- /docs, /stats, /health ----------


def test_document(client: TestClient) -> None:
    r = client.get("/docs/1")
    assert r.status_code == 200
    assert r.json() == {
        "doc_id": 1,
        "title": "a.txt #1: Python has an event loop too.",
        "source": "a.txt",
        "length": 6,
        "text": "Python has an event loop too.",
    }


@pytest.mark.parametrize(("doc_id", "status"), [(999, 404), (-1, 422), ("abc", 422)])
def test_document_errors(client: TestClient, doc_id: object, status: int) -> None:
    r = client.get(f"/docs/{doc_id}")
    assert r.status_code == status
    if status == 404:
        assert r.json() == {"detail": "document 999 not found"}


def test_openapi_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200  # Swagger UI, його пише FastAPI
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/search", "/docs/{doc_id}", "/stats", "/health"} <= set(paths)


def test_stats(client: TestClient) -> None:
    body = client.get("/stats").json()
    assert body["documents"] == 4 and body["books"] == 2 and body["positions"] is True
    assert body["uptime_s"] >= 0


def test_health_ok(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "documents": 4}
    assert client.head("/health").status_code == 200


def test_health_503_while_loading(app: FastAPI) -> None:
    app.dependency_overrides[get_app_state] = lambda: AppState()  # індексу ще немає
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 503 and r.json() == {"status": "loading"}
        assert c.get("/search", params={"q": "x"}).status_code == 503
        assert c.get("/stats").status_code == 503


def test_health_503_when_index_missing(app: FastAPI) -> None:
    # без підміни: lifespan справді пробує завантажити неіснуючий файл
    with TestClient(app) as c:
        r = c.get("/health")
        for _ in range(50):  # завантаження йде у фоні, чекаємо, поки впаде
            if r.json()["status"] == "error":
                break
            r = c.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "error" and "FileNotFoundError" in r.json()["detail"]


def test_real_load_through_lifespan(index: Index, tmp_path: Path) -> None:
    from findex.store import save

    path = tmp_path / "ix.bin"
    save(index, path)
    with TestClient(create_app(Settings(index_path=path))) as c:
        for _ in range(100):
            if c.get("/health").status_code == 200:
                break
        assert c.get("/search", params={"q": "python"}).json()["total"] == 2


# ---------- помилки 500 ----------


def test_unexpected_error_is_clean_500(
    app: FastAPI, index: Index, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    app.dependency_overrides[get_app_state] = lambda: AppState(engine=Engine(index))

    def boom(*args: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("findex.web.api.run_search", boom)
    with TestClient(app) as c, caplog.at_level(logging.ERROR):
        r = c.get("/search", params={"q": "python"}, headers={"X-Request-ID": "req-42"})
    assert r.status_code == 500
    assert r.json() == {"detail": "internal server error", "request_id": "req-42"}
    assert "secret" not in r.text  # стек і повідомлення не йдуть назовні
    assert "secret internal detail" in caplog.text  # але є в лозі


# ---------- HTML ----------


def test_home_page(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Пошук по книгах" in r.text


def test_home_search_highlight_and_escape(client: TestClient) -> None:
    r = client.get("/", params={"q": "python async"})
    assert r.status_code == 200
    assert "<mark>python</mark>" in r.text and "<mark>async</mark>" in r.text
    assert 'href="/doc/3?q=python%20async"' in r.text
    r = client.get("/", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.text  # Jinja2 екранує


def test_home_bad_query_message(client: TestClient) -> None:
    r = client.get("/", params={"q": "(python"})
    assert r.status_code == 200 and "Не вдалося розібрати запит" in r.text


def test_home_loading(app: FastAPI) -> None:
    app.dependency_overrides[get_app_state] = lambda: AppState()
    with TestClient(app) as c:
        assert c.get("/").status_code == 503


def test_doc_page(client: TestClient) -> None:
    r = client.get("/doc/1", params={"q": "loop"})
    assert r.status_code == 200
    assert "Python has an event <mark>loop</mark> too." in r.text
    assert 'href="/doc/0?q=loop"' in r.text and 'href="/doc/2?q=loop"' in r.text
    assert client.get("/doc/999").status_code == 404


def test_static_css(client: TestClient) -> None:
    r = client.get("/static/style.css")
    assert r.status_code == 200 and "mark" in r.text


def test_page_window() -> None:
    assert page_window(1, 1) == [1]
    assert page_window(5, 20) == [1, None, 3, 4, 5, 6, 7, None, 20]
    assert page_window(2, 5) == [1, 2, 3, 4, 5]


def test_search_def_is_plain_function() -> None:
    # рішення з M2: /search - звичайний def (FastAPI запускає його в пулі потоків)
    import inspect

    assert not inspect.iscoroutinefunction(search_def)

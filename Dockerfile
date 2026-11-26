# syntax=docker/dockerfile:1
# Два етапи (за гайдом uv): у першому ставимо залежності з uv.lock і будуємо індекс,
# у другому - тільки готове оточення, корпус та індекс, без uv і компіляторів.

# ---------- етап 1: збірка ----------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# спочатку тільки залежності - цей шар кешується, поки не змінився uv.lock
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# тепер сам findex (не editable: у фінальному образі src/ не потрібен)
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# корпус (18 книг Gutenberg, ~12 MB) і індекс з позиціями - прямо в образ.
# Для демо так найпростіше: контейнер нічого не качає при старті
COPY scripts/get_corpus.py ./scripts/
RUN .venv/bin/python scripts/get_corpus.py \
    && .venv/bin/findex index data/gutenberg --out index.bin --kind array --positions

# Lab 8: ембединги рахуються ~6 хв на 8 ядрах - на сервері збірки це надто довго,
# тому файл (int8, 19 MB) лежить у репозиторії. Модель качаємо в образ один раз,
# щоб контейнер не ходив за нею в мережу при кожному старті
COPY embeddings/gutenberg.emb.npz ./index.emb.npz
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/models')"

# ---------- етап 2: запуск ----------
FROM python:3.13-slim-bookworm
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# шлях до книг записаний в індексі (/app/data/...) - сніпети читають їх звідти
COPY --from=builder --chown=app:app /app/data /app/data
COPY --from=builder --chown=app:app /app/index.bin /app/index.bin
COPY --from=builder --chown=app:app /app/index.emb.npz /app/index.emb.npz
COPY --from=builder --chown=app:app /app/models /app/models

# конфіг - тільки змінні оточення; будь-яку можна перебити: docker run -e LOG_LEVEL=DEBUG
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    INDEX_PATH=/app/index.bin \
    MODEL_DIR=/app/models \
    HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=INFO

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"]
CMD ["findex", "serve"]

FROM ghcr.io/astral-sh/uv:0.9.7 AS uv
FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY scripts/download_model.py ./scripts/download_model.py
RUN uv run --no-sync python scripts/download_model.py

FROM python:3.12-slim
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/assets ./assets
COPY main.py alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log"]

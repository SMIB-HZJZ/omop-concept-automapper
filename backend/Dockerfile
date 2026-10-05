FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app/backend

# Dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./
ARG UV_EXTRA_ARGS="--no-dev"
RUN uv sync --frozen --no-install-project ${UV_EXTRA_ARGS}

COPY backend/ ./
# Example workflow plugins (enabled via WORKFLOW_PLUGINS / WORKFLOW_DEFINITIONS_DIR)
COPY examples/ /app/examples/
ENV PYTHONPATH=/app/backend:/app

RUN useradd --uid 1001 --create-home app \
    && mkdir -p /data/uploads /data/vocabulary \
    && chown -R app:app /data
USER app

EXPOSE 8000
ENTRYPOINT ["/app/backend/scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

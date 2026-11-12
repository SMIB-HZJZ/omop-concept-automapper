# Pinned for reproducible builds; bump deliberately
FROM python:3.12.14-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv
WORKDIR /app/backend

# Dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./
ARG UV_EXTRA_ARGS="--no-dev"
RUN uv sync --frozen --no-install-project ${UV_EXTRA_ARGS}

COPY backend/ ./
# Tests stay out of production images (the dev compose mounts the source, tests included)
ARG KEEP_TESTS=false
RUN if [ "$KEEP_TESTS" != "true" ]; then rm -rf tests; fi
# Example workflow plugins (enabled via WORKFLOW_PLUGINS / WORKFLOW_DEFINITIONS_DIR)
COPY examples/ /app/examples/
ENV PYTHONPATH=/app/backend:/app

RUN useradd --uid 1001 --create-home app \
    && mkdir -p /data/uploads /data/vocabulary \
    && chown -R app:app /data
USER app

EXPOSE 8000
# API liveness; the worker service overrides this in compose (it serves no HTTP)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status != 200)"
ENTRYPOINT ["/app/backend/scripts/entrypoint.sh"]
# X-Forwarded-For is trusted only from FORWARDED_ALLOW_IPS (uvicorn reads it; default 127.0.0.1).
# Set it to the reverse proxy's address in compose, never "*": the lockout keys on the client IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

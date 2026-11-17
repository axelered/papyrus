FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.30 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# libraries must never download at runtime: grammars and tokenizers are baked in here
COPY server ./server
COPY scripts ./scripts
# tree-sitter creates its cache dir as 0700, the non-root runtime user must still read it
RUN PAPYRUS_ASSETS_DIR=/app/assets /app/.venv/bin/python -m scripts.prefetch && chmod -R a+rX /app/assets


# shared by dev and runtime so tests run against the same system libraries as prod
FROM python:3.14-slim AS base

# LibreOffice is driven in-process through LibreOfficeKit (adr/convert.md), only the headless components
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --no-create-home papyrus
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PAPYRUS_ASSETS_DIR=/app/assets


# local and CI testing: server, test and scripts are bind-mounted at run time (adr/development.md)
FROM base AS dev

COPY --from=ghcr.io/astral-sh/uv:0.11.30 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/.pytest_cache" \
    RUFF_CACHE_DIR=/tmp/.ruff_cache
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --group dev --no-install-project
COPY --from=build /app/assets /app/assets

USER papyrus
EXPOSE 8000
# watch only the mounted source: polling the whole /app (venv included) misses changes for seconds
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "server"]


FROM base AS runtime

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/assets /app/assets
COPY server ./server

ENV PAPYRUS_CONFIG=/etc/papyrus/config.yml

USER papyrus
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# single process on purpose: --workers would multiply pools and split the admission limit
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]

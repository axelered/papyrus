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


FROM python:3.14-slim

RUN useradd --system --uid 10001 --no-create-home papyrus
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/assets /app/assets
COPY server ./server

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PAPYRUS_CONFIG=/etc/papyrus/config.yml \
    PAPYRUS_ASSETS_DIR=/app/assets

USER papyrus
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# single process on purpose: --workers would multiply pools and split the admission limit
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]

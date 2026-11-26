# Contributing

Thanks for helping with Papyrus. Open an issue before a large change so we can agree on the approach first, and see
[SECURITY.md](SECURITY.md) for vulnerabilities instead of opening a public issue.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Docker. The server refuses to start without an API key, so for local
development create a gitignored `.env`:

```shell
PAPYRUS_AUTH_DISABLED=true
PAPYRUS_CONFIG=config.local.yml
```

Download the chunking grammars and tokenizers once into the gitignored `.assets/`. The server never downloads at
runtime, and the Docker build does the same step:

```shell
uv run python -m scripts.prefetch
```

Then start the server with reload:

```shell
uv run fastapi dev --entrypoint server.main:app --reload
```

Or run the development environment with dekit:

```shell
dekit reload --yes
dekit up
```

## Docker loop

LibreOffice, used for legacy formats, only runs in the Linux image. On any OS with Docker, dekit
runs the image loop (`adr/development.md`):

| Task          | What                                                       | Port |
|---------------|------------------------------------------------------------|------|
| `docker/dev`  | dev image, source mounted read-only, reload, auth disabled | 8001 |
| `docker/prod` | prod image as shipped, API key `dev`                       | 8002 |
| `docker/test` | full test suite inside the dev image                       |      |

```shell
dekit start docker/dev
dekit start docker/build-dev   # rebuild after changing dependencies or the Dockerfile
```

## Checks

Format and lint:

```shell
ruff format
ruff check --fix
```

Run the tests. Tests marked `full` need the image and are deselected on the host, run them with
`dekit start docker/test`:

```shell
uv run pytest
```

The SDKs have their own tests, described in `sdk/python/README.md` and `sdk/typescript/README.md`.

## Logs and profiling

Logs are plain text on stderr. `PAPYRUS_LOG_LEVEL` (`debug`, `info`, `warning`, `error`) sets the papyrus loggers,
uvicorn keeps its own `--log-level`, and `PAPYRUS_LOG_FORMAT=json` switches every line to JSON.

With `PAPYRUS_TIMING=true` every response carries a `Server-Timing` header with the duration of each stage (`parse`,
`materialize`, `detect`, `convert`, `render`, `browser.goto`, ...), and the same values are logged once per request.
Keep it off in production, the header reveals internal timings. `docker exec papyrus-dev python -m scripts.bench`
prints cold and hot latency per operation (`adr/performance.md`).

## Pull requests

- Keep a change to one topic, with tests for new behavior and fixed bugs.
- Reflect configuration changes in `config.example.yml`, and API changes in the Bruno collection in `bruno/`.
- Commit messages are one line with a conventional prefix, for example `fix: keep healthy workers on cancel`.

## Agents

This repository is set up for agentic development with `work3`. Install it with `bun install -g @svara/work3`.

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).

# Development and CI

How to run the server and the tests on any machine, and how GitHub Actions checks every change. LibreOffice
(`adr/convert.md`) only runs inside the Linux image, so the project has two loops: a fast host loop and a
full-fidelity image loop.

## Goals

- Windows, macOS and Linux run the dev server and the full test suite with Docker plus dekit.
- Host development stays fast: native reload, IDE debugger, no Docker needed for most work.
- The tests that need the image run in CI on every change and fail loudly when LibreOffice is missing.
- Dev and prod share the same system layer (Python, LibreOffice, fonts) from one Dockerfile.

## Decisions

| Concern         | Choice                                                                                    |
|-----------------|-------------------------------------------------------------------------------------------|
| Default loop    | Host uv (`server` task, `uv run pytest`), `full` tests deselected                         |
| Full loop       | `dev` Docker stage with source bind-mounted, driven by dekit                              |
| Orchestration   | dekit only, no compose: `cmd` is an argv array, so relative `-v ./x:/app/x` works in any shell |
| Image layout    | One multi-stage Dockerfile, `runtime` stays last so `docker build .` builds prod           |
| CI              | One GitHub Actions job: build `dev`, lint and test inside it, then build `runtime`        |
| Skip guard      | `full` marker deselected by `addopts` on the host, re-included with `-m "full or not full"` in the image |

## Dockerfile

```
build    python:3.14-slim + uv: prod venv (--no-dev) and prefetched assets (unchanged)
base     python:3.14-slim + LibreOffice apt packages + fonts + user papyrus (uid 10001)
dev      FROM base: uv, `uv sync --locked --group dev --no-install-project` into /app/.venv, assets from build,
         pyproject.toml copied, server/test/scripts mounted at run time; USER papyrus;
         CMD uvicorn --reload --reload-dir server
runtime  FROM base: prod venv, assets, server; HEALTHCHECK and CMD as today (last stage = default target)
```

- `dev` and `runtime` share the cached `base` layer, so LibreOffice is installed once per build cache.
- `dev` has the same `ENV` as `runtime` (`PATH`, `PAPYRUS_ASSETS_DIR`, `PYTHONUNBUFFERED`) and runs as the same
  non-root user. The read-only mounts avoid uid conflicts on Linux hosts.
- Source is not copied into `dev`, so editing code needs no rebuild. Changing dependencies (`uv.lock`) does.
- `/app` is read-only for the runtime user, so `dev` points the pytest cache at `/tmp` via `PYTEST_ADDOPTS`.

## dekit Tasks

| Task                 | Type    | Deps                 | Command                                                       | Port |
|----------------------|---------|----------------------|---------------------------------------------------------------|------|
| `install`            | job     |                      | `uv sync --group dev` (unchanged)                             |      |
| `server`             | service | `install`            | host `fastapi dev --reload` (unchanged, autostart)            | 8000 |
| `docker/build-dev`   | job     |                      | `docker build --target dev -t papyrus:dev .`                  |      |
| `docker/dev`         | service | `docker/build-dev`   | `docker run --name papyrus-dev -p 8001:8000`, mounts, image CMD | 8001 |
| `docker/build-prod`  | job     |                      | `docker build -t papyrus .`                                   |      |
| `docker/prod`        | service | `docker/build-prod`  | `docker run --name papyrus-prod -p 8002:8000 -e PAPYRUS_API_KEY=dev papyrus` | 8002 |
| `docker/test`        | job     | `docker/build-dev`   | `docker run --name papyrus-test`, mounts, `pytest -m "full or not full"` |      |

- Container tasks live in a `docker/` folder: dekit allows no tasks under a task, and `server` is one.
- Every `docker run` uses `--rm --init`. Mounts for `docker/dev` and `docker/test`: `./server:/app/server:ro`,
  `./test:/app/test:ro`, `./scripts:/app/scripts:ro`.
- `docker/dev` env: `PAPYRUS_AUTH_DISABLED=true`, `WATCHFILES_FORCE_POLLING=true`. Inotify does not cross Docker
  Desktop bind mounts on Windows or macOS, so reload has to poll. It watches only `server`: polling all of `/app`
  (venv included) misses changes.
- `docker/prod` runs the shipped image with no mounts and real auth (key `dev`), as a local smoke test of prod.
- The ports differ so all three servers can run side by side.
- Containers are named and stopped with `stop: {cmd: docker stop <name>}`. On Windows dekit's SIGTERM ends the
  `docker run` client but not the container. After a hard kill the next start fails with "name in use":
  `docker rm -f <name>`.
- dekit jobs run once per runner start: after changing `uv.lock` or the Dockerfile, `dekit start docker/build-dev`.
  After editing `dekit.yaml`, `dekit reload --yes`, since `dekit up` keeps the saved tasks.
- The host `.env` is not passed into containers: its `PAPYRUS_CONFIG` points at a host path.

## Tests

- Tests that need the image (LibreOffice today) carry `@pytest.mark.full` and no `skipif`. The marker is registered
  in `pyproject.toml`.
- `pyproject.toml` sets `addopts = "-m 'not full'"`, so host `uv run pytest` deselects them.
- The image runs `pytest -m "full or not full"`, which overrides the filter, so every test runs and a missing or
  moved LibreOffice fails the `full` tests instead of skipping them. Not `-m ""`: dekit on Windows drops the empty
  argument.
- The override appears in exactly two places, the dekit `docker/test` task and the CI step, each with a short
  comment. Dropping it silently deselects the `full` tests again.
- API tests that use the fake convert service stay unmarked and run everywhere.

## CI

`.github/workflows/check.yml`, triggered on `pull_request` and on `push` to `main`, one `ubuntu-latest` job:

1. `actions/checkout`, `docker/setup-buildx-action`.
2. `docker/build-push-action`: `target: dev`, `load: true`, `tags: papyrus:dev`,
   `cache-from: type=gha`, `cache-to: type=gha,mode=max`.
3. Run inside `papyrus:dev` with the same mounts as dekit `docker/test`: `ruff format --check`, `ruff check`,
   `pytest -m "full or not full"`.
4. `docker/build-push-action`: default target (`runtime`), `push: false`, `cache-from: type=gha`, which proves
   the prod image builds.

- Timing: about 5-8 min with a cold cache (LibreOffice apt layer), about 1-2 min warm.
- `concurrency` cancels superseded runs on the same ref.

## Out of Scope

- todo: publish the image to GHCR on tags or `main` (release ADR).
- todo: a parallel host job (uv, fast tests) if CI feedback on a cold cache becomes a bottleneck.
- todo: Python version matrix, only once more than one version is supported.
- Devcontainers and compose: dekit already covers the same workflows.

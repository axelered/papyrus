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
| Skip guard      | `full` marker deselected by `addopts` on the host, re-included with `-m ""` in the image   |

## Dockerfile

```
build    python:3.14-slim + uv: prod venv (--no-dev) and prefetched assets (unchanged)
base     python:3.14-slim + LibreOffice apt packages + fonts + user papyrus (uid 10001)
dev      FROM base: uv, `uv sync --locked --group dev --no-install-project` into /app/.venv, assets from build,
         pyproject.toml copied, server/test/scripts mounted at run time; USER papyrus
runtime  FROM base: prod venv, assets, server; HEALTHCHECK and CMD as today (last stage = default target)
```

- `dev` and `runtime` share the cached `base` layer, so LibreOffice is installed once per build cache.
- `dev` has the same `ENV` as `runtime` (`PATH`, `PAPYRUS_ASSETS_DIR`, `PYTHONUNBUFFERED`) and runs as the same
  non-root user. The read-only mounts avoid uid conflicts on Linux hosts.
- Source is not copied into `dev`, so editing code needs no rebuild. Changing dependencies (`uv.lock`) does.
- `/app` is read-only for the runtime user, so `dev` points the pytest cache at `/tmp` via `PYTEST_ADDOPTS`.

## dekit Tasks

| Task          | Type    | Deps         | Command                                                                  | Port |
|---------------|---------|--------------|--------------------------------------------------------------------------|------|
| `install`     | job     |              | `uv sync --group dev` (unchanged)                                        |      |
| `server`      | service | `install`    | host `fastapi dev --reload` (unchanged, autostart)                       | 8000 |
| `image:dev`   | job     |              | `docker build --target dev -t papyrus:dev .`                             |      |
| `server:dev`  | service | `image:dev`  | `docker run --rm --init -p 8001:8000`, mounts, `fastapi dev --host 0.0.0.0 --entrypoint server:app` | 8001 |
| `image:prod`  | job     |              | `docker build -t papyrus .`                                              |      |
| `server:prod` | service | `image:prod` | `docker run --rm --init -p 8002:8000 -e PAPYRUS_API_KEY=dev papyrus`     | 8002 |
| `test`        | job     | `image:dev`  | `docker run --rm --init`, mounts, `pytest -m ""`                         |      |

- Mounts for `server:dev` and `test`: `./server:/app/server:ro`, `./test:/app/test:ro`, `./scripts:/app/scripts:ro`.
- `server:dev` env: `PAPYRUS_AUTH_DISABLED=true`, `WATCHFILES_FORCE_POLLING=true`. Inotify does not cross Docker
  Desktop bind mounts on Windows or macOS, so reload has to poll.
- `server:prod` runs the shipped image with no mounts and real auth (key `dev`), as a local smoke test of prod.
- The ports differ so all three servers can run side by side. Containers have no `--name`, so a hard kill never
  leaves behind a container that blocks the next start.
- The host `.env` is not passed into containers: its `PAPYRUS_CONFIG` points at a host path.

## Tests

- Tests that need the image (LibreOffice today) carry `@pytest.mark.full` and no `skipif`. The marker is registered
  in `pyproject.toml`.
- `pyproject.toml` sets `addopts = "-m 'not full'"`, so host `uv run pytest` deselects them.
- The image runs `pytest -m ""`. The empty expression clears the filter, so every test runs and a missing or moved
  LibreOffice fails the `full` tests instead of skipping them.
- `-m ""` appears in exactly two places, the dekit `test` task and the CI step, each with a short comment.
  Dropping it silently deselects the `full` tests again.
- todo: if pytest stops treating an empty `-m` as no filter, use `-m "full or not full"`.
- API tests that use the fake convert service stay unmarked and run everywhere.

## CI

`.github/workflows/check.yml`, triggered on `pull_request` and on `push` to `main`, one `ubuntu-latest` job:

1. `actions/checkout`, `docker/setup-buildx-action`.
2. `docker/build-push-action`: `target: dev`, `load: true`, `tags: papyrus:dev`,
   `cache-from: type=gha`, `cache-to: type=gha,mode=max`.
3. Run inside `papyrus:dev` with the same mounts as dekit `test`: `ruff format --check`, `ruff check`,
   `pytest -m ""`.
4. `docker/build-push-action`: default target (`runtime`), `push: false`, `cache-from: type=gha`, which proves
   the prod image builds.

- Timing: about 5-8 min with a cold cache (LibreOffice apt layer), about 1-2 min warm.
- `concurrency` cancels superseded runs on the same ref.

## Out of Scope

- todo: publish the image to GHCR on tags or `main` (release ADR).
- todo: a parallel host job (uv, fast tests) if CI feedback on a cold cache becomes a bottleneck.
- todo: Python version matrix, only once more than one version is supported.
- Devcontainers and compose: dekit already covers the same workflows.

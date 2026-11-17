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
| Default loop    | Host uv (`local/server` task, `uv run pytest`), `full` tests deselected                   |
| Full loop       | `dev` Docker stage with source bind-mounted, driven by dekit                              |
| Orchestration   | dekit only, no compose: `cmd` is an argv array, so relative `-v ./x:/app/x` works in any shell |
| Image layout    | One multi-stage Dockerfile, `runtime` stays last so `docker build .` builds prod           |
| CI              | One GitHub Actions job: stamp version and watermark, build `dev`, lint and test inside it, build `runtime`, push it to GHCR on `v*` tags |
| Versioning      | Hand-pushed `vX.Y.Z` tags; `svaraborut/semver` (always preview) and `svaraborut/watermark` stamp the build |
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
- `/app` is read-only for the runtime user, so `dev` points the pytest and ruff caches at `/tmp`
  (`PYTEST_ADDOPTS`, `RUFF_CACHE_DIR`).

## dekit Tasks

| Task                 | Type    | Deps                 | Command                                                       | Port |
|----------------------|---------|----------------------|---------------------------------------------------------------|------|
| `local/install`      | job     |                      | `uv sync --group dev`                                         |      |
| `local/server`       | service | `local/install`      | host `fastapi dev --reload`, `PAPYRUS_CONFIG=config.yml`      | 8000 |
| `docker/build-dev`   | job     |                      | `docker build --target dev -t papyrus:dev .`                  |      |
| `docker/dev`         | service | `docker/build-dev`   | `docker run --name papyrus-dev -p 8001:8000`, mounts, image CMD | 8001 |
| `docker/build-prod`  | job     |                      | `docker build -t papyrus .`                                   |      |
| `docker/prod`        | service | `docker/build-prod`  | `docker run --name papyrus-prod -p 8002:8000 -e PAPYRUS_API_KEY=dev papyrus` | 8002 |
| `docker/test`        | job     | `docker/build-dev`   | `docker run --name papyrus-test`, mounts, `pytest -m "full or not full"` |      |

- Host tasks live in `local/`, container tasks in `docker/`.
- `local/server`, `docker/dev` and `docker/prod` all read the gitignored `config.yml` at the repo root (copy
  `config.example.yml`). The containers mount it at `/etc/papyrus/config.yml`. `docker/test` does not: tests build
  their own config.
- todo: `config.yml` must exist before a container starts. Docker Desktop creates a missing bind source as a
  directory, which breaks config loading on the host and in the containers.
- Every `docker run` uses `--rm --init`. Mounts for `docker/dev` and `docker/test`: `./server:/app/server:ro`,
  `./test:/app/test:ro`, `./scripts:/app/scripts:ro`.
- `docker/dev` env: `PAPYRUS_AUTH_DISABLED=true`, `WATCHFILES_FORCE_POLLING=true`. Inotify does not cross Docker
  Desktop bind mounts on Windows or macOS, so reload has to poll. It watches only `server`: polling all of `/app`
  (venv included) misses changes.
- `docker/prod` runs the shipped image with only the config mounted and real auth (key `dev`), as a local smoke
  test of prod.
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

`.github/workflows/check.yml`, triggered on `pull_request`, `push` to `main` and `v*` tags, one `ubuntu-latest`
job:

1. `actions/checkout`, `docker/setup-buildx-action`.
2. Versioning, always run (see Versioning): `svaraborut/semver` stamps `server/version.py`, then
   `svaraborut/watermark` builds the watermark from its version.
3. `docker/build-push-action`: `target: dev`, `load: true`, `tags: papyrus:dev`, GHA cache in scope `dev`.
4. Run inside `papyrus:dev` with the same mounts as dekit `docker/test` plus `ruff.toml`: `ruff format --check`,
   `ruff check`, `pytest -m "full or not full"`.
5. `docker/build-push-action`: default target (`runtime`), `build-args: WATERMARK=<watermark>`, reads the `dev`
   and `runtime` cache scopes, writes `runtime`. This always proves the prod image builds.

- Timing: about 5-8 min with a cold cache (LibreOffice apt layer), about 1-2 min warm.
- `concurrency` cancels superseded runs on the same ref.

## Versioning

Git tags are the only source of truth: releases are `vX.Y.Z` tags pushed by hand, nothing is committed back.

| Step      | Config                                                                                       |
|-----------|----------------------------------------------------------------------------------------------|
| Version   | `svaraborut/semver@v1`, `preview: true` always, `files: server/version.py`                    |
| Watermark | `svaraborut/watermark@v2`, `version: <semver version>`, `format: vVERSION #SHA7 DATE`          |

- semver checks for a tagged HEAD before it checks preview. A `vX.Y.Z` tag at HEAD gives that version, anything else
  renders the next version as `X.Y.Z-rcN` (N = commits since the last release). With preview always on it never
  pushes tags, so the job keeps `contents: read`. Every run logs a version.
- Example watermarks: `v1.2.3 #2e6dcdd 2026-11-17` on a release, `v1.2.4-rc5 #9f1c0de 2026-11-20` on `main` or a PR.
- `server/version.py` holds `__version__ = '0.0.0'` (stamped in the CI workspace, never committed) and
  `__watermark__ = os.environ.get('WATERMARK') or None`.
- The `runtime` stage ends with `ARG WATERMARK=` and `ENV WATERMARK=$WATERMARK`. They are last so they never
  invalidate cached layers. `dev` has no watermark.
- `GET /` (`HelloResponse`) returns `version` from `__version__` and `watermark` from `__watermark__`. Locally that
  is `0.0.0` and `null`.
- `pyproject.toml` keeps the `0.0.0` placeholder: `uv.lock` records the project version, so stamping it breaks
  `uv sync --locked`. The server is never published as a wheel, so nothing reads it.
- todo: packages added to the repo get their own `__version__` stamped the same way. Stamping their
  `pyproject.toml` means building with `uv sync --frozen` behind a `uv lock --check` step that runs before stamping.
- todo: semver only recognises `vX.Y.Z`. A hand-pushed `v1.3.0-rc1` gets semver's `1.3.0-rcN` while
  `metadata-action` tags the image from the git tag, so decide on one RC naming scheme before building RCs.

## Release

- A `v*` tag runs the same checks, logs in to GHCR with `GITHUB_TOKEN` (`packages: write`) and pushes the
  runtime image to `ghcr.io/<owner>/<repo>`.
- Tags come from `docker/metadata-action`: `v1.2.3` publishes `1.2.3`, `1.2` and `latest`. A `v*` tag that is not
  semver publishes nothing and fails the push.
- Only tag builds publish. `main` and PRs build the runtime image, watermark included, without pushing it.
- todo: amd64 only, add `linux/arm64` with QEMU once arm hosts need it.

## Out of Scope

- todo: a parallel host job (uv, fast tests) if CI feedback on a cold cache becomes a bottleneck.
- todo: Python version matrix, only once more than one version is supported.
- Devcontainers and compose: dekit already covers the same workflows.

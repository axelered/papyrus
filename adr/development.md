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
| CI              | `ci.yml` on `main` and `release.yml` on dispatch, both over the reusable `_server`, `_sdk_python`, `_sdk_typescript` |
| Versioning      | One version for server and SDKs, tagged by `svaraborut/semver@v3` after the checks pass, capped at minor |
| Release         | GHCR image, PyPI and npm in parallel and idempotent, Trusted Publishing, then the GitHub release |
| Skip guard      | `full` marker deselected by `addopts` on the host, re-included with `-m "not live"` in the image; `live` deselected in both |

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
| `docker/test`        | job     | `docker/build-dev`   | `docker run --name papyrus-test`, mounts, `pytest -m "not live"` |      |

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
- `pyproject.toml` sets `addopts = "-m 'not full and not live'"`, so host `uv run pytest` deselects them.
- `@pytest.mark.live` tests call a paid OCR backend (`adr/ocr.md`). They run only by hand with `-m live`, take the
  backend from `PAPYRUS_LIVE_*` env variables and skip when those are missing.
- The image runs `pytest -m "not live"`, which overrides the filter, so every test runs and a missing or
  moved LibreOffice fails the `full` tests instead of skipping them. Not `-m ""`: dekit on Windows drops the empty
  argument.
- The override appears in exactly two places, the dekit `docker/test` task and the CI step, each with a short
  comment. Dropping it silently deselects the `full` tests again.
- API tests that use the fake convert service stay unmarked and run everywhere.

## CI

Two entry workflows call three reusable ones (`on: workflow_call`). GitHub allows no subfolders in
`.github/workflows`, so the `_` prefix marks the reusable files.

```
.github/workflows/
  ci.yml               push to main                 → _server, then _sdk_python and _sdk_typescript
  release.yml          workflow_dispatch on main    → the same checks, then tag, publish, GitHub release
  _server.yml          dev image: ruff, pytest, docs bundle; runtime image built, not pushed
  _sdk_python.yml      runtime image as server under test: SDK tests on 3.11 and 3.14, wheel check
  _sdk_typescript.yml  runtime image as server under test: Node 22, Node 24, Bun, package check
.github/actions/
  sdk-server/          composite: build the runtime image from cache, start it, wait for /health
```

- `ci.yml` triggers on `push: branches: [main]`. `pull_request` stays commented out for now.
- `concurrency` cancels superseded runs on pull requests only (`cancel-in-progress` on the event name), so
  every `main` commit gets a full status.
- No path filters: every check is 1-2 min warm, and filtered required checks would stay pending forever.
- The SDK checks `needs:` the server check and run in parallel with each other: a server failure stops the run
  before them, and they always find a warm `dev` and `runtime` cache. Each suite fails in its own job, so a red
  check names the package.
- Checks stamp no version: the runtime image they build is thrown away. Only the publish jobs stamp.
- Commit subjects in the changelog are untrusted. They go into action `with:` inputs and reach `run:` scripts only
  through `env:`, never through `${{ }}` inside a script.

### `_server.yml`

Permissions `contents: read`, on `ubuntu-latest`. Each check step runs when the dev image built, also after an
earlier check failed (`!cancelled()`), so one run reports format, lint, tests and docs together:

1. `actions/checkout`, `docker/setup-buildx-action`.
2. `docker/build-push-action` with `target: dev`, `load: true`, `tags: papyrus:dev`, GHA cache in scope `dev`.
3. Inside `papyrus:dev`, mounting `./server`, `./test`, `./scripts`, `./sdk` and `./ruff.toml` read-only:
   `ruff format --check`, `ruff check`. `sdk` is mounted only here: one Ruff config covers the whole repository.
4. `pytest -m "not live"` with the mounts of dekit `docker/test` (`server`, `test`, `scripts`), with the comment
   about the `-m` override (see Tests).
5. Docs bundle (`axelered-docs` skill): `scripts/openapi.py` inside `papyrus:dev` writes `openapi.json` (`jq empty`
   fails on stray stdout), then `docs.tar.gz` packs `bundle.json`, `openapi.json`, `docs/` and `bruno/` without
   `bruno/environments` and `.env*`, and the step fails if either slipped in. Uploaded on every run as the
   `docs-bundle` artifact (`retention-days: 7`), so the release job reuses it.
6. `docker/build-push-action` with the default target (`runtime`), no push, reading the `dev` and `runtime` cache
   scopes and writing `runtime`. This proves the prod image builds.

### `.github/actions/sdk-server`

The CI twin of dekit `sdk/server` (`adr/sdk-python.md`): `docker/setup-buildx-action`, `docker/build-push-action`
of `runtime` with `load: true`, `tags: papyrus`, both cache scopes read, nothing written; then
`docker run -d --init --name papyrus-sdk -p 8003:8000 -e PAPYRUS_API_KEY=dev papyrus`, no config file, and a
`curl --retry` wait on `/health`. Callers set `PAPYRUS_BASE_URL=http://localhost:8003` and `PAPYRUS_API_KEY=dev`
and print `docker logs papyrus-sdk` on failure. A failed start skips every step after it: the SDK steps are gated on
a setup step (`setup-uv`, `bun install`) that only runs after a successful start.

### `_sdk_python.yml`

One named step per Python, against the same server, each with `if: ${{ !cancelled() }}` so both versions report.
uv downloads the interpreters, no `setup-python`.

```
setup               checkout, sdk-server, setup-uv
Test (Python 3.11)  uv run --locked --directory sdk/python -p 3.11 pytest
Test (Python 3.14)  uv run --locked --directory sdk/python -p 3.14 pytest
Wheel (Python 3.11) uv build --wheel, clean 3.11 venv in $RUNNER_TEMP, install, import outside the source tree
Server logs         docker logs papyrus-sdk, on failure only
```

### `_sdk_typescript.yml`

One named step per runtime, `actions/setup-node` switching the Node on `PATH` between them. Test steps run with
`if: ${{ !cancelled() }}`, so every runtime reports after an earlier one failed.

```
setup                 checkout, sdk-server, setup-bun, bun install --frozen-lockfile
Check                 bun run check
Test (Node 22)        setup-node 22 (latest 22.x: running sources needs 22.18) → bun run test:node
Test (Node 24)        setup-node 24, the active LTS, pinned → bun run test:node
Test (Bun)            bun run test:bun
Pack                  npm pack → papyrus.tgz, required files present
Package (Node 22.12)  setup-node 22.12, the declared minimum → npm install --no-save, node use.mjs, use.cjs
Package (Bun)         bun use.mjs
Package (types)       tsc -p tsconfig.nodenext.json, tsc -p tsconfig.bundler.json
```

Fixtures and checks are those of `adr/sdk-typescript.md` Tests.

## Versioning

Releases are `vX.Y.Z` tags pushed by `svaraborut/semver@v3` from the release workflow, never by hand. Nothing is
committed back. Server, Python SDK and TypeScript SDK share one version.

| Input          | Value                                                            |
|----------------|------------------------------------------------------------------|
| `preview`      | `${{ inputs.dry-run }}` in the tag job, `true` everywhere else   |
| `minimum-bump` | `minor`                                                          |
| `maximum-bump` | `minor`: going to `1.0.0` is a deliberate edit of `release.yml`  |
| `aliases`      | `''`: moving `vX`, `vX.Y`, `latest` git tags only serve Actions repositories |

- With both bounds at `minor`, every release bumps the minor version, whatever the Conventional Commits since the
  highest reachable `vX.Y.Z` say: breaking changes cannot reach `1.0.0`, and patch releases need `minimum-bump:
  patch`. Starting from another version means pushing that tag by hand once.
- With `preview: true` on a commit tagged `vX.Y.Z`, semver outputs that version as is. Every publish job checks
  out the tag and runs semver that way to stamp its files, so no version passes between jobs and a re-run builds
  the same thing.
- `changelog` lists the commits since the previous `vX.Y.Z`, newest first, with links. On a tagged HEAD the tag
  itself is skipped, so a release lists exactly its own commits.
- Releases are always `X.Y.Z`: there is no prerelease channel.

Stamped files (`files:`), all keeping `0.0.0` in git:

```
server/version.py                   __version__
sdk/python/papyrus_sdk/version.py   __version__, read by hatchling as the wheel version
sdk/typescript/package.json         version
sdk/typescript/src/version.ts       export const VERSION
```

- The root `pyproject.toml` keeps its `0.0.0` placeholder: `uv.lock` records the project version, so stamping it
  breaks `uv sync --locked`. The server is never published as a wheel, so nothing reads it.
- `svaraborut/watermark@v2` (`version: <semver version>`, `format: vVERSION #SHA7 DATE`) runs in the image publish
  job only. The `runtime` stage ends with `ARG WATERMARK=` and `ENV WATERMARK=$WATERMARK`, last so they never
  invalidate cached layers; `dev` has no watermark.
- `GET /` (`HelloResponse`) returns `version` from `__version__` and `watermark` from `__watermark__`. Locally that
  is `0.0.0` and `null`.

## Release

`release.yml`, `on: workflow_dispatch` with one input `dry-run` (boolean, default `false`). Every job is guarded
by `github.ref == 'refs/heads/main'`. `concurrency: release`, never cancelled: two releases must not race for the
same tag.

```
server ┬→ sdk-python     ┬→ tag ┬→ publish-image ┬→ github-release
       └→ sdk-typescript ┘      ├→ publish-pypi  ┤
                                └→ publish-npm   ┘
```

1. Checks: the three reusable workflows, as in `ci.yml`. They run again on purpose: the tag is pushed only after
   they pass on the exact commit, so a red run never leaves a tag behind.
2. `tag` (`contents: write`): semver with the inputs above. It writes version and changelog to the job summary
   and exports `version`, `tag` and `changelog`. With `dry-run` the run stops here: the preview version shows what
   would be released, nothing is pushed or published.
3. Publishes, in parallel, each checking out `needs.tag.outputs.tag` and stamping as in Versioning. Each is
   idempotent, so a failure is fixed with "Re-run failed jobs" on the same run, never a new dispatch:
   - `publish-image` (`packages: write`): GHCR login with `GITHUB_TOKEN`, watermark, `docker/metadata-action`
     (`type=semver` on the tag: `{{version}}`, `{{major}}.{{minor}}` and `latest`), then `runtime` with
     `build-args: WATERMARK=<watermark>` pushed to `ghcr.io/<owner>/<repo>`. Pushing the same tags again is
     harmless.
   - `publish-pypi` (environment `pypi`, `id-token: write`): `uv build --directory sdk/python`, then
     `pypa/gh-action-pypi-publish` with `packages-dir: sdk/python/dist` and `skip-existing: true`.
   - `publish-npm` (environment `npm`, `id-token: write`): `actions/setup-node` with the npm registry and npm
     >= 11.5.1 (Trusted Publishing), `bun install`, then `npm publish` (`prepack` builds `dist`) to the `latest`
     dist-tag, with provenance. A step skips it when `npm view @axelered/papyrus@<version> version` already
     answers: npm refuses to publish a version twice.
4. `github-release` (`needs` all three publishes, `contents: write`): downloads `docs-bundle` and runs
   `softprops/action-gh-release@v2` with the tag as name, `docs.tar.gz` attached, and as body the changelog
   followed by the install lines: `ghcr.io/<owner>/<repo>:X.Y.Z`, `pip install papyrus-sdk==X.Y.Z`,
   `npm install @axelered/papyrus@X.Y.Z`. A release page appears only once every artifact exists; a re-run
   updates it.

- Only `tag` and `github-release` can write to the repository, and only the PyPI and npm jobs get `id-token`.
- Environments `pypi` and `npm` have no reviewers: pressing dispatch is the manual gate. They exist because
  Trusted Publishing is bound to an environment name.

### Bootstrap

One-time setup before the first release:

1. PyPI: a pending Trusted Publisher for `papyrus-sdk` (repository `axelered/papyrus`, workflow `release.yml`,
   environment `pypi`). It works before the project exists.
2. npm: the `axelered` organization, one manual first publish of `@axelered/papyrus` (Trusted Publishing can be
   configured only on an existing package), then the trusted publisher (workflow `release.yml`, environment
   `npm`), and the token of the first publish revoked.
3. Repository environments `pypi` and `npm`, no reviewers.
4. `check.yml` deleted, replaced by `ci.yml`.

## Out of Scope

- todo: notify `axelered/docs` with a `docs-release` dispatch after the GitHub release, once the `axelered-docs`
  GitHub App and its `DOCS_APP_ID`/`DOCS_APP_PRIVATE_KEY` secrets are set up; until then a docs `deploy` run picks
  it up.
- todo: enable `pull_request` in `ci.yml`, then protect `main` requiring the three checks.
- todo: review and simplify the Dockerfile stage layout, independent of the workflows.
- todo: Helm chart lint and OCI push (`adr/chart.md`) join `_server.yml` and the publishes later.
- todo: amd64 only, add `linux/arm64` with QEMU once arm hosts need it.
- todo: a parallel host job (uv, fast tests) if CI feedback on a cold cache becomes a bottleneck.
- todo: Python version matrix for the server, only once more than one version is supported.
- Devcontainers and compose: dekit already covers the same workflows.

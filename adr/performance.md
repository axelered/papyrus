# Performance

Where request latency goes, how it is measured, and the log of improvements. Covers the pool services of
`adr/architecture.md` (render, markdown, convert), detection and the browser of `adr/browser.md`.

## Goals

- Every latency claim comes from a measurement in the Linux image, never from reading code.
- Cold costs (process start, imports, lazy libraries, browser launch) stay off the request path where cheap to do.
- Hot costs are the floor: the work itself, measured per operation.

## Measuring

| Tool | Measures | How |
|---|---|---|
| `PAPYRUS_TIMING=true` | Per request stages, end to end | `Server-Timing` header and one log line per request (`server/utils/timing.py`), on in `docker/dev` |
| `scripts/bench.py` | Cold and hot latency per service operation | `docker exec papyrus-dev python -m scripts.bench`, prints the markdown table used below |

- Wrap new service steps and route stages in `stage('<name>')`; names are HTTP tokens, `browser.*` for the browser.
- Bench method: a fresh service with one worker per operation, `cold` is its first call, `hot` the median of the
  next 3 on the same worker. The forkserver is started before measuring, it boots once per server process.
- Bench samples are tiny (`test/fixtures/samples`): the numbers isolate fixed costs, not document size.
- The bench keeps server imports inside functions: spawn and forkserver import `__main__` into every worker.
- Compare before and after by mounting a `git worktree` of the old revision as `/app/server` in `papyrus:dev`.

## Baseline

Linux image (`papyrus:dev`), Python 3.14.10, 32 CPUs, mean of two passes, milliseconds.

| Service | Operation | Cold before | Cold after | Hot |
|---|---|---:|---:|---:|
| detect | png | 74 | 89 | 5 |
| render | png 1600x1200 to webp 256 | 2211 | 955 | 70 |
| render | pdf A4 to webp 256 | 2150 | 906 | 5 |
| markdown | html | 3510 | 2345 | 16 |
| markdown | html with chunking | 3587 | 2766 | 18 |
| markdown | docx | 3137 | 2079 | 11 |
| convert | docx page 1 to pdf | 2536 | 1259 | 44 |
| browser | html screenshot 1280x800 | 836 | 895 | 128 |

- "before" is the revision before the worker import fix below, "after" includes it. Hot times did not change.
- detect and browser run in the server process, so the fix does not apply to them; their deltas are noise.
- `processes: auto` starts one worker per CPU and pebble hands a task to any idle worker, so up to `processes`
  requests per pool pay the cold cost, again after every `max_tasks` recycle and every `--reload`.

### Website thumbnail

One remote `POST /v1/thumbnail` with a cold render worker, before the fix:

```
parse 0.1, materialize 591, detect 7, browser.acquire 0, browser.context 14, browser.goto 1625,
browser.capture 92, browser.close 9, screenshot 1836, render 1698, total 4136
```

`render` was the cold worker import, `materialize` the httpx pre-fetch of the HTML the browser loads again.

## Improvements

### Worker imports without the app

- Pool workers import their task's module, which imported the `server` package. `server/__init__.py` built the
  app (`create_app()`, about 0.85s) and `server.utils.problem` imported FastAPI for the exception handlers (about
  0.65s), so every cold worker paid about 1.5s before its first task.
- Decision: the ASGI entrypoint moves to `server/main.py` (`server.main:app`), `server/__init__.py` stays empty,
  and `server.utils.problem` imports FastAPI inside the functions that need it.
- Guard: `test/test_app.py` imports every worker module in a fresh interpreter and fails on `fastapi` or
  `server.factory` in `sys.modules`.
- Result: about 1.2s less per cold render, markdown and convert worker.

### Warm services

Moves every cold cost (worker imports, LibreOffice init, Magika model, Chromium launch) to startup, so boot memory
and failures match steady state.

- Config: `workers.warmup: bool = true`. `false` keeps everything lazy, for local development and `--reload`.
  todo: per-service warmup with the per-service sizing in YAML.
- `server/utils/pool.py` (moved from `server/services/pool.py`, a utility without domain knowledge):
  `ProcessPool(name, processes, timeout, max_tasks=0, initializer=None)`. `async start()` starts the pebble pool
  and returns once every worker has run its initializer, `async aclose()` stops it and joins in a thread,
  `__aenter__`/`__aexit__` call both. `run()` still starts an unstarted pool lazily.
- Layering (`utils/` never imports `server.errors`): the pool raises its own `PoolTimeout(TimeoutError)` and
  `PoolFailed(RuntimeError)` (worker died, unexpected worker exception, logged as today) and lets `ProblemError`
  from `server/utils/problem.py` through. `services/base.py` `pool_errors()`, a context manager used once around
  each service's `run`, maps them to `504 processing_timeout` and `500 processing_failed`.
- `server/services/base.py`: `Service` with no-op `async warm()` and `async aclose()`, `__aenter__` returns self,
  `__aexit__` calls `aclose()`. Services override only `warm` and `aclose`. `__aenter__` does not warm: the
  registry owns the switch and warms services concurrently.
- Services keep their pool by composition (no `PoolService`): the pool's generic `run` stays private and the
  browser has no pool.

| Service | `warm()` | Worker initializer |
|---|---|---|
| markdown | `await self._pool.start()` | `init_worker()`: imports markitdown and chonkie, builds the module `_converter` |
| convert | `await self._pool.start()` | `init_worker(profiles)`: `_ensure_office`, LibreOffice up (about 330 MB per worker at boot) |
| render | `await self._pool.start()` | `init_worker()`: imports pypdfium2 and Pillow |
| detect | loads `Magika()` in a thread | none |
| browser | launches Chromium through `_acquire`/`_release` | none |

- Initializers are top-level module functions (pickled into workers, never bound methods) that call the same lazy
  setup the task functions use, so lazy and warm paths share code. pebble reruns them for every replaced worker
  (`max_tasks` recycle, timeout kill), off the request path while the other workers serve.
- `ConvertService.aclose()` also removes the profiles root.
- Lifespan (`factory.py`): one `AsyncExitStack` enters `HttpFetcher` and `Services`; `Services` enters each service
  into its own stack. Teardown is reverse order and exception safe; `Services.aclose` and `_close_pools` go away.
- `Services.warm()` runs every `warm()` with `asyncio.gather` under one `asyncio.timeout(120)` and logs on
  `papyrus.startup`: `warming <names>`, `<name> warm in <s>s` per service, `services warm in <s>s`. Off logs
  `warmup off, services start on first use`; the topology line drops "started on first use".
- Failure: any `warm()` error (logged as `<name> warmup failed`) or the timeout (naming the unfinished services)
  fails startup, the process exits non-zero and the orchestrator keeps the previous version.
- todo: spike pebble before building `start()`: whether all workers spawn on the first `schedule`, whether an
  initializer exception surfaces or loops respawning, and how to know every worker is initialized (a no-op task
  returning `os.getpid()` repeated until `processes` distinct pids answer, bounded by the warmup timeout).
- Tests: services built directly stay lazy; fixtures switch `close()` to `aclose()` or `async with`.

## Remaining cold costs

Incremental imports of a cold worker in the image after the fix:

| Import | ms | Note |
|---|---:|---|
| `pebble` | 411 | pebble's own worker loop, paid by every worker regardless |
| `server.types.common` | 252 | pydantic models, needed for `ImageFormat` and `MAX_IMAGE_SIDE` |
| `markitdown` | 1234 | markdown workers only, includes magika and onnxruntime |
| `chonkie` | 224 | markdown workers with chunking |
| `pypdfium2` | 80 | render workers on the first pdf |

## Follow-ups

- todo: website thumbnails pre-fetch the HTML with httpx and the browser fetches it again (~0.6s); let Chromium
  navigate the URL directly as `/v1/screenshot` does, detection from the response content type.
- todo: the catch-all `context.route` sends every subresource through Python and disables the HTTP cache; route
  only the document navigation, abort the rest after serving it.
- todo: HTML thumbnails capture a lossless PNG up to 1280x2160 and the render worker re-decodes it; capture webp
  or downscale through the CDP clip scale.
- todo: worker types without pydantic (`ImageFormat`, `MAX_IMAGE_SIDE`, `Fit`) save about 0.25s per cold worker;
  only worth it with `workers.warmup: false`.

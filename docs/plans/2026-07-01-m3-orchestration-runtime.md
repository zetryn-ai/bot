# M3 — Orchestration Runtime (`python -m zetryn_bot`)

**Date:** 2026-07-01
**Status:** Shipped (v0.3.0)
**Target version:** v0.3.0

Give the bot a runnable entry point. M2 shipped `BotPipeline` as a library
(one candidate in → one `Decision` out). M3 turns that into a long-running
process: start every enabled `Scanner`, fan their candidates through a
shared queue into a worker pool that drives the pipeline, and shut down
cleanly on a signal. Still no execution, no wallet, no Redis on the hot
path — those are later milestones.

## 1. Goals

1. `python -m zetryn_bot` (and a `zetryn-bot` console script) boots a
   runtime that runs indefinitely and drains cleanly on SIGINT/SIGTERM.
2. Runs with **zero configuration** — the zero-arg scanners (Dexscreener ×3,
   GeckoTerminal ×2, Raydium) always run, so a bare `.env`-less checkout
   still does something useful for dev.
3. Multiple scanners run concurrently, each crash-supervised; a shared
   `asyncio.Queue` + worker pool decouples scan rate from decision rate and
   caps LLM concurrency.
4. Duplicate tokens (same mint from multiple scanners within a short window)
   are processed once, not N times.
5. LLM is **optional** — built from the framework's `ProviderConfig` if the
   provider keys are present in the environment; otherwise the runtime falls
   back to the rule-only path (`llm_client=None`) and keeps running.
6. Everything lives **inside the `zetryn_bot` package** (ships in the wheel);
   nothing at the repo root.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Agent topology | **One shared `BotPipeline`** built from `build_scanner`. Per-channel routing to `build_sniper` / `build_graduation` is deferred to a dedicated future milestone (after M6 — it needs `PositionContext` etc.). `BotPipeline` is already agent-agnostic, so routing later = several pipelines + a dispatcher, not a rewrite. |
| 2 | Decision sink | **`LogSink` only.** `RedisDecisionSink` deferred to whenever a real Decision consumer exists (M7 notifier / M9 dashboard). The 3 `scanner.*` Redis channels are candidate transport, not Decision transport. |
| 3 | Scanner selection | **Auto from keys + optional override.** Zero-arg scanners always on; key-requiring scanners on only when their key is present (skip + warn otherwise). `SCANNERS_ENABLED` (CSV of `.name`s) narrows the set when set. |
| 4 | LLM client | **Optional, auto-detect.** `try_build_llm_client()` builds via the framework's `ProviderConfig` (env-var *names*; framework resolves values from `.env`). Returns `None` when keys absent → rule-only. Bot `Settings` gains **no** LLM-key fields (boundary: framework owns LLM keys). |
| 5 | Candidate flow | **Shared `asyncio.Queue` + worker pool.** Producers (scanners) enqueue; W workers dequeue and call `pipeline.process`. Backpressure via `maxsize`; concurrency capped at W. |
| 6 | Dedup | **In-memory `mint → monotonic ts` cache with a TTL window** (default 60s), checked producer-side before enqueue. |

Additional constraints:

- **`ai-agent` (framework) is untouched.** M3 only imports from the
  installed `zetryn-trading` wheel.
- Twitter enricher (async `TwitterAccountPool.initialize()` + cookie files)
  and Telegram scanner (interactive `.session` login) are wired only when
  configured; the Twitter *enricher* wiring is deferred (documented as a
  known limitation) to keep the registry synchronous. Everything else
  (Helius, Rugcheck, Jupiter, GMGN enrichers) is wired.
- Zero changes to `zetryn_bot/scanners/*`, `models/token.py`, and the M2
  `adapters/` + `pipeline/` layers.

## 3. Architecture

```
Scanner tasks (N, supervised)   Shared Queue        Worker pool (W)
─────────────────────────────   ────────────        ───────────────
DexscreenerNewPairs ─┐
BirdeyeTrending ─────┤   dedup   ┌──────────────┐   ┌────────────────────┐
PumpfunStream ───────┼──gate───▶ │ asyncio.Queue│──▶│ _consume → pipeline│
  ... (enabled) ─────┘  (mint,ts)│  maxsize=K   │   │   .process()       │
                                 └──────────────┘   └─────────┬──────────┘
                                                              │ Decision
                                                              ▼ LogSink
```

### 3.1 New modules (all under `zetryn_bot/`)

| Path | Responsibility |
|---|---|
| `__main__.py` | Entry point — `python -m zetryn_bot` / `zetryn-bot`. Loads `Settings`, sets up logging, builds the runtime, installs signal handlers, `asyncio.run`. |
| `runtime/__init__.py` | package |
| `runtime/dedup.py` | `DedupCache(ttl_s, now_fn=time.monotonic)` — `seen(mint) -> bool`, injectable clock for tests. |
| `runtime/registry.py` | `build_enabled_scanners(settings)` + `build_enrichers(settings)` — config → instances, skip-when-key-missing. |
| `runtime/llm.py` | `try_build_llm_client()` — build framework LLM client from `ProviderConfig`; `None` on missing keys. |
| `runtime/orchestrator.py` | `Orchestrator` — owns the queue, producer tasks (supervised scanners), worker pool, shared `aiohttp.ClientSession`, and `start()`/`shutdown()`. |

### 3.2 `Settings` additions (scanner/runtime layer — no boundary breach)

```python
scanners_enabled: list[str] = []   # CSV override; empty = all auto-enabled
telegram_channels: str = ""        # JSON, consumed by build_channels_from_config
workers: int = 4
queue_size: int = 1000
dedup_ttl_s: float = 60.0
```

## 4. Orchestrator

```python
class Orchestrator:
    def __init__(self, pipeline, scanners, *, workers=4, queue_size=1000, dedup_ttl_s=60.0): ...

    async def _produce(self, scanner, session):
        async for cand in scanner.stream(session):
            if self.dedup.seen(cand.address):
                continue
            await self.queue.put(cand)          # backpressure when full

    async def _consume(self, session):
        while True:
            cand = await self.queue.get()
            try:
                await self.pipeline.process(cand, session)
            except Exception:
                logger.exception("pipeline error")   # a worker never dies
            finally:
                self.queue.task_done()

    async def start(self):
        self.session = aiohttp.ClientSession()
        producers = [asyncio.create_task(supervise(s.name, self._produce, s, self.session))
                     for s in self.scanners]
        consumers = [asyncio.create_task(self._consume(self.session)) for _ in range(self._workers)]
        self._tasks = producers + consumers

    async def shutdown(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.session.close()
```

- Scanners run under `supervise()` (crash → restart 5s). Workers are *not*
  supervised — their internal `try/except` per candidate means the loop
  never dies; only cancellation stops them.
- One shared `aiohttp.ClientSession` for all producers + workers + enrichers.

## 5. Entry point + lifecycle

```python
# zetryn_bot/__main__.py
async def _run(settings):
    llm = try_build_llm_client()               # None → rule-only
    agent = build_scanner(llm_client=llm)
    pipeline = BotPipeline(agent, enrichers=build_enrichers(settings), sink=LogSink())
    orch = Orchestrator(pipeline, build_enabled_scanners(settings),
                        workers=settings.workers, queue_size=settings.queue_size,
                        dedup_ttl_s=settings.dedup_ttl_s)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await orch.start()
    await stop.wait()
    await orch.shutdown()
```

Graceful shutdown: signal → set event → cancel all tasks → close session.
No force-kill; loguru `enqueue=True` flushes on exit.

## 6. Testing (offline, no network/LLM)

| Test file | Coverage |
|---|---|
| `tests/test_dedup.py` | first-seen `False`, within-window `True`, post-TTL `False` (injected `now_fn`, no `sleep`). |
| `tests/test_registry.py` | no keys → 6 zero-arg scanners; `birdeye_api_keys` → +2 Birdeye; `SCANNERS_ENABLED` filter; enrichers skip missing-key sources + correct order. |
| `tests/test_llm_wiring.py` | `try_build_llm_client()` → `None` when LLM env unset (monkeypatched). |
| `tests/test_orchestrator.py` | fake scanner (yields 3 then stops) + `ListSink` → 3 decisions; duplicate mint → processed once; raising scanner → supervised restart, runtime survives. |

Plus `scripts/m3_smoke.py` — offline: `Orchestrator` with one in-process
fake scanner + `build_scanner(llm_client=None)` + `ListSink`, drain, shutdown,
assert decisions emitted. Added to CI alongside m1/m2.

## 7. Out of scope

- Per-channel agent routing / specialized contexts — future milestone (post-M6).
- `RedisDecisionSink` / Decision fan-out — M7 / M9.
- Twitter enricher wiring, Telegram deep config — partial / deferred.
- Execution, wallet, persistence, notifier, dashboard — M4–M9.

## 8. Execution sub-phases

| Sub-phase | Scope |
|---|---|
| **B1** | `runtime/dedup.py` + `runtime/registry.py` + `runtime/llm.py` + `Settings` fields. Unit tests for each. |
| **B2** | `runtime/orchestrator.py` + `__main__.py` + `[project.scripts]`. Orchestrator tests + `scripts/m3_smoke.py`. |
| **B3** | Version → `0.3.0`, CHANGELOG, README (runtime usage), ROADMAP M3 → ✅, CI adds m3_smoke + pytest already there, design doc Status → Shipped. Release commit + tag + GitHub release. |

## 9. Definition of done

1. `python -m zetryn_bot` boots, runs zero-arg scanners with no `.env`, and
   drains cleanly on Ctrl-C.
2. `zetryn_bot/runtime/{dedup,registry,llm,orchestrator}.py` + `__main__.py`
   shipped; `[project.scripts] zetryn-bot` wired.
3. `python -m pytest` green (M2's 17 + ~10 new).
4. `ruff check` + `ruff format --check` clean on `zetryn_bot/ tests/ scripts/`.
5. `scripts/m3_smoke.py` runs successfully offline.
6. `__version__ = "0.3.0"` in `__init__.py` + `pyproject.toml`.
7. CHANGELOG `## [0.3.0]`, ROADMAP M3 ✅, this doc Status Shipped.

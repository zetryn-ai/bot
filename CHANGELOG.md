# Changelog

All notable changes to `zetryn-bot` will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.4] — 2026-07-04

### Added

- **End-to-end observability in the logs.** The runtime now traces the full
  pipeline so a run is analysable without guessing:
  - **Fetch** — `poll_loop` logs `fetched N candidate(s)` per scanner (debug),
    so every polling source's activity is visible.
  - **Enrich** — the pipeline logs a consolidated post-enrich line (debug) with
    `source`, liquidity, mcap, volume, holders, top10%, and GMGN
    safety/smart/kol/sniper/bundler — the full enriched state, not just GMGN.
  - **Decision** — `LogSink` now shows `source=<scanner[,enrichers]>`, the
    framework `scores`, and the AI verdict: `ai_score` / `ai_rec` / `ai_reason`
    when the LLM analyst ran, or `ai=skipped(no-llm-or-hard-gate)` when a
    candidate was rejected by a hard gate before reaching the LLM (so the
    absence of an AI score is explained, not silent).

## [0.3.3] — 2026-07-04

### Added

- **GMGN enricher logs a debug line on successful enrichment** (`safety`,
  `smart`, `kol`, `sniper`, `holders`) so a working GMGN key is observable in
  the runtime logs — previously success was silent and indistinguishable from
  a disabled enricher. Run with `LOG_LEVEL=DEBUG` to see it.

## [0.3.2] — 2026-07-04

### Fixed

- **Decision logs are no longer silently dropped.** `LogSink`, the enrichment
  loop, and the pipeline runner logged without binding a `component`, so
  `setup_logger`'s `"component" in extra` filter discarded every decision and
  pipeline-error record. They now bind a component and appear in the logs.

### Changed

- **GMGN enricher fails loud-once on auth rejection instead of flooding.** On a
  401 (`AUTH_KEY_INVALID`) the enricher now logs one actionable warning (with
  the server's message + where to fix the key) and disables GMGN for the rest
  of the run, rather than retrying — and re-logging — on every candidate.
  Response bodies are now logged on unexpected statuses for diagnosability.

### Added

- **`scripts/gmgn_check.py`** — one-shot GMGN key checker. Makes a single
  authenticated `token/info` read and prints PASS/FAIL with the server's exact
  response, so a key can be validated without running the whole bot. (The
  enricher's auth was verified correct against GMGN's official CLI — host,
  path, `X-APIKEY` + `client_id`, curl_cffi to clear Cloudflare — so the
  earlier 401s were an invalid key, not a code bug.)

## [0.3.1] — 2026-07-04

### Fixed

- **LLM keys in `.env` are now visible to the framework.** `__main__` calls
  `load_dotenv()` at startup so provider keys (`GROQ_API_KEY`, etc.) placed in
  `.env` reach `os.environ`, where `zetryn-trading`'s provider resolver reads
  them. Before this, the runtime loaded `.env` only into the bot's `Settings`
  (which has no LLM fields), so LLM keys in `.env` were silently ignored and
  the runtime always stayed rule-only. The LLM path now activates as
  documented when a provider key is present.

## [0.3.0] — 2026-07-02

**M3 — Orchestration runtime shipped.** The bot now has a runnable entry
point: `python -m zetryn_bot` (or the `zetryn-bot` console script) boots the
enabled scanners concurrently and drives them through the M2 pipeline until
it receives a shutdown signal.

### Added

- **`zetryn_bot.__main__`** — entry point. Loads `Settings`, sets up logging,
  builds the runtime, installs SIGINT/SIGTERM handlers, and runs until
  signalled, then drains cleanly. Runs with no `.env` (zero-arg scanners
  always run). Registered as the `zetryn-bot` console script.
- **`zetryn_bot.runtime.orchestrator.Orchestrator`** — runs each scanner as a
  crash-supervised producer feeding a bounded `asyncio.Queue`; a worker pool
  dequeues and drives `BotPipeline.process`. Decouples scan rate from decision
  rate (backpressure) and caps concurrency (and LLM calls) at the worker count.
- **`zetryn_bot.runtime.registry`** — `build_enabled_scanners()` +
  `build_enrichers()`: config → instances. Zero-arg scanners always on;
  key-requiring sources on only when their key is present (skip + warn).
  `SCANNERS_ENABLED` narrows the set.
- **`zetryn_bot.runtime.dedup.DedupCache`** — collapses duplicate mints seen
  across scanners within a TTL window (injectable clock).
- **`zetryn_bot.runtime.llm.try_build_llm_client`** — builds an LLM client
  from the framework's `ProviderConfig` when a provider key is present
  (`GROQ_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY`, `LLM_MODEL`
  override); returns `None` (rule-only) otherwise. The bot never reads the key
  value — the framework resolves it, preserving the LLM-key boundary.
- New `Settings` fields: `scanners_enabled`, `telegram_channels`, `workers`,
  `queue_size`, `dedup_ttl_s`.
- `scripts/m3_smoke.py` + 17 new tests (dedup, registry, llm, orchestrator).
  CI runs all three smoke scripts + pytest.

### Fixed

- `Settings` now annotates its CSV list fields (`helius_api_keys`,
  `birdeye_api_keys`, `scanners_enabled`) with `NoDecode`, so an empty
  `HELIUS_API_KEYS=` in `.env` no longer crashes with a JSON parse error
  before the CSV validator runs. (Latent since M1; first surfaced when M3
  actually loads `Settings` at runtime.)

## [0.2.0] — 2026-07-01

**M2 — Wire scanners to `zetryn-trading` shipped.** The bot is now able to
run a candidate through a real framework agent and get a `Decision` back,
in-process, no service boundary.

### Added

- **`zetryn_bot.adapters.token_input`** — `to_token_input(candidate)`, a
  pure mapping from `TokenCandidate` to the framework's `TokenInput`. No
  I/O; unit-tested against every non-trivial field group (market,
  activity, holders, contract, wallets, pumpfun, social/twitter). Also
  handles the holder-percentage rescale (bot stores 0–100, framework
  expects 0–1) and the scanner-name → `TokenSource` literal narrowing.
- **`zetryn_bot.pipeline.enrich.enrich_candidate()`** — sequential
  composition of `TokenEnricher` implementations; a raising enricher is
  skipped (logged) rather than blocking the rest of the chain.
- **`zetryn_bot.pipeline.sinks`** — `DecisionSink` Protocol, `LogSink`
  (production default), `ListSink` (test fixture). A Redis sink is
  deferred to M3.
- **`zetryn_bot.pipeline.runner.BotPipeline`** — agent-agnostic runner
  (`BotPipeline(agent, enrichers=..., sink=..., config=...)`). Adapter
  failures emit a synthetic `abort` decision (`flags={"synthetic": True}`)
  instead of crashing the loop; agent exceptions are caught and logged the
  same way.
- **`scripts/m2_smoke.py`** — offline smoke test running a real
  `build_scanner(llm_client=None)` against a healthy and a dangerous
  synthetic candidate.
- First test suite: 17 tests (`tests/`) — unit coverage for the adapter,
  enrichment, sinks, and runner, plus 2 integration tests against a real
  `build_scanner` graph. Wired into CI (`ruff.yml` now runs `pytest`).

### Dependencies

- Added `zetryn-trading>=1.1.0` from PyPI. (The M2 design doc originally
  planned a git+ssh commit-SHA pin, written before `zetryn-trading` v1.0.0
  landed on PyPI on 2026-06-28 — corrected once the PyPI release existed.)

## [0.1.0] — 2026-06-28

**M1 — Scanner refactor & baseline shipped.** First minor release after
the v0.0.0 foundational import. The 11 scanner sources copied from the
cdexio production bot are now standardised behind two clear Protocols
and a consistent code style, ready for the M2 wire-up to
`zetryn-trading`.

### Added

- **`zetryn_bot.scanners.protocol`** — two runtime-checkable Protocols:
  - `Scanner` — `def stream(session) -> AsyncIterator[TokenCandidate]`
    for continuous discovery sources (polling, streaming, social).
  - `TokenEnricher` — `async def enrich(mint, candidate, session) ->
    TokenCandidate` for on-demand mint-lookup sources.
- **`zetryn_bot.scanners._common`** — `poll_loop()` and `fetch_json()`
  helpers removing the boilerplate that was duplicated across every
  polling scanner.
- **`zetryn_bot.scanners.enrichers/`** — subfolder for the 5 enricher
  modules (helius, rugcheck, jupiter, gmgn_openapi, twitter). The
  Protocol-different concerns get a Protocol-different folder.
- **9 scanner classes** across 6 source modules (Scanner Protocol):
  `BirdeyeTrending`, `BirdeyeNewListing`, `DexscreenerNewPairs`,
  `DexscreenerTrending`, `DexscreenerBoost`, `GeckoTerminalNewPools`,
  `GeckoTerminalTrending`, `PumpfunStream`, `RaydiumNewPools`,
  `TelegramScanner`.
- **5 enricher classes** (TokenEnricher Protocol): `HeliusEnricher`,
  `RugcheckEnricher`, `JupiterEnricher`, `GmgnEnricher`,
  `TwitterEnricher` (+ supporting `TwitterAccountPool`).
- **Strict ruff config** in pyproject (`E`, `F`, `I`, `B`, `UP`, `SIM`,
  `RUF`) plus `.pre-commit-config.yaml` so the rule set is enforced on
  every commit going forward.

### Changed

- **Scanners no longer publish to Redis directly.** The cdexio
  `await publish_*(redis, ...)` calls inside scanner bodies are gone;
  scanners yield `TokenCandidate` values via `stream()` and the caller
  picks the sink (Redis, `zetryn-trading` agent, test mock, etc.).
- **Enrichers no longer mutate their input.** `enrich()` returns
  `candidate.model_copy(update={...})`; the original candidate is
  preserved. Matches the `TokenEnricher` Protocol contract.
- **Categorization corrected.** `jupiter`, `gmgn_openapi`, and `twitter`
  were initially listed as Scanners in the M1 design doc but their
  cdexio implementations are on-demand mint-lookup enrichers — moved to
  `scanners/enrichers/` accordingly. Final M1 layout: **6 scanners + 5
  enrichers**, not 9 scanners + 2 enrichers.
- **All Indonesian inline comments translated to English.** Logic is
  preserved exactly; only language unified per the M1 design decision
  #4 (all-English baseline for the public template repo).
- **Module docstring template applied to every scanner.** Each module
  documents its source URL, auth env vars, mechanism, rate limits, and
  populated/emitted fields per the M1 design's §4.3.

### Removed

- **`scanners/__init__.py::build_scanner_tasks`** — the cdexio
  orchestrator that hard-wired which scanners ran with what cadence and
  pushed everything to the legacy `scanner.*` Redis channels.
  Orchestration is M3 scope; the Phase 1 import already stripped it
  from `__init__.py`. The new docstring reflects the Protocol-based
  pattern: build scanners individually, drive them however your runtime
  prefers.
- **`scanners/__init__.py` enricher re-exports** — `helius` and
  `rugcheck` are no longer importable via `zetryn_bot.scanners.helius`.
  Use `zetryn_bot.scanners.enrichers.helius` (or the
  `scanners.enrichers` package namespace).

### Design

- M1 design doc:
  [`docs/plans/2026-06-28-m1-scanner-refactor.md`](docs/plans/2026-06-28-m1-scanner-refactor.md).
  Six locked decisions, Protocol contracts, file reorg, style + language
  config, five execution sub-phases (B1–B5), verification checklist.

### Out of scope (deferred to later milestones)

- Wire scanners to `zetryn-trading` decision agents — M2.
- Orchestration runtime (`main.py`, supervised multi-scanner runner) — M3.
- Tests — M2 (around the integration boundary, not the scanners in isolation).
- Execution layer (swap, position, reconciliation) — M4.
- Wallet management (encryption, monitor, sweeper) — M5.

See [`ROADMAP.md`](ROADMAP.md) for the full milestone plan.

## [0.0.0] — 2026-06-28

**Foundational import.** Scanner source modules copied from a working
cdexio production memecoin bot with absolute imports rewritten to the
`zetryn_bot.*` namespace and all decision-tier logic (scorer, filter,
risk agent, executor, wallet, notifier, persistence) stripped out.
Package scaffolding (`pyproject.toml`, `LICENSE`, `.gitignore`,
`.env.example`, `README.md`) included. No usable runtime yet — see M1
for the first cleanup pass.

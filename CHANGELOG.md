# Changelog

All notable changes to `zetryn-bot` will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] — 2026-07-11

**M10a — Exit intelligence shipped.** Exits can now be decided by the
framework's PL1 lifecycle agent (rule mode) instead of the static
TP/SL/max-hold triple. Off by default (`LIFECYCLE_ENABLED=false` keeps
v0.8.0 behaviour bit-for-bit). Design doc:
[docs/plans/2026-07-11-m10a-exit-intelligence.md](docs/plans/2026-07-11-m10a-exit-intelligence.md).

### Added

- **`zetryn_bot/execution/lifecycle.py`** — `LifecycleEngine`: builds a
  `PositionContext` per monitor tick and runs `strategies.agents.lifecycle`
  (deterministic rule mode, no LLM per tick). Gates in framework order:
  emergency → hard stop-loss → time stop → **trailing stop** → TP.
  The trailing stop is the new alpha: arms after the position peaks past
  `EXIT_TRAILING_ARM_PNL_PCT` (+20% default) and exits when
  `EXIT_TRAILING_DRAWDOWN_PCT` (50% default) of the peak value is given
  back — "momentum died" exits bank profit instead of round-tripping to
  the -15% SL or the 30-minute time stop.
- **`PositionTracker(lifecycle=...)`** — optional engine; when present,
  `check_once` asks it for the exit decision instead of the static rules.
  New close reasons `trailing_stop` and `emergency` join the existing
  taxonomy (`take_profit` / `stop_loss` / `max_hold` unchanged).
- **Settings** — `lifecycle_enabled`, `exit_trailing_arm_pnl_pct`,
  `exit_trailing_drawdown_pct`.

### Notes

- Peak PnL is tracked in-memory: a restart re-arms the trailing stop from
  the first post-restart quote (persisting peaks is a follow-up).
- TP ladder is a single full-exit rung — the Executor protocol sells whole
  positions only; multi-rung partial exits are M10.1.
- ROADMAP M10 split: M10a (this) / M10b (per-signal entry routing, planned).

## [0.8.0] — 2026-07-11

**M8 — Deployment shipped.** The bot now runs 24/7 on a VPS as a Docker
container supervised by the Docker daemon: it restarts itself after crashes
and reboots, persists to Postgres, notifies via Telegram, and updates with
one command.

### Added

- **`Dockerfile`** — two-stage build on `python:3.12-slim` (git only in the
  build stage), non-root `bot` user, NLTK VADER lexicon pre-downloaded at
  build time, `CMD python -m zetryn_bot`. Verified: image boots with zero
  config and runs the full rule-only pipeline (the M3 contract).
- **`.dockerignore`** — secrets (`.env`, `wallet.enc`, sessions, cookies) can
  never enter the build context.
- **`docker-compose.vps.yml`** — bot service with `restart: unless-stopped`,
  `logs/` + `data/` bind mounts, json-file log rotation, joining an external
  Docker network to reach a shared Postgres via container DNS (no
  host-published DB port involved).
- **`scripts/deploy.sh`** — one-command update, run on the server:
  `git pull → docker compose build → alembic upgrade head → up -d → status`.
- **CI**: `docker` job — builds the image on every push and runs the M1
  smoke test inside it.
- README: deployment section + status table caught up (M6–M8).

### Fixed

- **Telegram scanner hot-restart loop** — a producer that returned cleanly
  (no session / no channels / connect failure) was respawned immediately by
  the supervisor, re-logging the same ERROR every second. Permanently
  disabled states now park the producer (`_idle_forever`) instead of
  returning.
- **`twitter_login` cookie loading in containers** — the library writes a
  `.cache` directory inside its own site-packages at import time, which
  fails with `EACCES` for a non-root user; the image now pre-creates it
  writable.

## [0.7.0] — 2026-07-05

**M7 — Observability shipped.** A Telegram notifier surfaces the events that
need a human — trade opens/closes with full token + AI decision detail,
circuit-breaker trips, critical errors, and scanner rate-limit / LLM
key-rotation warnings — plus a periodic heartbeat and crash-dump capture.
Off by default; a misconfigured or unreachable Telegram never affects the
trading pipeline.

### Added

- **`zetryn_bot.notify`** package:
  - `protocol.py` — `Notifier` Protocol (`async def notify(text, *, dedup_key=None)`).
  - `telegram.py` — `TelegramNotifier` (Telegram Bot API `sendMessage`,
    in-memory dedup per `dedup_key` within a rolling window) and `NullNotifier`
    (true no-op, used when disabled/unconfigured).
  - `log_bridge.py` — forwards ERROR+ log records always, and WARNING records
    only when they match a rate-limit/rotation keyword — no call-site
    rewiring needed across scanners/framework.
  - `heartbeat.py` — periodic uptime + open-position/PnL summary, registered
    as an `Orchestrator` background task.
  - `format.py` — rich message formatting: `build_trade_meta()` captures a
    full token + decision detail snapshot (market metrics, holder data,
    safety flags, GMGN/Twitter signals, AI score/recommendation/reasoning) at
    buy-time, carried on `Position`/`SwapRequest` for reuse on open and close
    notifications (never persisted to the DB — notification richness only).
- **Notifier wiring**: `ExecutionSink` (trade opened), `PositionTracker`
  (trade closed, PnL, exit reason), `RiskManager` (circuit breaker tripped —
  edge-triggered once per day, not per rejected trade).
- **Crash dump**: an unhandled exception in `main()` writes the full
  traceback to `crash-<unix-ts>.log` and pushes a short excerpt to Telegram
  before re-raising (process still exits non-zero).
- New `Settings`: `NOTIFY_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `NOTIFY_DEDUP_WINDOW_S` (default 900s), `HEARTBEAT_INTERVAL_S` (default
  3600s).
- `scripts/m7_smoke.py` — offline dedup-logic checks + an optional live
  Telegram send when credentials are present in the environment.

### Changed

- `.env.example` now sets `EXIT_MAX_HOLD_S=10800` (3h) as the documented
  starting point instead of leaving it at the code default of 1800s (30min)
  — gives momentum room to develop given alerts already passed hard gates +
  AI analysis, while still bounding capital lock-up risk in a stagnant token.
  The `Settings` code default is unchanged; this only affects the template.

## [0.6.0] — 2026-07-05

**M6 — Persistence (PostgreSQL) shipped.** Open positions, closed-trade
history, and the daily-loss circuit breaker now survive a restart or crash.
In live mode, restored positions are reconciled against on-chain balances
before trading resumes. The framework's `DecisionLog` / `ReflectiveNode`
(never previously wired) can now be activated via a Postgres-backed store.

### Added

- **`zetryn_bot.db`** package (SQLAlchemy 2.0 async + `asyncpg`):
  - `models.py` — four tables: `positions`, `closed_trades`, `risk_state`
    (one row per day, for the circuit breaker), `decision_log_kv`.
  - `position_repo.py` — persists open positions + closed trades, and bridges
    the in-memory `time.monotonic()` `opened_at` to a wall-clock column and
    back on load (so `max_hold_s` stays correct across restarts).
  - `risk_repo.py` — persists/restores the daily realized PnL.
  - `memory_store.py` — `PostgresStore`, implementing the framework's
    `MemoryStore` Protocol; backs `DecisionLog` (activating `ReflectiveNode`
    when `ENABLE_DECISION_LOG=true`).
  - `engine.py` — async engine/session factory + a startup connectivity probe.
- **Alembic** migrations (`alembic/`), autogenerated from the ORM models;
  `env.py` reads `DATABASE_URL` from `Settings`.
- **Startup persistence wiring** — `RiskManager.load()` restores the circuit
  breaker; `PositionTracker.load_and_reconcile()` restores open positions and,
  in live mode, verifies each against its on-chain token balance (mismatch →
  `needs_review`, excluded from the monitor loop, logged at `WARNING`). If
  Postgres is unreachable the runtime falls back to in-memory state — never a
  crash.
- `SolanaRpc.get_token_balance_for_mint()` for reconciliation.
- New `Settings`: `DATABASE_URL`, `ENABLE_DECISION_LOG`.
- `docker-compose.yml` (Postgres 16) for local/VPS dev; `scripts/m6_smoke.py`
  (round-trip when the DB is reachable, in-memory-fallback check otherwise);
  17 new tests. CI gains a Postgres service and runs the DB integration tests.

### Changed

- `RiskManager.record_close` is now `async` (persists on close);
  `PositionTracker` and `RiskManager` accept an optional `repo=` (omitting it
  keeps the M4/M5 in-memory behaviour).

## [0.5.1] — 2026-07-04

### Fixed

- **Blank `LIVE_PRIORITY_FEE_LAMPORTS=` in `.env` crashed Settings.** An empty
  optional-int env var arrives as `""`, which int-parsing rejected — so copying
  `.env.example` (where it's blank by default, meaning "Jupiter auto") would
  crash startup. A `before` validator now maps a blank string to `None`.
- **`.env.example` had inline comments on blank-value lines** (`WALLET_PASSPHRASE=`,
  `LIVE_PRIORITY_FEE_LAMPORTS=`) — python-dotenv treats the comment as the value
  on a blank line, so `WALLET_PASSPHRASE` silently became the comment text.
  Comments moved to their own lines. Regression tests added.

## [0.5.0] — 2026-07-04

**M5 — Wallet management (live execution) shipped.** The bot can now sign and
submit real Solana swaps via a `LiveExecutor` that implements the same
`Executor` Protocol as M4's `PaperExecutor` — the risk and position layers
never changed.

### Added

- **`zetryn_bot.wallet.keystore.Wallet`** — loads a Fernet-encrypted keyfile
  (PBKDF2-derived key from `WALLET_PASSPHRASE`) into an in-memory
  `solders.Keypair`. `repr()`/`str()` only ever show the public key; the
  private key never appears in a log, exception message, or version control.
- **`scripts/wallet_init.py`** — one-time interactive setup (`getpass`, hidden
  input) that creates the encrypted keyfile. Neither the private key nor the
  passphrase ever touches disk unencrypted, a log line, or shell history.
- **`zetryn_bot.execution.rpc.SolanaRpc`** — thin `solana-py` wrapper: submit,
  confirm, and — critically — check on-chain signature status *before* ever
  considering a retry. A confirmation timeout is never treated as failure
  without checking on-chain truth first, which is what prevents double-swaps.
- **`zetryn_bot.execution.live.LiveExecutor`** — real swaps: Jupiter builds an
  unsigned transaction for the wallet's pubkey, `solders` signs it locally
  (<1ms), `SolanaRpc` submits and confirms. A per-mint `asyncio.Lock`
  serializes concurrent buy/sell calls for the same mint. A `BalanceCache`
  (TTL-refreshed, not queried inline) guards every buy against insufficient
  SOL + gas reserve without adding an RPC round-trip to the decision hot path.
- **`execution.jupiter.JupiterQuote.build_swap_tx`** — extends the M4 quote
  client with a swap-transaction builder (`lite-api.jup.ag/swap/v1/swap`,
  verified live). Never signs or sends — only builds.
- **`EXECUTION_MODE=paper|live`** with layered guards: live activates only
  when `EXECUTION_ENABLED=true` AND `EXECUTION_MODE=live` AND the wallet
  keyfile decrypts successfully. Any guard failure logs an error and falls
  back to paper — never a crash, never a silent live activation. When live is
  active, a `WARNING`-level banner announces the wallet pubkey and trade cap.
- **`RiskManager` gains `wallet_max_trade_sol`** — an absolute per-trade cap
  applied only in live mode, independent of `base_size_sol × confidence`, as
  a last-resort guard against misconfiguration when real funds are at stake.
- New deps: `solders`, `solana`, `base58`, `cryptography`.
- `scripts/m5_smoke.py` + 23 new tests (keystore, balance cache, live executor
  incl. concurrency + timeout/retry-safety, risk cap). **No real transaction
  is ever sent in tests or CI** — the live path is only exercised against
  mocked RPC/Jupiter.

### Note

Our own compute overhead (decision → sign → submit call) is sub-millisecond;
end-to-end swap latency is dominated by the Jupiter API and Solana
confirmation time (hundreds of ms to a few seconds), which is outside the
bot's control — see the M5 design doc for the full latency discussion.

## [0.4.2] — 2026-07-04

### Fixed

- **`max_positions` could be overshot under concurrency.** With multiple
  pipeline workers, several `ExecutionSink.emit` calls could each pass the
  max-positions / already-held checks during a buy's `await` window before any
  of them registered its position — opening more positions than the cap (or
  double-buying a mint). `ExecutionSink` now serializes the check→buy→add under
  an `asyncio.Lock`, so the cap and per-mint dedup hold exactly.

## [0.4.1] — 2026-07-04

### Added

- **`RISK_BUY_ACTIONS`** — configurable set of `Decision` actions that trigger a
  paper buy (default `alert`). Set `alert,watch` to also paper-trade the
  analyst's watchlist. The AI-first scanner rarely emits `alert` on fresh
  memecoins (market/social dimensions are almost always weak, so the best
  candidates cap at `watch` ~0.5–0.65), so `alert`-only can sit idle for a long
  time; acting on `watch` lets you gather outcome data on whether the analyst's
  watchlist has edge. Live deployments keep the conservative `alert`-only default.

## [0.4.0] — 2026-07-04

**M4 — Execution layer (paper-trading) shipped.** The bot can now act on its
own alerts: `python -m zetryn_bot` with `EXECUTION_ENABLED=true` opens paper
positions at real Jupiter quote prices, tracks them, and auto-exits on
take-profit / stop-loss / max-hold — recording PnL. No transactions, no
keypair, no funds; live swaps land with M5.

### Added

- **`zetryn_bot.execution`** package:
  - `jupiter.JupiterQuote` — read-only Jupiter quote client (current
    `lite-api.jup.ag/swap/v1` host; the legacy `quote-api.jup.ag/v6` is dead).
  - `executor.PaperExecutor` (+ `Executor` Protocol, `SwapRequest`, `Position`,
    `ClosedTrade`) — simulated fills at real quote prices, denominated in SOL so
    token decimals never need resolving. A `LiveExecutor` slots in at M5.
  - `risk.RiskManager` — buys on `alert` at/above a confidence floor, sizes
    `base_size_sol × confidence`, caps concurrent positions, and trips a
    daily-loss circuit breaker.
  - `position.PositionTracker` — in-memory open positions + a supervised monitor
    loop that polls Jupiter per position and exits on TP/SL/max-hold; periodic
    win-rate / PnL stats.
- **`ExecutionSink` + `TeeSink`** — execution plugs in as a `DecisionSink`
  behind a tee (log + execute); `BotPipeline` / `Orchestrator` unchanged. The
  orchestrator gained a `background_tasks` hook for the monitor loop.
- New `Settings`: `EXECUTION_ENABLED` (default false), `RISK_BASE_SIZE_SOL`,
  `RISK_MIN_CONFIDENCE`, `RISK_MAX_POSITIONS`, `RISK_DAILY_LOSS_LIMIT_SOL`,
  `EXIT_TP_PCT`, `EXIT_SL_PCT`, `EXIT_MAX_HOLD_S`, `EXEC_POLL_INTERVAL_S`.
- `scripts/m4_smoke.py` + 4 new test files (risk, paper executor, position
  tracker, execution sink). CI runs the m4 smoke.

With `EXECUTION_ENABLED=false` (the default) the runtime is byte-for-byte the
M3 behaviour.

## [0.3.6] — 2026-07-04

**The AI analyst path now works end to end.**

### Fixed

- **Multi-key `GROQ_API_KEY` (and other providers) were sent as a single
  invalid key.** The bot's convention is a comma-separated key list in one env
  var, but the framework's resolver reads the whole value as ONE key — so a
  17-key `GROQ_API_KEY` was sent as one giant Bearer token and every LLM call
  failed with `401 Invalid API Key`, silently falling back to a conservative
  rule-only "skip". `try_build_llm_client` now comma-splits the env value and
  passes literal keys, so the framework's KeyPool rotates over all of them.
  With this fixed, the analyst produces real `alert` / `watch` / `skip`
  verdicts with per-aspect scores and reasoning.

### Added

- **Decision gate thresholds are now configurable via env** (`GATE_MIN_LIQUIDITY_USD`,
  `GATE_MIN_VOLUME_1H`, `GATE_MAX_TOP10_PCT`, `GATE_MIN_HOLDERS`,
  `GATE_MAX_BUNDLER_WALLETS`, `GATE_MIN_GMGN_SAFETY_SCORE`). They feed the
  framework `ScannerConfig` that the hard gates check before the LLM. Defaults
  match the framework; loosen them to let more candidates reach the analyst.
  `min_liquidity_usd` / `min_volume_1h` are floored at 1 to avoid a
  division-by-zero in the framework's market gate when set to 0.

## [0.3.5] — 2026-07-04

**All discovery sources working: Raydium fixed, Twitter + Telegram wired.**

### Fixed

- **Raydium new-pool scanner produced 0 candidates.** `/pools/info/list` has no
  creation-time sort (`openTime`/`createTime` → HTTP 500), and the old
  `poolType=all` + `poolSortField=default` returned ~1.6-year-old blue-chip
  pools that the 24h age filter dropped. Switched to `poolType=standard` +
  `poolSortField=apr24h` (new low-liquidity pools carry the highest APR),
  which surfaces genuinely fresh pools (0 → ~60–130 candidates per poll).
- **Twitter enricher was broken by `twitter_login` API drift.**
  `Client.load_cookies` is now a coroutine (must be awaited — it was silently
  never awaited, so cookies never loaded) and `SearchTimelineProduct.LIVE` was
  renamed `LATEST`. Both fixed; Twitter now populates mentions / sentiment /
  engagement / velocity again.
- **Telegram scanner could hang the entire runtime.** It called
  `client.start()`, which prompts for phone/OTP on stdin when no session
  exists — a background task stuck in `input()` stalled the event loop so even
  SIGTERM couldn't drain. It now `connect()`s and checks
  `is_user_authorized()`, disabling itself with a clear message when no session
  is present instead of prompting.

### Added

- **`scripts/telegram_login.py`** — one-time interactive Telegram login that
  creates the Telethon `.session` file. Phone / OTP / password are entered at
  the terminal and never touch the runtime logs; the runtime then reuses the
  session with no credentials.
- **Twitter enricher is now wired into the runtime.** `build_twitter_enricher`
  (async) loads + initializes the cookie-backed account pool and appends the
  enricher last (runs once a symbol is known); it is skipped with a warning
  when no cookie store is configured, never blocking the pipeline.

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

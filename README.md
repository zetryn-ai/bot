# zetryn-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

**Solana memecoin scanner sources and bot scaffolding** — the I/O companion
to [`zetryn-trading`](https://github.com/zetryn-ai/ai-agent).

> [!WARNING]
> **v0.5.0 — pre-alpha, M1–M5 shipped.** 6 scanners + 5 enrichers conform
> to the `Scanner` / `TokenEnricher` Protocols; a `BotPipeline` wires a
> candidate through enrichment → adapter → a compiled `zetryn-trading`
> agent → a swappable `DecisionSink`; `python -m zetryn_bot` boots a
> runtime that runs the enabled scanners concurrently with crash-safe
> supervision; a paper-trading engine (M4) risk-sizes alerts into
> positions at real Jupiter prices; and a `LiveExecutor` (M5) can sign and
> submit real swaps from an encrypted wallet, gated behind
> `EXECUTION_MODE=live` and layered safety guards. There is **no
> persistence or observability yet** — no DB, no notifier, no Redis on the
> decision hot path. Those land in subsequent milestones; see
> [`ROADMAP.md`](ROADMAP.md).

## Boundary mirror of zetryn-trading

```
zetryn-trading  : decides   (graph + LLM + rules)                     ← framework
zetryn_bot      : executes  (scanners → normalise → publish → swap)   ← this repo
                            (planned: persistence, observability, dashboard)
```

The framework decides; the bot executes. The framework never holds your
private key or touches the chain — this repo owns all wallet/signing I/O.

## What's in here (v0.5.0)

```
zetryn_bot/
├── __init__.py                    __version__ = "0.5.0"
├── __main__.py                    M3: runtime entry point (python -m zetryn_bot)
├── config.py                      Pydantic Settings (scanner + runtime env vars)
├── logger_setup.py                Loguru config
├── adapters/token_input.py        to_token_input() — pure TokenCandidate -> TokenInput
├── pipeline/                      M2: enrich -> adapt -> agent -> sink
│   ├── enrich.py                  enrich_candidate() — composes TokenEnricher chain
│   ├── sinks.py                   DecisionSink Protocol + LogSink/ListSink/TeeSink/ExecutionSink
│   └── runner.py                  BotPipeline — agent-agnostic runner
├── runtime/                       M3: orchestration runtime
│   ├── orchestrator.py            Orchestrator — queue + worker pool + lifecycle
│   ├── registry.py                build_enabled_scanners() + build_enrichers()
│   ├── dedup.py                   DedupCache — collapse duplicate mints (TTL)
│   └── llm.py                     try_build_llm_client() — optional LLM wiring
├── execution/                     M4/M5: paper + live execution engine
│   ├── jupiter.py                 JupiterQuote — quote + swap-tx build
│   ├── executor.py                Executor Protocol + PaperExecutor (M4)
│   ├── live.py                    LiveExecutor + BalanceCache (M5, real swaps)
│   ├── rpc.py                     SolanaRpc — submit/confirm/on-chain-check (M5)
│   ├── risk.py                    RiskManager — sizing, gates, circuit breaker
│   └── position.py                PositionTracker — open positions + exit loop
├── wallet/                        M5: encrypted keypair
│   └── keystore.py                Wallet — decrypt wallet.enc, no-key-in-log
├── scanners/                      6 SOURCES + 9 CLASSES (Scanner Protocol)
│   ├── protocol.py                Scanner + TokenEnricher Protocols
│   ├── _common.py                 poll_loop() + fetch_json() helpers
│   ├── birdeye.py                 BirdeyeTrending · BirdeyeNewListing
│   ├── dexscreener.py             DexscreenerNewPairs · ...Trending · ...Boost
│   ├── geckoterminal.py           GeckoTerminalNewPools · ...Trending
│   ├── pumpfun.py                 PumpfunStream (WebSocket; reconnect-safe)
│   ├── raydium.py                 RaydiumNewPools
│   ├── telegram.py                TelegramScanner (telethon channel monitor)
│   └── enrichers/                 5 SOURCES (TokenEnricher Protocol)
│       ├── helius.py              HeliusEnricher  (holder distribution, metadata)
│       ├── rugcheck.py            RugcheckEnricher (safety analysis)
│       ├── jupiter.py             JupiterEnricher (price fallback)
│       ├── gmgn_openapi.py        GmgnEnricher (entity-labeled wallets + safety)
│       └── twitter.py             TwitterEnricher + TwitterAccountPool
│                                   (VADER sentiment with crypto lexicon)
├── models/token.py                TokenCandidate — shared schema
├── storage/redis_client.py        Redis pub/sub transport + 3 channels
└── utils/
    ├── key_pool.py                APIKeyPool + BirdeyeKeyPool + HeliusKeyPool
    └── supervisor.py              Crash-safe async task supervisor
```

## What's NOT here yet (see [ROADMAP.md](ROADMAP.md))

- ❌ Redis-backed decision sink / fan-out to a dashboard — M7 / M9
- ❌ Per-channel agent routing (sniper / graduation) — M10
- ✅ Execution layer — **paper-trading** (swap engine, position, PnL) — M4 (done)
- ✅ Live on-chain swaps + wallet — encrypted keypair, `LiveExecutor` — M5 (done)
- ❌ Wallet monitoring / multi-wallet rotation / sweeper — future hardening
- ❌ Persistence — Postgres for `DecisionLog`, position state — M6
- ❌ Observability — Telegram/Discord notifier, heartbeat, crash dump — M7
- ❌ API server + dashboard — M9
- ❌ Docker / deployment — M8

## Install

```bash
# Requires Python 3.11+
pip install -e ".[dev]"

# Or with requirements.txt
pip install -r requirements.txt

# Install pre-commit hooks (recommended)
pip install pre-commit
pre-commit install
```

## Configure

```bash
cp .env.example .env
# Edit .env — minimal: REDIS_URL. Scanners with missing keys are skipped at runtime.
```

Redis is **not** required in M3 — the runtime is single-process and keeps
candidates in-memory. It comes back when a Redis-backed sink lands (M7/M9).
PostgreSQL is **not** required until M6.

## Run the runtime (M3)

```bash
python -m zetryn_bot          # or the console script: zetryn-bot
```

Boots every enabled scanner concurrently and fans their candidates through
the pipeline. With no `.env`, the zero-arg scanners (Dexscreener ×3,
GeckoTerminal ×2, Raydium) run and decisions are logged; add API keys to
enable the rest. Ctrl-C (SIGINT/SIGTERM) drains and shuts down cleanly.

Tunables (env / `.env`):

| Var | Default | Meaning |
|---|---|---|
| `SCANNERS_ENABLED` | *(empty = all auto)* | CSV of scanner `.name`s to keep |
| `WORKERS` | `4` | pipeline worker pool size (caps LLM concurrency) |
| `QUEUE_SIZE` | `1000` | candidate queue bound (backpressure) |
| `DEDUP_TTL_S` | `60` | window for collapsing duplicate mints |
| `LLM_MODEL` | *(provider default)* | override the LLM model when a key is set |

The runtime runs **rule-only** unless a provider key (`GROQ_API_KEY`,
`OPENROUTER_API_KEY`, or `GEMINI_API_KEY`) is present — those are resolved by
`zetryn-trading`, not by the bot's `Settings`.

## Paper-trade alerts (M4)

```bash
EXECUTION_ENABLED=true python -m zetryn_bot
```

`alert`s at/above `RISK_MIN_CONFIDENCE` are risk-sized (`RISK_BASE_SIZE_SOL ×
confidence`) into paper positions at real Jupiter quote prices — no
transaction, no keypair, no funds. Positions auto-close on `EXIT_TP_PCT` /
`EXIT_SL_PCT` / `EXIT_MAX_HOLD_S`. See `.env.example` for the full `RISK_*` /
`EXIT_*` knobs, including `RISK_BUY_ACTIONS=alert,watch` to also paper-trade
the analyst's watchlist.

## Go live (M5)

**Real funds. Read this before setting `EXECUTION_MODE=live`.**

```bash
python scripts/wallet_init.py     # one-time: paste your base58 private key,
                                   # choose a passphrase -> writes wallet.enc
```

Fund the printed address, set `WALLET_PASSPHRASE` in `.env` (kept separate
from `wallet.enc`), and a real `SOLANA_RPC_URL` (a bare API key/UUID is not
enough — e.g. `https://mainnet.helius-rpc.com/?api-key=<key>`). Then:

```bash
EXECUTION_ENABLED=true EXECUTION_MODE=live python -m zetryn_bot
```

Live activates **only** when every guard passes: `EXECUTION_ENABLED=true` AND
`EXECUTION_MODE=live` AND the wallet decrypts successfully. Any failure logs
an error and falls back to paper — never a crash, never a silent live
activation. When live is active you'll see a `WARNING`-level banner with the
wallet pubkey and `WALLET_MAX_TRADE_SOL` (an absolute per-trade cap,
independent of `RISK_BASE_SIZE_SOL`, as a last-resort guard).

`LiveExecutor` never blind-retries: a confirmation timeout triggers an
on-chain check before any retry decision, so a slow-but-successful swap is
never resent. Our own compute overhead (sizing, signing, bookkeeping) is
sub-millisecond; end-to-end swap time is dominated by the Jupiter API and
Solana confirmation (hundreds of ms to a few seconds) — outside the bot's
control.

## Use individual scanners

There's no `main.py` yet — scanners are independent coroutines you wire
into your own runtime. Example pattern:

```python
import asyncio
import aiohttp

from zetryn_bot.config import Settings
from zetryn_bot.storage import connect, publish_momentum
from zetryn_bot.scanners.dexscreener import DexscreenerNewPairs

async def main() -> None:
    settings = Settings()                        # loads .env
    redis = await connect(settings.redis_url)
    async with aiohttp.ClientSession() as session:
        scanner = DexscreenerNewPairs()          # implements Scanner Protocol
        async for candidate in scanner.stream(session):
            await publish_momentum(redis, candidate.model_dump())

asyncio.run(main())
```

## Use individual enrichers

```python
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher
from zetryn_bot.scanners.enrichers.rugcheck import RugcheckEnricher

helius = HeliusEnricher(api_keys=settings.helius_api_keys)
rugcheck = RugcheckEnricher(redis=redis)         # optional 1h cache

async for candidate in some_scanner.stream(session):
    enriched = await helius.enrich(candidate.address, candidate, session)
    enriched = await rugcheck.enrich(candidate.address, enriched, session)
    await sink(enriched)
```

## Wire a scanner to `zetryn-trading` (M2)

`BotPipeline` composes enrichment, the adapter, a compiled agent graph,
and a sink. It's agent-agnostic — pass any compiled `Graph`:

```python
import asyncio
import aiohttp
from strategies.agents.scanner import build_scanner
from zetryn.llm import LLMClient  # real usage — omit for the rule-only path

from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import LogSink
from zetryn_bot.scanners.dexscreener import DexscreenerNewPairs
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher

async def main() -> None:
    agent = build_scanner(llm_client=LLMClient(...))  # or None for gates-only
    pipeline = BotPipeline(
        agent,
        enrichers=[HeliusEnricher(api_keys=[...])],
        sink=LogSink(),
    )
    async with aiohttp.ClientSession() as session:
        await pipeline.run_scanner(DexscreenerNewPairs(), session)

asyncio.run(main())
```

`pipeline.process(candidate, session)` is the single-candidate primitive
if you're driving the loop yourself (e.g. from `scripts/m2_smoke.py`).

Each scanner / enricher module documents its own contract (env vars,
output channel hint, rate limits) in its module docstring. See the
source files.

## Protocol contracts

The two Protocols are runtime-checkable. Implementations do not need to
inherit from them; the engine just duck-types on the signatures.

```python
# zetryn_bot/scanners/protocol.py

@runtime_checkable
class Scanner(Protocol):
    name: str
    def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]: ...

@runtime_checkable
class TokenEnricher(Protocol):
    name: str
    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate: ...
```

## Style

Strict ruff: `select = [E, F, I, B, UP, SIM, RUF]`, line length 100,
double-quote format. See `pyproject.toml` `[tool.ruff]`. Pre-commit
hooks (`.pre-commit-config.yaml`) enforce this on every commit. CI will
fail on any ruff violation once CI lands (M2/M3).

## Provenance

Scanner source files originate from a working production memecoin bot;
were copied into this repo on 2026-06-28; had their absolute imports
rewritten to the `zetryn_bot.*` namespace; had all decision-tier logic
(scorer, filter, risk agent, executor, wallet, notifier, etc.) stripped
out; and were refactored to the `Scanner` / `TokenEnricher` Protocols
in M1 (v0.1.0). Those decision concerns belong to
[`zetryn-trading`](https://github.com/zetryn-ai/ai-agent); their bot-side
counterparts come back in their own milestones.

## License

MIT. See [LICENSE](LICENSE).

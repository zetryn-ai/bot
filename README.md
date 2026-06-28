# zetryn-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

**Solana memecoin scanner sources and bot scaffolding** — the I/O companion
to [`zetryn-trading`](https://github.com/zetryn-ai/ai-agent).

> [!WARNING]
> **v0.1.0 — pre-alpha, M1 (scanner refactor) shipped.** 6 scanners + 5
> enrichers conform to the `Scanner` / `TokenEnricher` Protocols; the
> code style is unified, ruff-checked, and pre-commit-hooked. There is
> **no runtime yet** — no `main.py`, no wire-up to `zetryn-trading`
> agents, no execution layer. Those land in subsequent milestones; see
> [`ROADMAP.md`](ROADMAP.md).

## Boundary mirror of zetryn-trading

```
zetryn-trading  : decides   (graph + LLM + rules)            ← framework
zetryn_bot      : executes  (scanners → normalise → publish) ← this repo
                            (planned: swap, wallet, orchestration)
```

The framework decides; the bot executes. The framework never holds your
private key or touches the chain; this repo will own all that I/O.

## What's in here (v0.1.0)

```
zetryn_bot/
├── __init__.py                    __version__ = "0.1.0"
├── config.py                      Pydantic Settings (scanner-only env vars)
├── logger_setup.py                Loguru config
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

- ❌ `main.py` / orchestration runtime — M3
- ❌ Integration with `zetryn-trading` agents (scanner output → decision graph) — M2
- ❌ Execution layer — swap (Jupiter), position manager, reconciliation — M4
- ❌ Wallet — encryption, key management, monitor, sweeper — M5
- ❌ Persistence — Postgres for `DecisionLog`, position state — M6
- ❌ Observability — Telegram/Discord notifier, heartbeat, crash dump — M7
- ❌ API server + dashboard — M9
- ❌ Tests — M2 (around the integration boundary, not in isolation)
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

You'll also need a running Redis instance (`redis-server`). PostgreSQL is
**not** required until M6.

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

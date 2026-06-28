# zetryn-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

**Solana memecoin scanner sources and bot scaffolding** — the I/O companion
to [`zetryn-trading`](https://github.com/zetryn-ai/ai-agent).

> [!WARNING]
> **Phase 1 scaffolding (v0.0.0 — pre-alpha).** This repo currently holds
> only the scanner source code copied from a working production bot, with
> imports rewritten and decision logic stripped out. There is **no
> orchestration yet** — no `main.py`, no wire-up to `zetryn-trading`
> agents, no execution layer. Roadmap is being drafted; see
> "What's next" below.

## Boundary mirror of zetryn-trading

```
zetryn-trading  : decides   (graph + LLM + rules)            ← framework
zetryn_bot      : executes  (scanners → normalise → publish) ← this repo
                            (planned: swap, wallet, orchestration)
```

The framework decides; the bot executes. The framework never holds your
private key or touches the chain; this repo will own all that I/O.

## What's in here (Phase 1)

```
zetryn_bot/
├── scanners/                   ← 11 source modules
│   ├── birdeye.py              BirdEye REST (trending / new listings)
│   ├── dexscreener.py          DexScreener REST (new pairs / trending / boost)
│   ├── geckoterminal.py        GeckoTerminal REST (new pools / trending)
│   ├── gmgn_openapi.py         GMGN OpenAPI (TLS-impersonated via curl-cffi)
│   ├── helius.py               Helius RPC + DAS API (token enrichment)
│   ├── jupiter.py              Jupiter price / quote REST
│   ├── pumpfun.py              Pump.fun WebSocket (new tokens + migrations)
│   ├── raydium.py              Raydium new-pool polling
│   ├── rugcheck.py             RugCheck safety scanner
│   ├── telegram.py             Telegram channel scraper (telethon)
│   └── twitter.py              Twitter scraper (twitter_login + VADER sentiment)
├── models/token.py             TokenCandidate — shared schema scanners populate
├── storage/redis_client.py     Redis pub/sub transport + 3 channels
├── utils/
│   ├── key_pool.py             APIKeyPool + BirdeyeKeyPool + HeliusKeyPool
│   └── supervisor.py           Crash-safe async task supervisor
├── config.py                   Pydantic Settings (scanner-only env vars)
└── logger_setup.py             Loguru config
```

## What's NOT here yet

- ❌ `main.py` / orchestration — how scanners are wired into a running bot
- ❌ Integration with `zetryn-trading` agents (scanner output → decision graph)
- ❌ Execution layer — swap (Jupiter), position manager, reconciliation
- ❌ Wallet — encryption, key management, monitor, sweeper
- ❌ Persistence — Postgres for DecisionLog, position state
- ❌ API server + dashboard
- ❌ Tests (the cdexio test suite was decision-pipeline-specific; will write fresh tests for the zetryn-trading integration)
- ❌ Docker / deployment

These are intentional gaps for Phase 1. The roadmap discussion (Phase 2+)
will sequence them.

## Install

```bash
# Requires Python 3.11+
pip install -e ".[dev]"

# Or with requirements.txt
pip install -r requirements.txt
```

## Configure

```bash
cp .env.example .env
# Edit .env — minimal: REDIS_URL. Scanners with missing keys are skipped at runtime.
```

You'll also need a running Redis instance (`redis-server`). PostgreSQL is
**not** required at Phase 1.

## Use individual scanners

There's no `main.py` yet — scanners are independent coroutines you wire
into your own runtime. Example pattern (you'll write this yourself for now):

```python
import asyncio
import aiohttp

from zetryn_bot.config import Settings
from zetryn_bot.storage import connect, publish_sniper
from zetryn_bot.scanners.dexscreener import poll_dexscreener_new_pairs
from zetryn_bot.utils import supervise

async def main():
    settings = Settings()                        # loads .env
    redis = await connect(settings.redis_url)
    async with aiohttp.ClientSession() as session:
        await supervise(
            "scanner.dexscreener.new",
            poll_dexscreener_new_pairs, session, redis,
        )

asyncio.run(main())
```

Each scanner module documents its own contract (what env vars it expects,
which channel it publishes to). See the source files.

## Provenance

Scanner source files originate from a working production memecoin bot,
were copied into this repo on 2026-06-28, had their absolute imports
rewritten to the `zetryn_bot.*` namespace, and had all decision-tier
logic (scorer, filter, risk agent, executor, wallet, notifier, etc.)
stripped out. The intent is for those concerns to be added back in their
own phases, with `zetryn-trading` owning the decision tier.

## License

MIT. See [LICENSE](LICENSE).

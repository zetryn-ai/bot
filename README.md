# zetryn-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

**Solana memecoin trading bot** — the I/O and execution side of the Zetryn
stack. It scans public sources for new tokens, enriches them with
safety/wallet/social signals, hands each candidate to a
[`zetryn-trading`](https://github.com/zetryn-ai/ai-agent) decision agent, and
can act on the result — from a logged decision, to a paper trade, to a real
signed swap.

> [!WARNING]
> **v0.5.1 — pre-alpha, M1–M5 shipped.** The runtime, paper-trading engine,
> and live-execution wallet all work end to end (see [Status](#status)
> below). There is **no persistence or observability yet** — no database, no
> notifier, no dashboard. A crash or restart loses in-memory position state.
> Treat this as an active research/dev build, not a production deployment.

## How the pieces fit

```
zetryn-trading  (framework, PyPI: zetryn-trading)   — DECIDES
    graph orchestration · LLM analyst · scoring · never touches the chain

zetryn_bot      (this repo)                          — EXECUTES
    scan → enrich → adapt → [agent decides] → risk-size → paper or live swap
```

The framework never sees your private key or touches the chain — this repo
owns all I/O: fetching, enrichment, wallet, signing, and submission.

## Status

| Milestone | What it shipped | State |
|---|---|---|
| **M1** | `Scanner` / `TokenEnricher` protocols, 9 scanner + 5 enricher classes | ✅ shipped |
| **M2** | `BotPipeline` — wires a candidate through enrichment → adapter → a compiled `zetryn-trading` agent → a sink | ✅ shipped |
| **M3** | `python -m zetryn_bot` — concurrent runtime, crash-safe supervision, graceful shutdown | ✅ shipped |
| **M4** | Paper-trading engine — risk-sized alerts become simulated positions at real Jupiter prices, auto-exit on TP/SL/time | ✅ shipped |
| **M5** | Encrypted wallet + `LiveExecutor` — real signed swaps, gated behind layered safety guards | ✅ shipped |
| M6 | Persistence (PostgreSQL: decision log, position state) | 📅 planned |
| M7 | Observability (Telegram/Discord notifier, heartbeat) | 📅 planned |
| M8 | Deployment (Docker, systemd) | 📅 planned |
| M9 | API + dashboard | 📅 planned |
| M10 | Specialized per-signal agent routing (sniper, graduation, KOL copy-trade) | 📅 planned |

Full detail, dependencies, and design docs: [`ROADMAP.md`](ROADMAP.md) and
[`docs/plans/`](docs/plans/).

## Quickstart

```bash
# 1. Install (Python 3.11+)
pip install -e ".[dev]"

# 2. Configure — every var is optional; the bot runs with zero config
cp .env.example .env

# 3. Run — the zero-arg scanners (Dexscreener, GeckoTerminal, Raydium) work
#    immediately with no API keys; decisions are logged, nothing is bought.
python -m zetryn_bot          # or the installed console script: zetryn-bot
```

Ctrl-C (SIGINT/SIGTERM) drains in-flight work and shuts down cleanly. Add API
keys to `.env` to unlock the rest of the sources (Birdeye, Pump.fun,
Telegram, Twitter — see `.env.example` for each one's signup link), and a
Groq/OpenRouter/Gemini key to unlock the LLM analyst instead of the
rule-only fallback.

## Escalating what the bot does

Three independent switches, each opt-in, each documented in `.env.example`:

| Mode | Switch | What happens | Funds at risk |
|---|---|---|---|
| **Scan only** (default) | *(nothing set)* | Every decision is logged; nothing is bought | none |
| **Paper trade** | `EXECUTION_ENABLED=true` | Alerts become simulated positions at real Jupiter prices; auto-exit on TP/SL/time | none — no transaction, no wallet |
| **Live trade** | + `EXECUTION_MODE=live` | Alerts become real, signed, submitted swaps | **real funds** |

### Paper trading (M4)

```bash
EXECUTION_ENABLED=true python -m zetryn_bot
```

Alerts at or above `RISK_MIN_CONFIDENCE` are sized (`RISK_BASE_SIZE_SOL ×
confidence`) into simulated positions priced from real Jupiter quotes.
Positions auto-close on `EXIT_TP_PCT` / `EXIT_SL_PCT` / `EXIT_MAX_HOLD_S`.
Set `RISK_BUY_ACTIONS=alert,watch` to also paper-trade the analyst's
watchlist (useful for gathering outcome data — the analyst rarely emits a
bare `alert` on brand-new memecoins). See `.env.example` for the full
`RISK_*` / `EXIT_*` / `GATE_*` reference.

### Going live (M5)

**Real funds. Read this before setting `EXECUTION_MODE=live`.**

```bash
python scripts/wallet_init.py
# → paste your base58 private key, choose a passphrase, get a public key back.
# Neither secret ever touches disk unencrypted, a log line, or shell history.
```

Then: fund the printed address, set `WALLET_PASSPHRASE` in `.env` (kept
somewhere separate from the resulting `wallet.enc`), and point `SOLANA_RPC_URL`
at a real RPC endpoint (a bare API key is not enough — e.g.
`https://mainnet.helius-rpc.com/?api-key=<key>`). Then:

```bash
EXECUTION_ENABLED=true EXECUTION_MODE=live python -m zetryn_bot
```

Live execution activates **only** when every guard passes:
`EXECUTION_ENABLED=true` **and** `EXECUTION_MODE=live` **and** the wallet
keyfile decrypts successfully. Any single failure logs an error and falls
back to paper — never a crash, never a silent live activation. When live is
active, a `WARNING`-level banner announces the wallet's public key and
`WALLET_MAX_TRADE_SOL` (an absolute per-trade cap, independent of
`RISK_BASE_SIZE_SOL`, as a last line of defense against misconfiguration).

`LiveExecutor` never blind-retries a swap: on a confirmation timeout it
checks the transaction's actual on-chain status before deciding anything,
so a slow-but-successful swap is never resubmitted. Signing itself is local
and sub-millisecond (`solders`); end-to-end swap time is dominated by the
Jupiter API and Solana's confirmation time (hundreds of ms to a few
seconds) — that part is outside the bot's control.

## Configuration reference

Every setting has a safe default and is documented inline in
[`.env.example`](.env.example) — copy it and read the comments rather than
guessing. Groups, in the order they appear:

| Group | Covers |
|---|---|
| LLM analyst | `GROQ_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY` — first one set wins; none set = rule-only gates |
| Solana RPC | `SOLANA_RPC_URL` (+ fallback) — used by Helius/Pump.fun/Raydium scanners and, if you go live, transaction submission |
| Scanner keys | Helius, Birdeye, GMGN, PumpPortal — each scanner/enricher is skipped (not a hard error) when its key is absent |
| Telegram / Twitter | Channel list, cookie path — see [scripts/telegram_login.py](scripts/telegram_login.py) for one-time login |
| Runtime | `SCANNERS_ENABLED`, `WORKERS`, `QUEUE_SIZE`, `DEDUP_TTL_S` |
| Decision gates | `GATE_MIN_LIQUIDITY_USD` and friends — the hard filters a candidate must clear before it ever reaches the LLM |
| Execution (M4) | `EXECUTION_ENABLED`, `RISK_*`, `EXIT_*` — paper-trading sizing and exit rules |
| Wallet + live (M5) | `EXECUTION_MODE`, `WALLET_*`, `LIVE_*` — see [Going live](#going-live-m5) above |

## Testing this repo

```bash
python -m pytest                 # full unit test suite (offline, no network)
python scripts/m1_smoke.py       # ... through m5_smoke.py — one per milestone,
                                  # each exercises its milestone's real external
                                  # API contract against a mock, offline
ruff check zetryn_bot/           # lint
ruff format --check zetryn_bot/  # format check
```

No test or smoke script ever sends a real transaction or spends real funds —
the live-execution path (M5) is only ever exercised against a mocked RPC and
Jupiter client.

## Architecture at a glance

```
zetryn_bot/
├── __main__.py                    entry point — python -m zetryn_bot
├── config.py                      Settings (pydantic) — every env var, one place
├── runtime/                       concurrent scanner orchestration (M3)
│   ├── orchestrator.py            queue + worker pool + supervised tasks
│   ├── registry.py                config → scanner/enricher instances
│   └── llm.py                     optional LLM client wiring
├── pipeline/                      per-candidate flow (M2)
│   ├── enrich.py                  runs the TokenEnricher chain
│   ├── runner.py                  BotPipeline — agent-agnostic runner
│   └── sinks.py                   LogSink / TeeSink / ExecutionSink
├── adapters/token_input.py        TokenCandidate -> zetryn-trading's TokenInput
├── execution/                     paper + live trading engine (M4/M5)
│   ├── risk.py                    sizing, confidence gate, circuit breaker
│   ├── position.py                open positions + TP/SL/time exit loop
│   ├── executor.py                Executor protocol + PaperExecutor
│   ├── live.py                    LiveExecutor — real signed swaps
│   ├── rpc.py                     Solana submit/confirm/on-chain verification
│   └── jupiter.py                 quote + swap-transaction building
├── wallet/keystore.py             encrypted keypair, never logged (M5)
├── scanners/                      9 discovery sources + 5 enrichers (M1)
├── models/token.py                TokenCandidate — the shared schema
└── storage/redis_client.py        pub/sub transport (not on the M3+ hot path)
```

Every scanner and enricher documents its own contract — env vars, rate
limits, what it populates — in its module docstring.

## Extending the bot

**Add a scanner or enricher** — implement `Scanner` or `TokenEnricher`
(both `runtime_checkable` Protocols, no inheritance needed):

```python
# zetryn_bot/scanners/protocol.py
@runtime_checkable
class Scanner(Protocol):
    name: str
    def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]: ...

@runtime_checkable
class TokenEnricher(Protocol):
    name: str
    async def enrich(self, mint: str, candidate: TokenCandidate, session) -> TokenCandidate: ...
```

Wire it into `runtime/registry.py`'s `build_enabled_scanners()` /
`build_enrichers()` so `.env` can turn it on.

**Drive `BotPipeline` yourself** (e.g. outside the M3 runtime, or from a
notebook):

```python
import asyncio
import aiohttp
from strategies.agents.scanner import build_scanner

from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import LogSink
from zetryn_bot.scanners.dexscreener import DexscreenerNewPairs
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher

async def main() -> None:
    agent = build_scanner(llm_client=None)  # or a real zetryn.llm.LLMClient
    pipeline = BotPipeline(agent, enrichers=[HeliusEnricher(api_keys=[...])], sink=LogSink())
    async with aiohttp.ClientSession() as session:
        await pipeline.run_scanner(DexscreenerNewPairs(), session)

asyncio.run(main())
```

`pipeline.process(candidate, session)` is the single-candidate primitive if
you're driving the loop yourself.

## Style

Strict ruff (`E`, `F`, `I`, `B`, `UP`, `SIM`, `RUF`), line length 100,
double-quote format — see `pyproject.toml` `[tool.ruff]`. Pre-commit hooks
enforce this on every commit; CI runs the same checks plus the full test
suite and every milestone's smoke script.

## Provenance

Scanner source files originate from a working production memecoin bot;
were imported on 2026-06-28, rewritten to the `zetryn_bot.*` namespace, had
all decision-tier logic (scorer, filter, risk agent, executor, wallet,
notifier) stripped out, and were refactored to the `Scanner` /
`TokenEnricher` Protocols in M1. Those decision-tier concerns now live in
[`zetryn-trading`](https://github.com/zetryn-ai/ai-agent); their bot-side
counterparts (execution, wallet) came back in M4/M5, with persistence and
observability following in M6/M7.

## License

MIT. See [LICENSE](LICENSE).

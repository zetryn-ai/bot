# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Documentation conventions (MUST FOLLOW)

Three places, no fourth:

| Change type | Where to document |
|---|---|
| Roadmap milestone (M2, M3, …) — substantive design with locked decisions | **One** markdown file in `docs/plans/YYYY-MM-DD-mN-<slug>.md`. Headers: `**Date:**` + `**Status:**` (`Draft` / `Approved` / `Shipped (vX.Y.Z)` / `Historical`). Add a row to `docs/plans/README.md`. |
| Non-milestone change (refactor, bug fix, dep bump, doc tweak) | git history. No design doc needed. |
| User-facing release notes (semver tagged) | `CHANGELOG.md` at root. |
| Roadmap status (which milestone is current, dependencies) | `ROADMAP.md` at root — single source of truth. README links to it. |

**Hard rule — DO NOT create ad-hoc `*.md` files** outside these three
locations. No `NOTES.md`, no `TODO.md`, no per-feature READMEs in source
trees. If unsure where something belongs, default to a commit message
that explains it; if the explanation grows past a paragraph, it's
probably a design doc waiting to happen — go to `docs/plans/`.

## Framework Boundary (NON-NEGOTIABLE — read first, every session)

**This repo is the I/O side.** The decision side lives in
[`zetryn-trading`](https://github.com/zetryn-ai/ai-agent) (PyPI:
`zetryn-trading`). The boundary mirrors that framework's boundary, and
crossing it from either direction is the #1 source of scope leak.

| `zetryn_bot` (this repo) OWNS | `zetryn-trading` (framework) OWNS |
|---|---|
| Fetching data (HTTP, WebSocket, RPC, social feeds) | Graph orchestration, node execution, `StepTrace` |
| Wallet management, key encryption, signing (M5) | LLM analyst calls, structured output, fallback |
| Transaction execution, slippage, MEV (M4) | Scoring, decision aggregation |
| Position tracking, PnL bookkeeping (M4–M6) | Memory primitives (`Blacklist`, `DecisionLog`, `ReflectiveNode`) |
| Persistence — PostgreSQL (M6) | Knowledge primitives (`KnowledgePack`) |
| Notifier (Telegram/Discord), heartbeat, crash dump (M7) | Tool primitives (`Tool`, `ToolRegistry`, tool-use loop) |
| Dashboard backend + frontend (M9) | Backtest harness — `Backtester` |
| Pre-filter at the bot before calling the framework | Decision modes (`rule` / `llm` / `hybrid` / `hybrid_audit`) |

**Practical implications when designing or coding here:**

- New scanner / enricher? **Probably yes** — implement `Scanner` or
  `TokenEnricher` from `zetryn_bot.scanners.protocol`.
- New "L3 scorer" / "filter pipeline" / "AI score 0-100" inside
  `zetryn_bot/`? **No.** That's decision-tier; it belongs to
  `zetryn-trading` agents. M2 wires scanner output → `zetryn-trading`
  agents that own scoring.
- New wallet operation (sign, sweep, encrypt)? **Yes** — but not until
  M4/M5. Until then, ignore wallet concerns.
- New "decide whether to buy" rule node? **No.** That's a
  `zetryn-trading` graph node, not a bot-side concern.

If decision-tier code keeps creeping into `zetryn_bot/`, that's the
signal something is wrong. Flag it; do not normalise it.

## Commit identity (ROLLING RANDOM — no need to ask)

Five GitHub identities are available, all wired via SSH host aliases in
`~/.ssh/config`. Mirror of the convention in
`zetryn-ai/ai-agent/scripts/commit-as.sh`.

| Identity | Email | SSH host |
|---|---|---|
| `aldirrss` | aldialputra@gmail.com | `github-aldi` |
| `cry` | cryptowave3142@gmail.com | `github-cry` |
| `zetryn` | zetrynai@gmail.com | `github-zetryn` |
| `cdexio` | cdexioagent@gmail.com | `github_cdexio` |
| `lema` | lemacoreofficial@gmail.com | `github_lema` |

**Rule:**

- **Patch / dev commits** — `./scripts/commit-as.sh random "message"
  main` (or omit branch if main is default). Random rolls one of the
  five identities each time. Same identity may roll twice in a row;
  that's expected.
- **Minor and major version releases** (`v0.X.0`, `v1.X.0`,
  `vX.0.0`) — use the `zetryn` identity inline:
  ```bash
  GIT_AUTHOR_NAME=zetryn GIT_AUTHOR_EMAIL=zetrynai@gmail.com \
  GIT_COMMITTER_NAME=zetryn GIT_COMMITTER_EMAIL=zetrynai@gmail.com \
    git -c user.name=zetryn -c user.email=zetrynai@gmail.com commit -m "..."
  ```
  And annotate the tag the same way (annotated tags carry a tagger
  identity, which must also be `zetryn`).

**Do NOT ask the user which identity to use** for random commits. The
script handles it.

## Commands

```bash
# 1. Set up a venv (no shared conda env — bot has its own deps)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Pre-commit hook (recommended — enforces ruff on every commit)
pip install pre-commit
pre-commit install

# 3. Lint + format
ruff check zetryn_bot/
ruff format --check zetryn_bot/
ruff format zetryn_bot/                # apply formatting

# 4. Smoke test — offline (Protocol conformance + version check)
python scripts/m1_smoke.py

# 5. Smoke test — live (hits real DexScreener for ~5s)
M1_SMOKE_LIVE=1 python scripts/m1_smoke.py

# 6. Commit + push (random identity)
./scripts/commit-as.sh random "feat: your message" main
```

There is no `pytest` yet. Tests land in M2 (around the integration
boundary with `zetryn-trading`, not the scanners in isolation).

## Architecture

```
zetryn-ai/bot (this repo) — I/O layer
│
├── zetryn_bot/                         ← the library
│   ├── __init__.py                     ← __version__
│   ├── config.py                       ← Pydantic Settings (scanner env vars)
│   ├── logger_setup.py                 ← Loguru setup
│   │
│   ├── scanners/                       ← the M1 deliverable
│   │   ├── protocol.py                 Scanner + TokenEnricher Protocols
│   │   ├── _common.py                  poll_loop() + fetch_json()
│   │   ├── birdeye.py                  2 Scanner classes
│   │   ├── dexscreener.py              3 Scanner classes
│   │   ├── geckoterminal.py            2 Scanner classes
│   │   ├── pumpfun.py                  1 Scanner class (WebSocket)
│   │   ├── raydium.py                  1 Scanner class
│   │   ├── telegram.py                 1 Scanner class (telethon)
│   │   └── enrichers/                  5 TokenEnricher classes
│   │       ├── helius.py
│   │       ├── rugcheck.py
│   │       ├── jupiter.py
│   │       ├── gmgn_openapi.py
│   │       └── twitter.py
│   │
│   ├── models/token.py                 TokenCandidate (Pydantic schema)
│   ├── storage/redis_client.py         Redis pub/sub transport + 3 channels
│   └── utils/
│       ├── key_pool.py                 APIKeyPool + Birdeye + Helius
│       └── supervisor.py               supervise() — crash-safe async loop
│
├── docs/plans/                         ← milestone design docs (1 per milestone)
├── scripts/                            ← dev tools (commit-as.sh, m1_smoke.py)
└── .github/workflows/ruff.yml          ← lint + smoke CI
```

### Scanner Protocol (continuous candidate stream)

```python
# zetryn_bot/scanners/protocol.py

@runtime_checkable
class Scanner(Protocol):
    name: str
    def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]: ...
```

Implementations live in the top-level `scanners/` modules. Contract:

- Yield `TokenCandidate` values indefinitely; the caller decides the sink.
- Never publish to Redis / write to disk / call any sink directly. Pure stream.
- Handle transient errors gracefully (log + continue, don't propagate).
- Sleep between polls via `asyncio.sleep`; never block.
- Release HTTP / WebSocket resources cleanly when the caller breaks the
  loop. Use `async with` and `finally`.

### TokenEnricher Protocol (on-demand mint lookup)

```python
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

Implementations live in `scanners/enrichers/`. Contract:

- Treat the input `candidate` as **immutable**. Return a copy via
  `candidate.model_copy(update={...})`; **never** mutate the argument.
- Only populate the fields this enricher owns. Other fields pass through
  unchanged.
- Transient errors: log and return the unchanged `candidate`. Persistent
  errors (auth, config): may raise.

### Module docstring template

Every scanner / enricher module starts with this docstring shape:

```python
"""<Name> — <one-line what it scans / enriches>.

Source: <URL or docs link>
Auth: <env var name(s) or "none">
Mechanism: <"REST polling every Ns" | "WebSocket stream" | "on-demand lookup">
Rate limits: <known limits>
Emits: <for Scanner — what stream() yields, downstream channel hint>
Populates: <for TokenEnricher — what fields on TokenCandidate are filled>
"""
```

### Settings (`zetryn_bot.config.Settings`)

Pydantic `BaseSettings`, loaded from `.env`. Intentionally narrow: only
the env vars scanners + transport + logging actually read. Wallet,
execution, decision thresholds, notifier, and persistence configs will
come back per milestone, in their own modules — not as one monolithic
Settings.

### Redis transport (`zetryn_bot.storage.redis_client`)

Three pub/sub channels:

- `scanner.sniper` — discovery / new-pair events
- `scanner.migration` — Pump.fun → Raydium graduations (dedicated fast path)
- `scanner.momentum` — trending / volume / boost events

`connect(url)` + `publish_sniper(redis, data)` + `publish_migration` +
`publish_momentum`. cdexio's decision-stream consumer-group bootstrap
(`stream.commands`, `stream.decisions`) was intentionally removed at
import time — that belongs to the decision tier (now `zetryn-trading`).

### Key rotation (`zetryn_bot.utils.key_pool`)

`APIKeyPool` (RPM + RPD aware, 429 cooldown), `BirdeyeKeyPool`,
`HeliusKeyPool`. LLM-provider pools (Gemini / Groq / OpenRouter) are
intentionally **not** here — those overlap with
`zetryn_trading.llm.LLMRouter`. Don't duplicate.

## Key patterns

**Adding a new scanner:**

1. Pick a category by what it produces. Continuous stream → `Scanner`.
   On-demand lookup `(mint) → enriched candidate` → `TokenEnricher`.
2. New file: `zetryn_bot/scanners/<name>.py` (Scanner) or
   `zetryn_bot/scanners/enrichers/<name>.py` (TokenEnricher).
3. Module docstring per template above.
4. Class implementing the Protocol. `name` class attribute is the
   stable identifier; use `<source>.<mode>` (e.g. `"dexscreener.boost"`).
5. Polling helpers in `zetryn_bot.scanners._common`: `poll_loop()` for
   the standard "fetch every N seconds with error survival" loop;
   `fetch_json()` for consistent HTTP GET + status handling.
6. Update the relevant `__init__.py` only if you want the class
   re-exported at package level — most consumers import the class
   directly from its module.
7. Run `python scripts/m1_smoke.py` and add the new class to the
   smoke test's scanner / enricher lists.
8. `./scripts/commit-as.sh random "feat: add <name> scanner" main`.

**Adding a new milestone:**

1. New design doc: `docs/plans/YYYY-MM-DD-mN-<slug>.md`.
   `**Status:** Draft`. Lock 4-6 decisions via brainstorming; document
   them as a table.
2. Add a row to `docs/plans/README.md`.
3. Update `ROADMAP.md` — change the milestone's Status from `📅 planned`
   to `🚧 in-progress` + link to the design doc.
4. Execute the milestone in sub-phases (B1, B2, ...). One commit per
   sub-phase. Use `commit-as.sh random` for sub-phase commits.
5. Final sub-phase: version bump, CHANGELOG entry, README update,
   design doc Status → Shipped. Commit + push with the `zetryn`
   identity (minor or major release).
6. Tag the release with the `zetryn` identity. Push tag. Create a
   GitHub release with handcrafted notes.

**Versioning convention** (matches `zetryn-trading`):

| Change type | Bump |
|---|---|
| Bug fix, no API change | `v1.0.X` (patch) |
| Additive feature, no breaking change | `v1.X.0` (minor) |
| Breaking change to public API | `vX.0.0` (major) |

Pre-`v1.0.0`, breaking changes are allowed without a major bump —
minor bumps signal substantial milestone landings.

## Provenance

Scanner source files originate from a working production memecoin bot
(cdexio); were imported on 2026-06-28 (commit `738c239`); had their
absolute imports rewritten to the `zetryn_bot.*` namespace; had all
decision-tier logic stripped out (scorer, filter, risk agent, executor,
wallet, notifier, persistence); and were refactored to the `Scanner` /
`TokenEnricher` Protocols in M1 (`v0.1.0`). The decision tier those
modules used to feed now lives in `zetryn-trading`; bot-side counterparts
(execution, wallet, persistence, etc.) come back per milestone — see
`ROADMAP.md`.

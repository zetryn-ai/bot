# M2 — Wire Scanners to `zetryn-trading`

**Date:** 2026-06-28
**Status:** Shipped (v0.2.0)
**Target version:** v0.2.0

## 0. Deviations from the plan below (reconciled at landing, 2026-07-01)

The sections below are the original brainstorming sketch. Three things
changed once B1–B5 actually ran against the real `zetryn-trading` v1.1.0
package:

1. **Dependency is PyPI, not git+ssh.** §8 planned a commit-SHA pin
   because `zetryn-trading` wasn't on PyPI yet per `project_release_history.md`
   at doc-write time. It shipped to PyPI as v1.0.0 the same day (2026-06-28)
   and is now v1.1.0 — `pyproject.toml` uses `"zetryn-trading>=1.1.0"` directly.
2. **Sinks live in `zetryn_bot/pipeline/sinks.py`, not a separate
   `zetryn_bot/sinks/` package.** §3.1 and §6 sketched a 4-file package
   (`__init__.py` + `base.py` + `log_sink.py` + `list_sink.py`) for two
   ~10-line classes and a Protocol. Collapsed to one file — no behavior
   difference, just one less package for three tiny things.
3. **Field names in §4 corrected against the real schema.** The sketch
   predated inspecting `trading.schemas` directly (`price_usd`, `market_cap_usd`,
   `volume_5m_usd`-on-`MarketData`, `holder_count`, `mayhem_mode`, etc. don't
   exist on the real classes — they're `price`, `mcap`, `MarketData` has no
   5m volume, `HolderData.count`, `PumpfunData.is_mayhem_mode`). The shipped
   `zetryn_bot/adapters/token_input.py` is the source of truth; §4 below is
   left as-is for historical record of the original intent, not as an
   accurate reference.
4. **`TwitterData` has no `influencer_count` field.** The shipped adapter
   maps the bot's top-influencer fields onto the framework's single
   `handle`/`followers` pair and drops `twitter_influencer_count` (documented
   inline in the adapter — lossy, no target field exists).
5. **Adapter failures are caught inside `BotPipeline.process()`, not a
   free `synthetic_abort()` helper.** Same synthetic-abort behavior as §3.4
   describes, just inlined at the one call site instead of a separate function.

Wire the M1 scanner subsystem to the `zetryn-trading` framework. The
bot becomes a thin runtime that pulls candidates from `Scanner` streams,
enriches them via `TokenEnricher` impls, adapts to the framework's
`TokenInput` schema, invokes a compiled agent graph, and emits the
resulting `Decision` to a swappable sink. No execution, no wallet, no
Redis on the hot path — those land in later milestones.

## 1. Goals

1. End-to-end wiring from scanner stream → framework decision → sink, in
   a single Python process, library-style (no service boundary).
2. Adapter is **pure** — `to_token_input(candidate, source) → TokenInput`
   has no I/O and is unit-testable without the framework or network.
3. Enrichment is **separate** from adapter — the bot's `pipeline/enrich`
   composes the five M1 enrichers; the adapter never calls them.
4. Pipeline runner is **agent-agnostic** — it accepts any compiled
   `Graph` (default `build_scanner(llm_client=None)`), so swapping in
   `build_sniper` / `build_graduation` later is a constructor change.
5. Sink is **swappable** via a Protocol — `LogSink` is the production
   default in M2; `ListSink` covers tests. A Redis sink lands in M3 when
   orchestration needs fan-out.
6. Bot grows its first test suite — unit coverage for the new layers
   plus one integration test against a real `build_scanner` (rule-only
   path, no API key required).

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Adapter location | (C) `zetryn_bot/adapters/token_input.py` — pure mapping function; enrichment lives separately in `zetryn_bot/pipeline/enrich.py`. Framework never imports bot types. |
| 2 | Execution model | (C) In-process library import as default; `DecisionSink` Protocol seeded so M3 can swap to Redis without touching scanner / pipeline code. |
| 3 | Agent selection | (A) `build_scanner` as default. `BotPipeline(agent=...)` accepts any compiled `Graph`, so callers can pass `build_sniper(...)` etc. without modifying the bot. |
| 4 | Testing strategy | (B) Unit (mock framework) for adapter / pipeline / sinks + one integration test invoking real `build_scanner(llm_client=None)` to verify schema contract. Live smoke is a separate opt-in script (`scripts/m2_smoke.py`), not part of pytest. |
| 5 | Error handling | (C) Pragmatic mix — abort decisions emit to sink (audit); adapter errors emit a synthetic abort decision; agent exceptions log at `error` level and continue (long-running pipeline must survive transient LLM hiccups). |
| 6 | Decision sink | (C) `LogSink` (production default) + `ListSink` (test fixture). Redis sink deferred to M3. |

Additional declared constraints:

- `TokenCandidate` schema **stays as-is**. The adapter does the bridging;
  M2 does not redesign it.
- `zetryn-trading` is added as a git+ssh dep (the framework is not yet
  on PyPI per `project_release_history.md`). Pinned to a commit SHA in
  `pyproject.toml` to catch schema drift deterministically.
- No specialized agent contexts (`KOLContext`, `GraduationContext`,
  `PositionContext`) wired in M2 — only `TradingContext` for the
  scanner agent. Specialized wirings follow once orchestration runtime
  exists in M3.
- Zero changes to `zetryn_bot/scanners/*` and `zetryn_bot/models/token.py`
  — M2 only adds **adapter, pipeline, sinks**.

## 3. Architecture

```
Scanner (M1)                Adapter layer (M2)            Framework (zetryn-trading)
─────────                   ──────────────                ──────────────────────────
DexscreenerNewPairs ──┐
BirdeyeTrending ──────┤   ┌─────────────────┐           ┌──────────────────────┐
PumpfunStream ────────┼──▶│ pipeline/enrich │──Cand────▶│ adapters/to_token_  │
RaydiumNewPools ──────┤   │  (call enrichers)│           │ input.py (pure map) │
  ... 9 scanners ─────┘   └─────────────────┘           └──────────┬───────────┘
                                                                   │ TokenInput
                                                                   ▼
                          ┌─────────────────┐           ┌──────────────────────┐
                          │  DecisionSink   │◀─Decision─│ build_scanner(g)    │
                          │ (Log | List)    │           │  + TradingContext   │
                          └─────────────────┘           └──────────────────────┘
```

### 3.1 New modules

| Path | Responsibility | LOC est. |
|---|---|---|
| `zetryn_bot/adapters/__init__.py` + `token_input.py` | Pure `to_token_input(candidate, source) → TokenInput` field mapping | ~80 |
| `zetryn_bot/pipeline/__init__.py` + `enrich.py` | `enrich_candidate(candidate, enrichers, session)` — sequential composition of `TokenEnricher`s | ~60 |
| `zetryn_bot/pipeline/runner.py` | `BotPipeline(scanner, enrichers, agent, sink, config)` — loop, error handling | ~120 |
| `zetryn_bot/sinks/__init__.py` + `base.py` + `log_sink.py` + `list_sink.py` | `DecisionSink` Protocol + two implementations | ~70 |

### 3.2 `DecisionSink` Protocol

```python
# zetryn_bot/sinks/base.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trading.schemas import Decision


@runtime_checkable
class DecisionSink(Protocol):
    """A sink for :class:`Decision` values produced by the pipeline.

    Implementations must:
    - Be cheap to call per decision (no synchronous network in the hot
      path unless the implementation is explicitly buffering / async).
    - Not raise — sinks log internally and swallow transient errors;
      the pipeline must keep running.
    - Be replaceable: ``LogSink`` for production-by-default, ``ListSink``
      for tests, ``RedisDecisionSink`` planned for M3.
    """

    name: str
    async def emit(self, decision: Decision) -> None: ...
```

### 3.3 Pipeline loop

```python
# zetryn_bot/pipeline/runner.py (sketch — final implementation in B4)

class BotPipeline:
    def __init__(
        self,
        scanner: Scanner,
        enrichers: list[TokenEnricher],
        agent: Graph,
        sink: DecisionSink,
        config: ScannerConfig,
    ) -> None: ...

    async def run(self, session: aiohttp.ClientSession) -> None:
        async for candidate in self.scanner.stream(session):
            try:
                enriched = await enrich_candidate(candidate, self.enrichers, session)
                token_input = to_token_input(enriched, source=self.scanner.name)
            except Exception as exc:                # adapter / validation failure
                await self.sink.emit(synthetic_abort(candidate, reason=str(exc)))
                continue
            try:
                ctx = TradingContext(token=token_input, config=self.config)
                decision = await self.agent.ainvoke(ctx)
            except Exception as exc:                # framework / LLM crash
                logger.bind(component=self.scanner.name).error(
                    f"agent crash: {exc!r}"
                )
                continue
            await self.sink.emit(decision)          # emit including aborts
```

### 3.4 Error handling matrix

| Scenario | Action | Log level | Sink emit? |
|---|---|---|---|
| Framework returns `Decision(action="abort")` | continue loop | `info` (via sink) | yes — audit |
| Adapter / `ValidationError` while building `TokenInput` | continue, emit synthetic abort | `warning` | yes — synthetic abort with `flags={"synthetic": True, "source": "bot_adapter"}` |
| Enricher transient failure | swallowed inside `enrich_candidate`; fields pass through unchanged | `warning` | n/a — pipeline continues to agent |
| Agent raises unexpected `Exception` | continue loop | `error` (so M7 notifier can page) | no — exceptions are bugs, not decisions |

Synthetic abort helper (`pipeline/runner.py`):

```python
def synthetic_abort(c: TokenCandidate, *, reason: str) -> Decision:
    return Decision(
        token_mint=c.address,
        action="abort",
        reason=f"adapter_error: {reason}",
        scores=NarrativeScore(safety=0, market=0, social=0, narrative=0, final=0),
        analysis=None,
        flags={"synthetic": True, "source": "bot_adapter"},
    )
```

## 4. Adapter contract

```python
# zetryn_bot/adapters/token_input.py — final shape locked in B3

def to_token_input(c: TokenCandidate, *, source: str) -> TokenInput:
    return TokenInput(
        mint=c.address,
        symbol=c.symbol,
        name=c.name,
        source=_map_source(source),
        market=MarketData(
            price_usd=c.price_usd,
            liquidity_usd=c.liquidity_usd,
            market_cap_usd=c.market_cap_usd,
            volume_5m_usd=c.volume_5m_usd,
            volume_1h_usd=c.volume_1h_usd,
            buys_5m=c.buys_5m,
            sells_5m=c.sells_5m,
            age_seconds=c.age_seconds,
        ),
        activity=ActivityData(txns_1m=c.txns_1m, txns_5m=c.txns_5m),
        holders=HolderData(
            holder_count=c.holder_count,
            top10_pct=c.top10_holder_pct,
            dev_pct=c.dev_wallet_pct,
        ),
        contract=ContractData(
            is_honeypot=c.is_honeypot,
            is_mintable=c.is_mintable,
            is_freezable=c.is_freezable,
            bundled_supply=c.bundled_supply,
            dev_rug_history=c.dev_rug_history,
        ),
        wallets=WalletIntel(
            smart_wallets=c.gmgn_smart_wallets,
            kol_wallets=c.gmgn_kol_wallets,
            sniper_wallets=c.gmgn_sniper_wallets,
            bundler_wallets=c.gmgn_bundler_wallets,
            whale_wallets=c.gmgn_whale_wallets,
        ),
        pumpfun=_pumpfun_or_none(c),
        social=SocialData(twitter=_twitter_data(c)),
    )
```

### 4.1 Source mapping

`_map_source(scanner_name)` collapses scanner identifiers to the
framework's `TokenSource` literal:

| Scanner `name` | `TokenSource` |
|---|---|
| `"dexscreener.new_pairs"`, `"dexscreener.trending"`, `"dexscreener.boost"` | `"dexscreener"` |
| `"birdeye.trending"`, `"birdeye.new_listing"` | `"birdeye"` |
| `"raydium.new_pools"` | `"raydium"` |
| `"pumpfun.stream"` | `"pumpfun_ws"` |
| `"geckoterminal.*"`, `"telegram.*"`, anything else | `"manual"` (framework's catch-all) |

If/when the framework widens `TokenSource` to include `"geckoterminal"`
/ `"telegram"`, this mapping table updates in a single function.

### 4.2 `pumpfun` field

Only populated when `_map_source(...) == "pumpfun_ws"`:

```python
def _pumpfun_or_none(c: TokenCandidate) -> PumpfunData | None:
    if c.bonding_curve_sol == 0.0 and c.creator_sol_buy == 0.0:
        return None
    return PumpfunData(
        creator_sol_buy=c.creator_sol_buy,
        bonding_curve_sol=c.bonding_curve_sol,
        bonding_curve_pct=c.bonding_curve_pct,
        mayhem_mode=c.is_mayhem_mode,
        creator_wallet=c.creator_wallet,
    )
```

### 4.3 Twitter sub-mapping

`_twitter_data(c)` packs the M1 enricher's Twitter fields into the
framework's `TwitterData`:

```python
def _twitter_data(c: TokenCandidate) -> TwitterData:
    return TwitterData(
        mentions_1h=c.twitter_mentions_1h,
        mention_growth_pct=c.twitter_mention_growth_pct,
        influencer_count=c.twitter_influencer_count,
        top_influencer_handle=c.twitter_top_influencer_handle,
        top_influencer_followers=c.twitter_top_influencer_followers,
        sentiment=c.twitter_sentiment,
        engagement=c.twitter_engagement,
        velocity_tpm=c.twitter_velocity_tpm,
    )
```

If a `TwitterData` field name disagrees on landing — verify in B3
against the current `trading.schemas.TwitterData` and adjust the
mapping (single file) before tests are wired.

## 5. Enrichment pipeline

```python
# zetryn_bot/pipeline/enrich.py — final in B4

async def enrich_candidate(
    candidate: TokenCandidate,
    enrichers: list[TokenEnricher],
    session: aiohttp.ClientSession,
) -> TokenCandidate:
    result = candidate
    for enricher in enrichers:
        try:
            result = await enricher.enrich(result.address, result, session)
        except Exception as exc:
            logger.bind(component=enricher.name).warning(
                f"enrich failed: {exc!r}"
            )
    return result
```

Sequential, not parallel. Rationale: enrichers form an order-sensitive
chain (Helius populates `holder_count` and metadata first; RugCheck
reads the mint; GMGN populates the wallet-label fields; Jupiter is a
price fallback; Twitter runs last on the now-symbol-known candidate).
Parallelizing with `asyncio.gather` is a perf optimization that lands
when M3 profiling justifies it, not now.

## 6. Sinks

```python
# zetryn_bot/sinks/log_sink.py

class LogSink:
    name = "log"
    def __init__(self, level: str = "info") -> None:
        self._level = level

    async def emit(self, decision: Decision) -> None:
        logger.bind(component="sink.log").log(
            self._level.upper(),
            f"decision mint={decision.token_mint} action={decision.action} "
            f"score={decision.scores.final:.2f} reason={decision.reason}",
        )


# zetryn_bot/sinks/list_sink.py

class ListSink:
    name = "list"
    def __init__(self) -> None:
        self.decisions: list[Decision] = []

    async def emit(self, decision: Decision) -> None:
        self.decisions.append(decision)
```

## 7. Testing

### 7.1 Layout

```
tests/
├── conftest.py                          # fixtures: ListSink, FakeAgent, sample candidate
├── adapters/
│   └── test_token_input.py             # 6–8 tests: per-field mapping, source literal,
│                                       # pumpfun branching, twitter sub-mapping,
│                                       # empty / None defaults
├── pipeline/
│   ├── test_enrich.py                  # 3 tests: happy path, transient enricher failure
│                                       # pass-through, order preserved
│   └── test_runner.py                  # 6 tests: scanner→sink happy path,
│                                       # adapter error → synthetic abort emitted,
│                                       # agent exception → no emit + logger.error,
│                                       # abort decision still emitted (audit),
│                                       # enricher failure → still reaches agent
├── sinks/
│   └── test_sinks.py                   # 2 tests: ListSink collects in order,
│                                       # LogSink calls logger at configured level
└── integration/
    └── test_real_scanner_agent.py      # 1 test: build_scanner(llm_client=None)
                                       # invoked end-to-end with a hand-built
                                       # TokenInput; assert Decision shape
                                       # (rule-only path, no API key, no network)
```

Target: ~18 tests total, all hijau in CI.

### 7.2 Fixtures (`conftest.py`)

- `sample_candidate` — a deterministic `TokenCandidate` with sane
  defaults across every field group.
- `list_sink` — fresh `ListSink` per test.
- `fake_agent` — a stub with `async def ainvoke(ctx) → Decision(...)`,
  configurable to return abort / buy / raise.
- `fake_scanner` — yields a fixed list of `TokenCandidate`s then stops.

### 7.3 Smoke (out-of-band)

`scripts/m2_smoke.py` (mirror `scripts/m1_smoke.py`):

- `M2_SMOKE_LIVE=1 python scripts/m2_smoke.py` → run one
  `DexscreenerNewPairs` for ~10 s with no enrichers, `llm_client=None`,
  `LogSink`. Verifies end-to-end against real data without burning any
  API key.
- Unflagged invocation prints "set `M2_SMOKE_LIVE=1` to run live"
  and exits 0.

### 7.4 CI

Update `.github/workflows/ruff.yml` to add `pytest tests/ -q` after the
ruff steps. Smoke stays manual.

## 8. Dependencies

Adds to `[project.dependencies]` in `pyproject.toml`:

```toml
"zetryn-trading @ git+ssh://git@github-zetryn/zetryn-ai/ai-agent.git@<commit-sha>",
```

A commit SHA is pinned (not a branch / tag) so schema drift is loud and
deterministic. When `zetryn-trading` publishes to PyPI (`v1.0.0`), this
dep flips to the PyPI version in a follow-up commit.

`[project.optional-dependencies].dev` gains `pytest-asyncio` if not
already present.

## 9. Out of scope

- `main.py` orchestration runtime — M3. M2 ships `BotPipeline` as a
  library; running it requires a few lines of user / smoke-script glue.
- `RedisDecisionSink` — M3. The Protocol is in place; the impl lands
  when M3's orchestration needs fan-out to a dashboard / worker.
- Multi-agent fanout (run `scanner` + `sniper` + `graduation` in
  parallel and aggregate) — M3+.
- Specialized agent wiring (`build_kol_copytrade`,
  `build_graduation`, `build_lifecycle`, `build_confluence`,
  `build_dip_buy`, `build_organic_detector`) — these require
  specialized context objects (`KOLContext`, `GraduationContext`,
  `PositionContext`, ...) that the bot must construct from its own
  state. That's a separate wiring milestone after orchestration exists.
- Wallet / execution / persistence / notifier — M4 / M5 / M6 / M7.
- Performance benchmarking, parallel enrichment — deferred.

## 10. Execution sub-phases

| Sub-phase | Scope | Files | Verification |
|---|---|---|---|
| **B1** | Add `zetryn-trading` git+ssh dep + pinned SHA to `pyproject.toml`. `pip install -e ".[dev]"` in conda env `zetryn-bot`. Import smoke. | 1 | `python -c "from zetryn.strategies.agents.scanner import build_scanner; from trading.schemas import TokenInput, TradingContext, ScannerConfig, Decision"` |
| **B2** | `zetryn_bot/sinks/` — Protocol + `LogSink` + `ListSink`. Unit tests. | 4 + 1 | `pytest tests/sinks/ -q` clean |
| **B3** | `zetryn_bot/adapters/token_input.py` — pure mapping + `_map_source` + `_pumpfun_or_none` + `_twitter_data`. Verify field names against the actual `trading.schemas` and reconcile (single file). Unit tests. | 2 + 1 | `pytest tests/adapters/ -q` clean |
| **B4** | `zetryn_bot/pipeline/` — `enrich.py` + `runner.py` with error matrix. Unit + integration tests. | 3 + 2 | `pytest tests/pipeline/ tests/integration/ -q` clean |
| **B5** | `scripts/m2_smoke.py`. Bump `__version__` and `[project].version` → `0.2.0`. CHANGELOG entry. README "Phase 2" rewrite. Design doc Status → `Shipped (v0.2.0)`. ROADMAP M2 → ✅. CI workflow add pytest. | 6 | Full suite clean, smoke live opt-in runs, `ruff check` clean, `from zetryn_bot import __version__; assert __version__ == "0.2.0"` |

Sub-phases B1–B4 commit with random identity via `./scripts/commit-as.sh
random`. B5 (minor version bump) uses the `zetryn` identity per
`CLAUDE.md` rule.

## 11. Definition of done

M2 is shipped when:

1. `zetryn_bot/adapters/token_input.py` exports `to_token_input`;
   mapping covers every non-trivial `TokenCandidate` field that has a
   `TokenInput` counterpart.
2. `zetryn_bot/pipeline/runner.py` `BotPipeline.run()` executes
   end-to-end with the error-handling matrix in §3.4.
3. `DecisionSink` Protocol + `LogSink` + `ListSink` shipped under
   `zetryn_bot/sinks/`.
4. `pytest tests/ -q` is green: ≥ 15 unit tests + 1 integration test
   (real `build_scanner(llm_client=None)`).
5. `ruff check` and `ruff format --check` pass on `zetryn_bot/` and
   `tests/`.
6. `scripts/m2_smoke.py` exists and runs successfully when
   `M2_SMOKE_LIVE=1` is set.
7. `__version__ = "0.2.0"` in `zetryn_bot/__init__.py` and
   `pyproject.toml`.
8. `CHANGELOG.md` has a `## [0.2.0]` entry summarizing M2.
9. This design doc Status changes from `Draft` → `Shipped (v0.2.0)`.
10. `ROADMAP.md` row for M2 flips to `✅ shipped` / `v0.2.0`.

## 12. Risks acknowledged

- **Schema drift in `zetryn-trading`** (`TokenInput`, `TwitterData`,
  `WalletIntel`, `Decision` field names / types) — integration test
  catches contract regressions; commit-SHA pinning of the dep keeps
  drift deterministic; adapter is one file to update.
- **`build_scanner` signature change** — `BotPipeline.__init__(agent=...)`
  accepts any compiled `Graph`, so a builder signature change does not
  cascade into the pipeline.
- **`TokenSource` literal too narrow** — `_map_source` falls back to
  `"manual"` for `geckoterminal` and `telegram` for now; if the
  framework widens the literal, the table updates in one place.
- **Long-running pipeline + transient agent crashes** — `error`-level
  logging + continue-loop policy keeps the runtime alive; M7 notifier
  is the upstream consumer of those error events when it lands.

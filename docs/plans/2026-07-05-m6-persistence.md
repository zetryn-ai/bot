# M6 — Persistence (PostgreSQL)

**Date:** 2026-07-05
**Status:** Shipped (v0.6.0)
**Target version:** v0.6.0

M4/M5 track open positions, closed trades, and the daily-loss circuit breaker
entirely in memory — a restart (or crash) loses all of it, and the daily
circuit breaker silently resets mid-day. M6 adds PostgreSQL-backed persistence
so the bot survives restarts, and wires the framework's existing
`DecisionLog` / `ReflectiveNode` primitives (never previously activated) to
close the reflective-learning loop.

## 1. Goals

1. Open positions and closed-trade history survive a restart or crash.
2. The daily-loss circuit breaker (`RiskManager`) persists — a restart does
   not silently re-open trading past the day's loss limit.
3. In `EXECUTION_MODE=live`, positions restored from the DB are reconciled
   against actual on-chain token balances before being handed to the monitor
   loop — a mismatch is flagged, not auto-traded.
4. The framework's `DecisionLog` (read by `ReflectiveNode` to inject
   "lessons from recent losses" into the analyst prompt) becomes usable by
   supplying a Postgres-backed `MemoryStore` — activating a learning loop
   that has existed in the framework since M2/M3 but was never wired.
5. Postgres unreachable at startup → log once, fall back to in-memory state
   (M4/M5 behaviour) — never a crash.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Decision log | **Implement `PostgresStore` satisfying the framework's `MemoryStore` Protocol**, wire it into `DecisionLog` and pass `decision_log=` to `build_scanner()`. This activates `ReflectiveNode` without any decision-tier logic in the bot — the framework still owns what "reflection" means; the bot only supplies storage. |
| 2 | DB stack | **SQLAlchemy 2.0 async ORM** (declarative models, `AsyncSession`), `asyncpg` as the driver underneath. One stack for everything — models double as Alembic's autogenerate source; raw SQL/Core (`text()`) stays available for anything ORM patterns don't fit well. |
| 3 | Migrations | **Alembic**, `env.py` reading `DATABASE_URL` from `Settings`, `target_metadata` from `db/models.py`. |
| 4 | Crash recovery (live) | Positions loaded from the DB are **verified against on-chain token balance** before joining the monitor loop. Mismatch → marked `needs_review` in the DB, excluded from auto-trading, logged as a loud `WARNING`. Paper mode skips this (nothing on-chain to check against). |
| 5 | Circuit breaker | Daily realized PnL **persists** (`risk_state` table, one row per date) — a restart does not reset the circuit breaker mid-day. |
| 6 | Local dev | `docker-compose.yml` at the repo root (Postgres 16, for VPS / anyone without a local install); works equally against a natively-installed Postgres via `DATABASE_URL`. |

Additional constraints:

- **Boundary:** position/trade/PnL persistence is bot-owned infrastructure
  (per `CLAUDE.md`'s M4–M6 row). `DecisionLog`/`ReflectiveNode` *logic*
  remains framework-owned — the bot supplies a storage backend, nothing more.
- **Technical necessity, not a preference:** `Position.opened_at` (M4/M5) is
  `time.monotonic()`-based, which is only meaningful within one process
  lifetime. Persisting it requires a wall-clock timestamp in the DB and a
  monotonic-offset reconstruction on load (see §4) — otherwise `max_hold_s`
  comparisons break silently after every restart.
- Postgres unreachable at startup → log an error once, continue with
  in-memory state (mirrors the M5 wallet-failure fallback pattern) — never a
  hard crash.
- Zero changes to `scanners/`, `pipeline/`, `runtime/registry.py` beyond
  what startup wiring requires.

## 3. Architecture

```
Settings.database_url
        │
        ▼
  AsyncEngine + async_sessionmaker
        │
   ┌────┼─────────────────┬──────────────────────┐
   ▼                      ▼                      ▼
PositionRepo         RiskStateRepo          PostgresStore
(positions,          (risk_state:           (decision_log_kv:
 closed_trades)        1 row/date)            generic namespaced KV)
   │                      │                      │
   ▼                      ▼                      ▼
PositionTracker      RiskManager            DecisionLog (framework)
.load_and_reconcile()  .load()                    │
   │                                               ▼
   │ (live only: verify vs on-chain balance)  ReflectiveNode (framework)
   ▼                                          → lessons_text in analyst prompt
monitor_loop() as before
```

### 3.1 New modules (all under `zetryn_bot/`)

| Path | Responsibility |
|---|---|
| `db/__init__.py` | package |
| `db/engine.py` | `create_async_engine()` + `async_sessionmaker` from `Settings.database_url` |
| `db/models.py` | SQLAlchemy 2.0 ORM: `Position`, `ClosedTrade`, `RiskState`, `DecisionLogEntry` |
| `db/memory_store.py` | `PostgresStore` — implements the framework's `MemoryStore` Protocol over `decision_log_kv` |
| `db/position_repo.py` | `PositionRepo` — save/delete open positions, save closed trades, load-with-monotonic-bridge |
| `db/risk_repo.py` | `RiskStateRepo` — load/save today's realized PnL |
| `alembic/env.py`, `alembic/versions/0001_init.py` | migrations, autogenerated from `db/models.py` |
| `docker-compose.yml` | Postgres 16 for local/VPS dev |

### 3.2 Schema (4 tables)

```python
class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    mint: Mapped[str] = mapped_column(index=True, unique=True)
    symbol: Mapped[str]
    size_sol: Mapped[Decimal]
    tokens_atomic: Mapped[int]
    take_profit_pct: Mapped[Decimal]
    stop_loss_pct: Mapped[Decimal]
    max_hold_s: Mapped[Decimal]
    confidence: Mapped[Decimal]
    opened_at: Mapped[datetime]              # wall-clock (timestamptz)
    execution_mode: Mapped[str]              # "paper" | "live"
    status: Mapped[str] = mapped_column(default="open")  # "open" | "needs_review"

class ClosedTrade(Base):
    __tablename__ = "closed_trades"          # fully denormalized — no FK to positions
    id: Mapped[int] = mapped_column(primary_key=True)
    mint: Mapped[str]
    symbol: Mapped[str]
    size_sol: Mapped[Decimal]
    tokens_atomic: Mapped[int]
    exit_sol: Mapped[Decimal]
    pnl_sol: Mapped[Decimal]
    reason: Mapped[str]
    confidence: Mapped[Decimal]
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime]
    execution_mode: Mapped[str]

class RiskState(Base):
    __tablename__ = "risk_state"
    date: Mapped[date] = mapped_column(primary_key=True)  # one row per day
    realized_pnl_sol: Mapped[Decimal]
    updated_at: Mapped[datetime]

class DecisionLogEntry(Base):
    __tablename__ = "decision_log_kv"        # generic — backs PostgresStore
    ns: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    exp: Mapped[datetime | None]             # TTL, mirrors InMemoryStore/JSONFileStore
```

## 4. Components

**`PositionRepo`** — `save(position, execution_mode)` upserts by mint;
`delete(mint)` removes an open position; `save_closed_trade(trade,
execution_mode)` inserts into `closed_trades`; `load_all_open(now_fn=...)`
reconstructs each `Position` with `opened_at = now_fn() - elapsed`, where
`elapsed = wall_clock_now - row.opened_at` — the monotonic-offset bridge that
keeps `max_hold_s` comparisons correct across a restart.

**Live reconciliation** (`PositionTracker.load_and_reconcile(execution_mode,
rpc)`): for each loaded position, when `execution_mode == "live"`, fetch the
actual on-chain token balance and compare to `tokens_atomic`. Mismatch →
`PositionRepo.mark_needs_review(mint)`, excluded from `_open` (never
auto-traded), logged at `WARNING`. Paper mode skips the check entirely.

**`RiskStateRepo`** — `load_today(date) -> Decimal` (0 if no row yet),
`save(date, realized_pnl_sol)` upserts by date. `RiskManager` gains an
optional `repo`: `load()` restores `_realized_pnl_today` at startup;
`record_close()` now also persists.

**`PostgresStore`** — implements `get`/`put`/`delete`/`query` over
`decision_log_kv`, honoring `exp` exactly like `InMemoryStore`/`JSONFileStore`
(expired treated as absent). Wired as
`DecisionLog(PostgresStore(session_factory))`, passed to
`build_scanner(decision_log=...)` when `ENABLE_DECISION_LOG=true`.

## 5. Wiring (`__main__.build_orchestrator`)

```python
engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine)

risk = RiskManager(RiskConfig(...), repo=RiskStateRepo(session_factory))
await risk.load()

tracker = PositionTracker(executor, jupiter, risk, repo=PositionRepo(session_factory), ...)
await tracker.load_and_reconcile(settings.execution_mode, rpc if live_mode else None)

decision_log = None
if settings.enable_decision_log:
    decision_log = DecisionLog(PostgresStore(session_factory))
agent = build_scanner(llm_client=llm, decision_log=decision_log)
```

A DB connection failure at any point above logs one `ERROR` and continues
with in-memory-only state (`repo=None` everywhere) — mirrors the M5
wallet-failure fallback; never a hard crash.

### 5.1 `Settings` additions

```python
database_url: str = "postgresql+asyncpg://zetryn:zetryn@localhost:5432/zetryn_bot"
enable_decision_log: bool = False
```

### 5.2 New dependencies

`sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`.

## 6. Testing

Integration tests run against a real Postgres (`DATABASE_URL_TEST`,
auto-migrated in a fixture) — no mock DB; `pytest.mark.skipif` when
unreachable (CI provisions a Postgres service).

| Test | Coverage |
|---|---|
| `tests/test_position_repo.py` | save/load/delete round-trip; monotonic↔wall-clock bridge (a position aged 100s reads back as ~100s old after a simulated restart) |
| `tests/test_risk_repo.py` | save/load daily PnL; date rollover doesn't bleed into another day |
| `tests/test_postgres_store.py` | `MemoryStore` Protocol contract (get/put/delete/query); TTL expiry |
| `tests/test_reconciliation.py` | live: on-chain mismatch → `needs_review`, excluded from `_open`; paper: no reconciliation, everything loads into `_open` |
| Extend `test_position_tracker.py` / `test_risk.py` | constructors accept `repo=None` — M4/M5 in-memory behaviour unchanged |

`scripts/m6_smoke.py` — Postgres reachable: round-trip save/load a position +
daily PnL. Unreachable: verify the in-memory fallback engages cleanly (not a
crash). Added to CI (with a Postgres service container).

## 7. Out of scope

- Blacklist / KnowledgePack persistence (framework primitives, not asked for).
- Multi-instance/HA Postgres, connection pooling tuning beyond defaults.
- A UI/CLI to review `needs_review` positions — M9 (dashboard) territory;
  for M6 a direct SQL query is the documented workaround.
- Notifier on reconciliation mismatch (Telegram/Discord alert) — M7.

## 8. Execution sub-phases

| Sub-phase | Scope |
|---|---|
| **B1** | `db/models.py`, `db/engine.py`, Alembic setup + initial migration, `docker-compose.yml`. |
| **B2** | `PositionRepo`, `RiskStateRepo`, `PostgresStore` + repo tests against real Postgres. |
| **B3** | Wire `PositionTracker`/`RiskManager` to accept `repo=`; `__main__` startup sequence (load, reconcile, `decision_log`); `scripts/m6_smoke.py`; remaining tests. |
| **B4** | Version `0.6.0`, CHANGELOG, ROADMAP M6 → ✅, this doc → Shipped, tag + release. |

## 9. Definition of done

1. Kill `-9` the process mid-run, restart — open positions and today's
   realized PnL are unchanged, not reset.
2. Live mode: an on-chain/DB mismatch on a restored position is marked
   `needs_review` and excluded from auto-trading, with a clear `WARNING`.
3. `ENABLE_DECISION_LOG=true` activates `ReflectiveNode` — verifiable via the
   framework's debug logging showing `lessons_text` injected into the
   analyst prompt.
4. Postgres unreachable → in-memory fallback, no crash.
5. `alembic upgrade head` runs cleanly from an empty database.
6. `python -m pytest` green (M5's 76 + new tests); `ruff` clean.
7. `scripts/m6_smoke.py` passes (both the DB-reachable and fallback paths).
8. `__version__ = "0.6.0"`; CHANGELOG; ROADMAP M6 ✅; this doc → Shipped.

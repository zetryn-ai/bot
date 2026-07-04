# M4 — Execution Layer (paper-trading first)

**Date:** 2026-07-04
**Status:** Shipped (v0.4.0)
**Target version:** v0.4.0

M3 produces `Decision`s from the LLM analyst but only logs them. M4 closes the
loop: turn an `alert` into a sized paper trade at real Jupiter prices, track the
open position, and auto-exit on take-profit / stop-loss / max-hold — recording
PnL. No real transactions, no keypair, no funds: the execution *engine* is built
behind swappable interfaces so a real `LiveExecutor` slots in at M5 (wallet)
without touching the risk or position layers.

## 1. Goals

1. `Decision(action=alert)` with sufficient confidence → a risk-sized paper buy
   at a real Jupiter quote price, tracked as an open position.
2. Open positions are monitored and auto-closed on TP / SL / max-hold, with PnL
   recorded and logged.
3. Everything is behind a swappable `Executor` Protocol — `PaperExecutor` now,
   `LiveExecutor` at M5 — so the risk/position layers never change.
4. M4 plugs in via the existing `DecisionSink` Protocol (a new `ExecutionSink`
   behind a `TeeSink`), so `BotPipeline` / `Orchestrator` are untouched.
5. Off by default (`EXECUTION_ENABLED=false`) — the M3 runtime behaves
   identically until execution is explicitly turned on.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Execution mode | **Paper-trading first**, behind a swappable `Executor` Protocol. `PaperExecutor` uses real Jupiter quote prices (incl. price impact) but performs no transaction and holds no keypair. `LiveExecutor` + signing land with M5. This dissolves the "signing before M5" tension. |
| 2 | Trigger + sizing | **Minimal `RiskManager`.** Buy when `action=alert` AND `confidence >= min_confidence`; `size = base_size_sol × confidence`; cap `max_positions`; daily-loss **circuit breaker**. Full per-strategy sizing / tiered slippage deferred (uncalibrated). |
| 3 | Exits | **Rule-based** in `PositionTracker`: take-profit % / stop-loss % / max-hold-time, snapshot at entry, checked by polling Jupiter quotes per open position. LLM lifecycle exits (`build_lifecycle`) are decision-tier → **M10**. |
| 4 | State | **In-memory** (open-positions dict + closed-trades list) + logging. Restart resets — acceptable for paper. Durable persistence (PostgreSQL) is **M6**. |
| 5 | Integration | **`ExecutionSink` implementing `DecisionSink` + `TeeSink`** (log + execute). Zero change to `BotPipeline` / `Orchestrator` — exactly what the swappable-sink design was for. |

Additional constraints:

- **Boundary:** execution / slippage / position tracking / PnL are bot-owned
  (M4–M6). The alert/watch/skip decision stays framework-owned (done in M3).
  No decision-tier logic in `execution/`.
- **Per API-verification rule:** the Jupiter quote endpoint/host is verified
  against current docs at implementation time (Jupiter has shifted hosts /
  `lite-api`); treat any hardcoded URL as suspect until checked live.
- Zero changes to `scanners/`, `adapters/`, `models/token.py`, and the M3
  runtime/registry beyond the sink wiring.

## 3. Architecture

```
Decision (M3 pipeline)
   │  TeeSink
   ├───────────────► LogSink          (unchanged: log every decision)
   └───────────────► ExecutionSink
                         │  action=alert & confidence >= threshold & mint not already held
                         ▼
                     RiskManager ── action/conf → circuit-breaker → max-concurrent ──► reject (log)
                         │  SwapRequest(mint, size_sol, tp%, sl%, max_hold_s)
                         ▼
                     Executor (Protocol)
                         │  PaperExecutor: Jupiter quote → simulated fill (real price+impact, NO tx)
                         ▼
                     PositionTracker ── in-memory open dict; monitor loop polls Jupiter per position
                         │  exit rule TP / SL / max-hold
                         ▼
                     PaperExecutor.sell → ClosedTrade → RiskManager.record_close (circuit breaker) + log PnL
```

### 3.1 New modules (all under `zetryn_bot/`)

| Path | Responsibility |
|---|---|
| `execution/__init__.py` | package |
| `execution/jupiter.py` | `JupiterQuote` — read-only Jupiter V6 quote (price + impact), no tx |
| `execution/executor.py` | `Executor` Protocol + `PaperExecutor` (quote → simulated fill) + `SwapRequest` |
| `execution/risk.py` | `RiskManager` — alert/confidence gate, sizing, max-concurrent, daily-loss circuit breaker |
| `execution/position.py` | `Position` / `ClosedTrade` models + `PositionTracker` (in-memory state + monitor loop) |
| `pipeline/sinks.py` (extend) | `ExecutionSink` + `TeeSink` |

### 3.2 `Settings` additions

```python
execution_enabled: bool = False   # master switch; off = identical M3 behaviour
risk_base_size_sol: float = 0.1
risk_min_confidence: float = 0.6
risk_max_positions: int = 5
risk_daily_loss_limit_sol: float = 1.0
exit_tp_pct: float = 0.30         # +30% take profit
exit_sl_pct: float = 0.15         # -15% stop loss
exit_max_hold_s: float = 1800     # 30 min
exec_poll_interval_s: float = 5.0
```

## 4. Components

**RiskManager.evaluate(candidate, decision, open_count) → SwapRequest | None**
Three sequential gates (each reject → `None`, logged): action==alert &
confidence≥min → daily-loss circuit breaker → open_count<max. Then
`size = base_size_sol × confidence`. Carries exit params (snapshot at entry).
`record_close(pnl_sol)` accumulates daily realized PnL (reset on date change).

**PaperExecutor** — `buy(req)`: quote SOL→mint, `entry_price = size_sol / out_tokens`,
return `Position`. `sell(pos, reason)`: quote mint→SOL, `pnl = exit_sol - size_sol`,
return `ClosedTrade`. Real prices, no tx, no keypair.

**PositionTracker** — `_open: dict[mint→Position]`, `_closed: list[ClosedTrade]`.
`monitor_loop()` (supervised task): per open position, poll Jupiter, compute
`pnl_pct`, check TP→SL→max-hold in order, sell + record. `stats()` →
open/closed/win-rate/total-PnL for periodic logging. One position per mint
(dict-keyed dedup); `ExecutionSink` also guards `mint in _open` before buying.

**ExecutionSink.emit** — skip if already held; `RiskManager.evaluate`; on a
`SwapRequest`, `executor.buy` → `tracker.add`. **TeeSink** fans one decision to
several sinks, isolating per-sink errors.

## 5. Wiring

`__main__.build_orchestrator`: when `execution_enabled`, build
`JupiterQuote` → `PaperExecutor` → `RiskManager` → `PositionTracker`, set
`sink = TeeSink([LogSink(), ExecutionSink(risk, executor, tracker)])`, and
register `tracker.monitor_loop()` as a supervised task on `Orchestrator.start()`
(add an optional `extra_tasks` hook to the orchestrator, or start it in
`_run`). When off, `sink = LogSink()` and no monitor loop — identical to M3.

## 6. Testing (offline, no network/tx)

| Test | Coverage |
|---|---|
| `tests/test_risk.py` | action/confidence gate, circuit breaker trips on daily loss, max-concurrent, `size = base×conf` |
| `tests/test_paper_executor.py` | buy/sell with a mocked `JupiterQuote` → entry/exit/PnL correct |
| `tests/test_position_tracker.py` | TP / SL / max-hold each trigger exactly (mock quote, injected clock); stats/win-rate |
| `tests/test_execution_sink.py` | alert→buy, non-alert→no-op, already-held dedup, TeeSink fan-out + error isolation |

Plus `scripts/m4_smoke.py` — offline: a synthetic `alert` decision →
RiskManager → PaperExecutor (mocked Jupiter) → PositionTracker → forced exit →
assert PnL + stats. Added to CI.

## 7. Out of scope

- Real on-chain swaps, keypair, signing, RPC submission, confirmation —
  `LiveExecutor`, arrives with M5 (wallet).
- LLM-driven exit / position re-evaluation (`build_lifecycle`) — M10.
- Durable persistence of positions / trades — M6.
- Notifier / alerting on fills — M7.

## 8. Execution sub-phases

| Sub-phase | Scope |
|---|---|
| **B1** | `execution/{jupiter,executor,risk,position}.py` + `Settings` fields + unit tests. |
| **B2** | `ExecutionSink` + `TeeSink` + `__main__` wiring + `scripts/m4_smoke.py` + sink tests. |
| **B3** | Version `0.4.0`, CHANGELOG, ROADMAP M4 → ✅, this doc → Shipped, tag + release. |

## 9. Definition of done

1. `EXECUTION_ENABLED=true python -m zetryn_bot` opens paper positions from
   alerts, monitors them, auto-exits on TP/SL/max-hold, and logs PnL + stats.
2. Default (`EXECUTION_ENABLED=false`) is byte-for-byte M3 behaviour.
3. `execution/{jupiter,executor,risk,position}.py` + sink extensions shipped.
4. `python -m pytest` green (M3's + ~4 new test files); `ruff` clean.
5. `scripts/m4_smoke.py` passes offline.
6. `__version__ = "0.4.0"`; CHANGELOG `## [0.4.0]`; ROADMAP M4 ✅; this doc Shipped.

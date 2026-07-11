# M10a — Exit Intelligence (framework lifecycle agent)

**Date:** 2026-07-11
**Status:** Shipped (v0.9.0)

## Problem

Entries are decided by the framework's LLM analyst; exits were three static
rules in `PositionTracker._exit_reason` (TP +30% / SL -15% / max-hold 30 min).
The framework's PL1 lifecycle agent (`build_lifecycle`, shipped in
`zetryn-trading` v1.1.0) was never consumed by anyone — the biggest alpha gap
identified in the 2026-07-11 review: smart entry, dumb exit. Most costly
pattern: a position runs +25%, momentum dies, and the bot rides it all the way
back down to the -15% SL or the 30-minute time stop.

## Scope split

ROADMAP M10 ("specialized strategy routing incl. PositionContext") covers two
independent deliverables. This milestone ships the PositionContext half:

- **M10a (this)** — exit intelligence: `PositionContext` per monitor tick →
  framework lifecycle agent → exit decision.
- **M10b (later)** — per-signal entry routing (sniper / graduation / KOL
  copy-trade / confluence agents).

## Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Decision mode | `rule` (deterministic, sub-ms, no LLM per 5s tick — free-tier quota untouched). `llm`/`hybrid` modes are a follow-up once per-position evaluation intervals exist. |
| 2 | Integration point | New `zetryn_bot/execution/lifecycle.py` `LifecycleEngine`; `PositionTracker` takes an optional `lifecycle=` and calls it where `_exit_reason` used to run. `lifecycle_enabled=False` default keeps v0.8.0 behaviour bit-for-bit. |
| 3 | Peak tracking | In-memory dict in the engine (needed for the trailing stop). Restart resets peaks — trailing re-arms from the first post-restart quote. Accepted for M10a; persisting peaks is a follow-up column. |
| 4 | TP ladder | Single full-exit rung `[(exit_tp_pct, 1.0)]` — the `Executor` protocol only sells whole positions. Multi-rung partial exits (`scale_out`) are M10.1 (needs partial-sell support in Paper/Live executors + DB position updates). |
| 5 | Token snapshot | Minimal `TokenInput(mint, symbol)` — the tracker holds no fresh enrichment, so the emergency (rug) gate is inert for now. Storing entry snapshots / re-enriching per tick is a follow-up. |
| 6 | Close-reason taxonomy | Keep existing DB values (`take_profit`, `stop_loss`, `max_hold`) and add `trailing_stop` + `emergency` — dashboards keep working, new exits are distinguishable. |

## What changed vs static exits

Gates per tick, framework order: `emergency_exit → hard_stop_loss →
time_stop → trailing_stop → tp_ladder → rule_hold`. TP/SL/max-hold reproduce
the old behaviour from the same env vars. The new alpha is the trailing stop:
arms once peak PnL ≥ `EXIT_TRAILING_ARM_PNL_PCT` (default +20%), exits when
the position gives back `EXIT_TRAILING_DRAWDOWN_PCT` (default 50%) of its
peak *value* — "momentum died" exits bank profit instead of round-tripping.

## Config

```env
LIFECYCLE_ENABLED=true          # default false — static exits
EXIT_TRAILING_ARM_PNL_PCT=0.20
EXIT_TRAILING_DRAWDOWN_PCT=0.50
# existing EXIT_TP_PCT / EXIT_SL_PCT / EXIT_MAX_HOLD_S feed the same gates
```

## Follow-ups

- M10.1 — partial exits (multi-rung ladder) once executors support partial sells.
- Persist peak PnL per position (survives restarts).
- Store the entry `TokenInput` snapshot → arms the emergency rug gate and
  unlocks `llm`/`hybrid` lifecycle modes with a real fact sheet.
- M10b — per-signal entry routing.

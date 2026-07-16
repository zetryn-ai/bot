# Strategy Rework — "Zetryn Focus"

**Date:** 2026-07-17
**Status:** Approved
**Scope:** Decision/strategy layer only. Infra (scanners, enrichers, paper
execution, DB, dashboard, deploy) unchanged — it is proven-healthy. Trade
history is PRESERVED (evidence). Bot paper service is currently STOPPED on
the VPS; this rework precedes restart.

> Locked via brainstorming 2026-07-17. Driven by live-data forensics: the
> 6-route spread was mostly marginal-to-negative, and the graduation route —
> previously best (+0.37 SOL / 65% WR on 07-14) — collapsed to 0% WR / −1.03
> SOL on 07-16 by buying into post-migration dumps (v0.12.1 fantom-fix
> aftermath). Balance fell +0.52 → −1.77 SOL, all from graduation.

## Goal

On 1 SOL capital, run **two sharp routes done well** instead of six blunt
ones, with **protection moved upstream** (entry + sizing) because thin
memecoin liquidity makes catastrophic dumps un-exitable. Honest about limits.

## Locked decisions

1. **Narrow to 2 buying routes:** `graduation` + `sniper`. Park `momentum`,
   `social`, `launch`, `other` (scan + log only, no buys).
2. **Graduation entry = wait-and-confirm + momentum/liquidity guard** (fixes
   buying into dumps).
3. **Sizing = liquidity-aware + hard cap** (kills the "0.09 SOL into a $400
   pool" class).
4. **Exits = keep TP ladder + SL + stagnation; add faster polling + wire the
   (currently inert) emergency rug-gate.** Accept catastrophic dumps are
   bounded by entry+sizing, not exits.
5. **Sniper = observation-first;** wire CalibrationMap to record score→outcome,
   set the buy threshold from empirical data near go-live (no blind buying of
   the highest-rug route).

## Route lifecycle (why these two)

```
 SNIPER (0–120s, bonding curve)  →  GRADUATION (migration event)  →  [launch/momentum: later, DEX]
 pump.fun WS newToken               pump.fun WS migration            REST DEX scanners (parked)
```
Launch overlaps graduation for pump.fun tokens (same token, caught minutes
later via REST vs the migration instant via WS) and its unique slice
(non-pumpfun DEX launches) was breakeven → parked. Sniper (pre-DEX birth) and
graduation (migration instant) are the two earliest, highest-value points.

## Design by component (all bot-side unless noted; framework NOT modified)

### C1 — Route narrowing  (config)
- `ROUTE_CONF_FLOORS`: set `momentum:1.0,social:1.0,launch:1.0,other:1.0` →
  those routes never satisfy the buy floor (decisions still logged/observed).
- Keep `graduation` and `sniper` floors as-is.
- Rationale for conf-floor over size-mult-0: floor is the cleanest "observe
  but never buy"; size 0 risks a divide/RiskManager edge case.

### C2 — Graduation entry guard  (bot: `routing/gates.py` + `GraduationPipeline`)
- New `graduation_gate(candidate)` pre-filter (mirrors existing
  `momentum_gate`/`launch_gate` pattern):
  - **skip** if `price_change_5m_pct < 0` (already pumped and now dropping —
    the dump we kept buying), and
  - **skip** if `liquidity_usd < GRADUATION_MIN_LIQUIDITY_USD` (default 2000 —
    a real post-migration pool, not a dust artifact).
- **Wait-and-confirm:** the migration event is fresh; defer the decision by a
  short confirmation window so the initial dump reveals itself before we act.
  Implement as a per-mint delay in `GraduationPipeline` (default
  `GRADUATION_CONFIRM_DELAY_S=20`): on migration, stamp the candidate; only run
  the buy pipeline once ≥ delay has elapsed AND the guard passes on the
  re-checked (re-enriched) snapshot. If Jupiter still cannot quote, retry up to
  `GRADUATION_MAX_WAIT_S=180` then drop. No curve fallback (fantom source).

### C3 — Liquidity-aware sizing + hard cap  (bot: `execution/risk.py`)
- `RiskManager` size becomes:
  `size = min(base_size × confidence × route_mult, RISK_MAX_SIZE_SOL, liquidity_usd × RISK_MAX_POOL_PCT / sol_usd)`
- Defaults: `RISK_MAX_SIZE_SOL=0.03`, `RISK_MAX_POOL_PCT=0.01` (≤1% of pool).
- `sol_usd` from the existing Jupiter price helper (already used by the API).
  If price unavailable, fall back to the hard SOL cap only.
- Effect: thin pool → tiny position automatically; no position can dominate a
  pool (slippage bound). The 0.09-SOL-into-$400 class is impossible.

### C4 — Exits  (bot: `config` + `execution/position.py` + snapshot wiring)
- Faster monitor: `POSITION_POLL_INTERVAL_S` lowered (default 3 → 2s; tunable).
- **Emergency rug-gate:** persist the entry `TokenInput` snapshot on the
  position and feed it to `PositionContext.token` each lifecycle tick so the
  framework's `emergency_exit` gate (currently inert — no token data) can fire
  on rug signals. Bot supplies data; framework decides (boundary preserved).
- TP ladder / SL / stagnation unchanged.

### C5 — Sniper observation + calibration  (bot: calibration store; framework: CalibrationMap)
- Sniper buy threshold stays high (no buys). Every sniper decision already
  carries `sniper_score`; record `(score, outcome)` pairs.
- Wire the framework's `CalibrationMap` (exists since v1.2.0) with a small
  persistence store so we accumulate score→win-rate. Near go-live, set
  `SNIPER_SCORE_SMALL_BUY` from the empirical crossover, not a guess.

## Files touched

| File | Change |
|---|---|
| `zetryn_bot/config.py` | `graduation_min_liquidity_usd`, `graduation_confirm_delay_s`, `graduation_max_wait_s`, `risk_max_size_sol`, `risk_max_pool_pct`, `position_poll_interval_s` |
| `zetryn_bot/routing/gates.py` | `graduation_gate()` |
| `zetryn_bot/routing/graduation.py` (`GraduationPipeline`) | wait-and-confirm delay + guard + no-curve-fallback retry |
| `zetryn_bot/execution/risk.py` | liquidity-aware sizing + hard cap |
| `zetryn_bot/execution/position.py` | faster poll; entry-snapshot → PositionContext for emergency gate |
| `zetryn_bot/__main__.py` | apply graduation_gate to graduation route; calibration wiring |
| `.env` (VPS) + `.env.example` | new knobs + parked-route conf floors |

## Testing

- Unit: `graduation_gate` (skip on Δ5m<0 / low liq, pass on healthy);
  liquidity-aware sizing (cap binds on thin pool, pool% binds on large pool,
  SOL-price fallback); wait-and-confirm delay logic.
- Full suite green + ruff.
- Live paper verify after restart: graduation buys only on stable/rising
  post-migration tokens; position sizes ≤ cap; no −90% stop-loss cluster.

## Non-goals (YAGNI)

- No changes to `momentum`/`social`/`launch`/`other` decision logic (parked).
- No `sniper_score` weight changes (observe + calibrate instead).
- No framework release (all changes bot-side; framework used as-is).
- No live execution, no multi-user (separate branch), no data reset.

## Rollout

Implement in impact order, test each: **C1 + C3 (bound all damage) → C2
(graduation fix) → C4 (exits) → C5 (calibration).** Patch release, redeploy to
VPS, restart the paper service, verify live before the Saturday checkpoint.

# M10b — Specialized entry routing

**Date:** 2026-07-12
**Status:** Approved
**Target version:** v0.11.0

M3 wires every candidate — a 30-second-old pump.fun launch, a graduation
event, a trending token — into ONE generalist agent (`build_scanner`). The
framework has shipped specialized entry agents since v0.16.0 that none of our
signals reach. M10b adds the dispatcher: route each candidate to the agent
built for its signal shape, with the routing table config-driven so the
window-2 dry-run findings land as configuration, not code.

Developed on `feat/m10b-routing` (draft PR) while the 4×24h dry run holds
`main`; `ROUTING_ENABLED=false` default makes the merge behaviour-neutral.

## 1. Goals

1. Fresh pump.fun launches reach the **sniper** agent (rule mode —
   sub-millisecond, no LLM in the hot loop; launches live and die in seconds).
2. Pump.fun→Raydium migrations reach the **graduation** agent with a real
   `GraduationEvent` (fill time computed from our own launch observations).
3. Everything else keeps the current generalist **scanner** path (LLM +
   v1.3.0 momentum inputs) — zero behaviour change for those sources.
4. Risk stays GLOBAL (one circuit breaker, one max-positions cap, cooldown,
   blocked sources), with per-route size multipliers + confidence floors.
5. `ROUTING_ENABLED=false` (default) = bit-for-bit v0.9.x behaviour.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Scope | **Sniper + Graduation + Scanner fallback.** KOL Copy-Trade / Confluence / Dip-Buy / Growth Detector deferred to M10c — each needs new bot-side infrastructure (real-time wallet event feeds, per-token time-series store), not just routing. |
| 2 | Routing mechanism | **Characteristic rules, config-driven, first-match.** e.g. `source=pumpfun_ws AND age<=SNIPER_MAX_AGE_S → sniper`; `source=pumpfun_migration → graduation`; else scanner. Thresholds in `.env` so window-2 findings are poured in as numbers. |
| 3 | Sniper mode | **Pure rule** (framework default): `fast_safety → fast_market → rule_size_and_buy`, no LLM. Rule-mode buys carry `action="buy"`, `confidence=0.6`. |
| 4 | Risk & sizing | **One global RiskManager** (daily breaker, max positions, re-entry cooldown, blocked sources all stay global) + per-route `size_multiplier` and `conf_floor` from config. Total exposure stays singly-capped. |

## 3. Architecture

```
Orchestrator queue → worker → RoutedPipeline.process(candidate)   (BARU)
                                 │ 1. LaunchMemory.record(candidate)   [pumpfun_ws]
                                 │ 2. first-match rule → route name
                                 ├─ "sniper"     BotPipeline(agent=build_sniper(),        config=SniperConfig)
                                 ├─ "graduation" GraduationPipeline(build_graduation())   ← GraduationContext
                                 └─ "scanner"    BotPipeline(agent=build_scanner(llm), config=ScannerConfig)
                                 │ 3. decision.meta["route"] = name
                                 ↓
                     TeeSink → LogSink + ExecutionSink  (SHARED, satu untuk semua route)
                                  → RiskManager GLOBAL
                                     gate 1  : action in buy_actions, conf >= min_confidence
                                     gate 1d : conf >= ROUTE_CONF_FLOORS[route]      (BARU)
                                     sizing  : base × conf × ROUTE_SIZE_MULTIPLIERS[route] (BARU)
```

### 3.1 New modules (`zetryn_bot/routing/`)

| Path | Responsibility |
|---|---|
| `routing/launch_memory.py` | `LaunchMemory` — TTL dict `mint → launch_monotonic_ts`, fed from `pumpfun_ws` candidates; answers `fill_seconds(mint)` when the matching `pumpfun_migration` arrives. In-memory only (a restart loses pending launches → those migrations route with `fill_seconds=0` = unknown). |
| `routing/router.py` | `RoutedPipeline` — same `process(candidate, session)` surface as `BotPipeline` so the Orchestrator does not change. Owns the rule list, the per-route pipelines, LaunchMemory feeding, and stamps `decision.meta["route"]`. |
| `routing/graduation.py` | `GraduationPipeline` — enrich → `to_token_input` → `GraduationEvent` (best-effort, §4) → `GraduationContext` → `graph.run` → shared sink. Mirrors `BotPipeline.process`'s error handling (synthetic abort on failure). |

### 3.2 Touched modules

- `config.py` — `routing_enabled` (False), `sniper_max_age_s` (120),
  `route_size_multipliers` ("sniper:0.5,graduation:1.0,scanner:1.0"),
  `route_conf_floors` ("sniper:0.6,graduation:0.6,scanner:0.6") + parsers.
- `execution/risk.py` — `RiskConfig.route_size_multipliers` /
  `route_conf_floors`; `evaluate()` reads `decision.meta["route"]`
  (missing/unknown route → multiplier 1.0, no extra floor).
- `__main__.py` — when `routing_enabled`: build the three pipelines (shared
  enrichers + shared sink), wrap in `RoutedPipeline`, hand to Orchestrator.
- `.env.example` — routing section; note that sniper buys need `buy` in
  `RISK_BUY_ACTIONS`.

### 3.3 GraduationEvent (best-effort mapping)

| Field | Source |
|---|---|
| `mint`, `pair_address` | candidate (pair = mint fallback) |
| `detected_at_ts`, `pair_age_seconds` | now / candidate.age_seconds |
| `bonding_curve_fill_seconds` | `LaunchMemory` (0.0 = launch not observed) |
| `bonding_curve_sol_raised` | `candidate.bonding_curve_sol` |
| `bonding_curve_premium_pct` | 0.0 (not derivable yet) |
| `bonding_curve_unique_buyers` | 0 (feed not available — gate relaxed) |
| `initial_liquidity_sol` | `candidate.liquidity_usd`/SOL price unavailable → 0.0, gate relaxed |
| `lp_burned` | not populated by enrichers today → `require_lp_burned=False` initially |

`GraduationConfig` shipped defaults would reject everything on the missing
fields, so the bot passes a relaxed config (`min_unique_buyers=0`,
`require_lp_burned=False`, `min_initial_liquidity_sol=0`,
`max_pair_age_seconds=SNIPER-scale value`) and relies on the fields we DO
have (fill_seconds, sol_raised, liquidity/top10/bundler via TokenInput).
Tightening these gates is a config change once the data exists (M10c wallet
feeds / enricher work).

## 4. Data flow notes

- **Cooldown / blocked-source / require-sources policies stay upstream of
  routes** — they live in ExecutionSink + RiskManager, which all routes
  share; a sniper buy of a mint in cooldown is rejected exactly like a
  scanner buy.
- Sniper rule-mode emits `action="buy"` — the operator must include `buy`
  in `RISK_BUY_ACTIONS` for the sniper route to trade (documented; the
  routing section of `.env.example` says so explicitly).
- Telegram OPENED/CLOSED notifications gain a `Route: <name>` line via
  `decision.meta` (already rendered through `build_trade_meta`'s Decision
  section? no — meta is not printed today; add one line to the Decision
  block).

## 5. Testing plan

- Unit: rule matching (first-match, age boundary, fallback), LaunchMemory
  (record/fill_seconds/TTL expiry), GraduationEvent mapping (fill from
  memory vs unknown), RiskManager route multiplier + floor, RoutedPipeline
  routes to the right stub pipeline and stamps meta.
- `scripts/m10b_smoke.py` — offline: three synthetic candidates (fresh
  pumpfun launch / migration / trending) flow through RoutedPipeline with
  stub agents; assert route labels + graduation event fields.
- Full suite must stay green with `ROUTING_ENABLED=false` (default) — no
  behavioural diff.

## 6. Definition of Done

- [ ] `ROUTING_ENABLED=false` → suite + smokes bit-for-bit green (merge-safe).
- [ ] With routing on (locally): pumpfun_ws candidate age≤threshold routes
      to sniper (no LLM call), pumpfun_migration routes to graduation with
      a non-trivial `GraduationEvent`, others route to scanner.
- [ ] Per-route sizing verified: sniper buy sized at 0.5× scanner-equivalent.
- [ ] Draft PR green on CI; branch rebases cleanly on post-dry-run `main`.

## 7. Sub-phases

- **B1** — `routing/` package (LaunchMemory, RoutedPipeline, GraduationPipeline) + unit tests.
- **B2** — Settings + RiskConfig route knobs + `__main__` wiring + notif route label + `.env.example`.
- **B3** — `m10b_smoke.py`, full-suite regression, draft PR. (Release/versioning happens at merge time, after window-2 data fills the routing table.)

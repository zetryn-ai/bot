# Roadmap

The bot template ships in milestones (`M1`–`M9`). Each milestone has a
design document at [`docs/plans/`](docs/plans/) and bumps the version
appropriately on landing.

> **Position relative to `zetryn-trading`:** the framework decides; this
> bot executes. Roadmap below tracks the *executes* side — scanners,
> orchestration, swap, wallet, persistence, deploy, dashboard. Decision
> logic lives in [`zetryn-trading`](https://github.com/zetryn-ai/ai-agent),
> not here.

## Milestones

| ID | Title | Status | Version | Design |
|---|---|---|---|---|
| **M1** | Scanner refactor & baseline | ✅ shipped | v0.1.0 | [2026-06-28-m1-scanner-refactor.md](docs/plans/2026-06-28-m1-scanner-refactor.md) |
| **M2** | Wire scanners to `zetryn-trading` | ✅ shipped | v0.2.0 | [2026-06-28-m2-wire-zetryn-trading.md](docs/plans/2026-06-28-m2-wire-zetryn-trading.md) |
| **M3** | Orchestration runtime (`python -m zetryn_bot`) | ✅ shipped | v0.3.0 | [2026-07-01-m3-orchestration-runtime.md](docs/plans/2026-07-01-m3-orchestration-runtime.md) |
| **M4** | Execution layer (paper-trading: swap engine, position, PnL) | ✅ shipped | v0.4.0 | [2026-07-04-m4-execution-layer.md](docs/plans/2026-07-04-m4-execution-layer.md) |
| **M5** | Wallet management (encrypted keypair, LiveExecutor) | ✅ shipped | v0.5.0 | [2026-07-04-m5-wallet-management.md](docs/plans/2026-07-04-m5-wallet-management.md) |
| **M6** | Persistence (PostgreSQL — DecisionLog, position state) | ✅ shipped | v0.6.0 | [2026-07-05-m6-persistence.md](docs/plans/2026-07-05-m6-persistence.md) |
| **M7** | Observability (Telegram notifier, heartbeat, crash dump) | ✅ shipped | v0.7.0 | [2026-07-05-m7-observability.md](docs/plans/2026-07-05-m7-observability.md) |
| **M8** | Deployment (Dockerfile, compose, systemd, deploy docs) | 📅 planned | v0.8.0 | TBD |
| **M9** | API + Dashboard (FastAPI + Next.js) | 📅 planned | v0.9.0 (or v1.0.0 cut) | TBD |
| **M10** | Specialized strategy routing (per-signal agents: sniper / graduation / KOL copy-trade / confluence, incl. `PositionContext`) | 📅 planned | v0.10.0 | TBD |

**On M10:** M3 wires a single general agent (`build_scanner`). Routing each
signal type to its specialized `zetryn-trading` agent (`build_sniper`,
`build_graduation`, `build_kol_copytrade`, …) is deferred to its own milestone.
It needs `PositionContext` / `GraduationContext` etc., which only become
meaningful once execution (M4) and persistence (M6) exist — so M10 sits
**after M6**. `BotPipeline` is already agent-agnostic, so M10 is "several
pipelines + a dispatcher," not a rewrite of M3.

A `v1.0.0` stable release is cut once **M2** ships at minimum (the first
end-to-end zetryn-trading-integrated bot). The exact cutoff between
`v0.x` and `v1.0.0` is decided once we have real-source testing data —
versioning before that follows additive minor bumps per milestone.

## Versioning convention

Mirrors `zetryn-trading`:

| Change type | Version bump |
|---|---|
| Bug fix, no API change | `v1.0.X` (patch) |
| Additive feature, no breaking change | `v1.X.0` (minor) |
| Breaking change to public API | `vX.0.0` (major) |

For pre-v1.0.0 milestones, breaking changes are allowed without a major
bump — minor bumps signal substantial milestone landings.

## Decision dependencies (high level)

```
M1 (refactor + Protocol)
   │
   └─→ M2 (wire to zetryn-trading)
          │
          └─→ M3 (orchestration runtime)
                 │
                 ├─→ M4 (execution)  ──┐
                 ├─→ M6 (persistence) ─┼─→ M9 (API + Dashboard)
                 └─→ M7 (observability)│
                                       │
                       M5 (wallet) ────┘
                       (depends on M4)
                       
                                       M8 (deploy) — needs M4

   M4 + M6 ─→ M10 (specialized strategy routing — needs PositionContext)
```

M5 (wallet) only meaningful after M4 (execution) — wallet exists to sign
transactions. M8 (deploy) needs M4 at minimum (something worth deploying).
M9 (dashboard) needs M3 + M6 (something to display + state to query).

## Design doc convention

Every milestone gets one design document in
[`docs/plans/YYYY-MM-DD-mN-<slug>.md`](docs/plans/) with a `**Status:**`
header (`Draft` / `Approved` / `Shipped (vX.Y.Z)` / `Historical`).

Index of plans lives in [`docs/plans/README.md`](docs/plans/README.md).

For non-milestone changes (bug fixes, doc tweaks, dependency bumps), use
the usual git history — no design doc needed.

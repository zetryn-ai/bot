# M10c — Event-driven entry agents (KOL copy-trade first)

**Date:** 2026-07-12
**Status:** Approved
**Target version:** v0.12.0

M10b routes candidates to specialist agents, but every signal is still
*market-shaped* (launches, migrations, trending). The framework has shipped
event-driven agents since v0.16.0 — KOL copy-trade (S1), smart-money
confluence, dip-buy, growth detector — that need *wallet-shaped* events the
bot does not produce yet. M10c adds that event feed and wires the highest
evidence-tier agent (S1 KOL copy-trade per `docs/STRATEGIES.md`) into the
M10b router.

Context for priorities (2026-07-12 audit): scanner-route trades bleed (29%
WR buying trending laggards); the sniper now trades via the curve adapter
(v0.11.0) but is rule-thin. Copy-trade attacks the core problem — entry
QUALITY — with the strongest external signal available: wallets with a
proven track record buying before we would ever see the token trend.

## 1. Decisions to lock (brainstorm)

| # | Topic | Recommendation |
|---|---|---|
| 1 | First agent | **KOL copy-trade (S1)** — highest evidence tier, smallest infra (wallet feed only). Confluence/dip-buy/growth follow once the feed + per-token time series exist. |
| 2 | Wallet event feed | **Helius Enhanced Webhooks** (free tier, address-activity → HTTPS POST to a new bot endpoint). Yellowstone gRPC is the live-scaling upgrade later — different milestone. |
| 3 | KOL wallet list | **Auto-discovery via GMGN top-trader data** (USER DECISION 2026-07-12 — not manual curation): periodically pull GMGN's top-trader/smart-money rankings, qualify by 30-day win rate ≥ 60%, ≥ 50 trades, not clustered/bundled; refresh the Helius webhook address set on change. GMGN endpoints MUST be re-verified live before implementation (key-pair signing; spec drifts). Manual `KOL_WALLETS` env stays as an additive override. |
| 4 | Sizing/risk | Same GLOBAL RiskManager; new route `kol` with its own size multiplier + conf floor (config, like M10b). |
| 5 | Live execution hardening (Jito bundles, priority fees, PumpPortal live curve trades) | Separate milestone **M11**, developed **IN PARALLEL** with M10c on its own branch (USER DECISION 2026-07-12 — same pattern as M9/M10b), both due before the 31 Jul go-live. |

## 2. Architecture sketch

```
Helius webhook (address activity, curated KOL set)
        │ HTTPS POST /webhooks/helius (new, token-guarded, runs in bot API)
        ▼
KolEventScanner (Scanner protocol — queue-backed like TelegramScanner)
        │ TokenCandidate(sources=["kol_buy"], kol wallet + size in fields TBD)
        ▼
RoutedPipeline rule: primary_source == "kol_buy" → framework build_kol_copy_trade()
        ▼ decision.meta["route"]="kol" → shared TeeSink → GLOBAL RiskManager
```

Bot-side new pieces: webhook receiver (FastAPI route in the existing api
app or a tiny listener in the bot container — decide at implementation),
`KolEventScanner`, `KOL_WALLETS` config + qualification doc, route wiring,
`kol` route policy knobs. Framework: `strategies.agents.kol` (exists).

## 3. Non-goals

- Auto wallet discovery (follow-up once the feed is proven).
- Confluence / dip-buy / growth agents (need per-token time-series store —
  design in this doc's follow-up revision).
- Live execution path changes (M11).

## 4. Definition of Done (draft)

- A curated KOL buy event reaches the kol agent within seconds of the
  on-chain transaction and produces a routed decision visible in the live
  feed (route badge `kol`).
- Webhook outage degrades silently to zero kol candidates (no crash,
  supervised).
- Suite green with the feature disabled (default off), like M10b.

# M9 — API + Dashboard (read-only)

**Date:** 2026-07-12
**Status:** Approved
**Target version:** v0.10.0

The bot runs 24/7 on the VPS with real trades accumulating in Postgres, but
the only windows into it are Telegram pushes and `docker logs`. M9 adds a
web dashboard: open positions, trade history + PnL analytics, a live view of
the AI's decisions, and runtime status — strictly read-only.

Developed on `feat/m9-dashboard` (draft PR) in parallel with the dry run and
M10b; merge order is M10b first, then M9 rebases.

## 1. Goals

1. Monitor from any browser (phone included): open positions, today's PnL,
   circuit-breaker state, trade history, win-rate breakdowns, equity curve.
2. **Live AI activity** — a realtime table of every token that REACHED the
   AI analyst (hard-gate rejects excluded): its scores, reasoning, and how
   far it got (AI verdict → which risk gate stopped it → or position opened).
3. Zero write paths from the web to the bot (read-only; control = M9.1).
4. One image, one deploy command — the dashboard is a second container of
   the SAME `zetryn-bot` image with a different CMD.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Architecture | **FastAPI + static SPA in one container.** FastAPI serves `/api/*` and the built React (Vite) bundle. No Node runtime on the VPS — the SPA builds in a Docker stage. Runs as compose service `dashboard` using the same `zetryn-bot` image (`uvicorn zetryn_bot.api.app:app`). |
| 2 | Live updates | **Polling 5–10s** from the SPA. WebSocket deferred to M9.1. |
| 3 | Access | **nginx host reverse-proxy + subdomain + certbot TLS + token login** — follows the VPS's existing `*.lemacore.com` pattern (erp/futures/memescan). Default vhost `zetryn.lemacore.com` (rename at deploy). Container port binds to `127.0.0.1:8140` only; auth = `Authorization: Bearer <DASHBOARD_TOKEN>` from `.env`, token entered once on a simple login screen and kept in localStorage. |
| 4 | Control | **Read-only.** No start/stop/pause from the web in M9 — important given live mode later. Pause-buying / kill-switch = M9.1 after the access pattern is proven. |
| 5 | AI activity source (user addition) | Decisions currently exist only as log lines. New **`ai_decisions` table** written by the BOT (an `AiActivitySink` on the existing TeeSink) for candidates whose `decision.analysis` is not None — i.e. exactly the tokens that reached the LLM. The row also records the post-AI outcome ("stopped where"). Retention-pruned (default 14 days). |

## 3. Architecture

```
Browser ── https://zetryn.lemacore.com ── nginx (host, certbot) ── 127.0.0.1:8140
                                                                        │
                                              container "dashboard" (image zetryn-bot)
                                              uvicorn zetryn_bot.api.app:app
                                              ├─ /api/*  (Bearer DASHBOARD_TOKEN)
                                              └─ /       (static SPA build)
                                                       │  SELECT-only
                                              postgres-16 (odoo-lema_odoo-net)
                                                       ▲
                                              container "bot" ── AiActivitySink (BARU)
                                                                 menulis ai_decisions
```

### 3.1 `ai_decisions` table + sink (bot side)

```python
class AiDecisionModel(Base):
    __tablename__ = "ai_decisions"
    id: int (pk)
    ts: datetime (indexed)
    mint, symbol, primary_source, route: str
    action: str            # skip | watch | alert | buy | abort
    confidence: Numeric
    final_score: Numeric   # raw analyst score (pre-calibration)
    scores: JSONB          # safety/market/wallets/social/final
    reasoning: Text        # full analyst reasoning
    reasons: JSONB         # decision.reasons list (incl. guardrail msgs)
    outcome: str           # ai_skip | not_buy_action | already_held | cooldown |
                           # risk_rejected | buy_failed | opened
    outcome_detail: str    # e.g. risk gate name ("source dexscreener_boost is buy-blocked")
```

- `AiActivitySink.emit()` inserts only when `decision.analysis is not None`
  (sniper rule-mode and hard-gate rejects never reach it — per the user's
  requirement this table is exclusively "token yang masuk ke AI").
- **Outcome tracking**: `RiskManager` gains `last_reject_reason` exposure via
  a new `evaluate_ex()` returning `(request, reject_reason)`; `ExecutionSink`
  uses it and reports the outcome (opened / cooldown / already_held /
  risk_rejected+reason / buy_failed) to the `AiActivitySink` row via a
  callback keyed on (mint, decision id). Failure to write the row NEVER
  affects trading (log + continue — same fallback pattern as M6).
- Retention: delete rows older than `AI_ACTIVITY_RETENTION_DAYS` (14) —
  pruned opportunistically at bot startup + daily.
- Alembic migration `0002_ai_decisions`.

### 3.2 API endpoints (`zetryn_bot/api/`, all GET, Bearer-token guarded)

| Endpoint | Returns |
|---|---|
| `/api/overview` | open positions (from `positions`), today's realized PnL (`risk_state`), open/closed counts, breaker status |
| `/api/ai-activity?limit=100` | newest `ai_decisions` rows — the live AI table |
| `/api/trades?limit&offset&reason&source&since` | closed trades, paginated |
| `/api/stats` | win-rate & PnL grouped by source / route / confidence band / day |
| `/api/equity` | cumulative-PnL time series from `closed_trades` |
| `/api/status` | DB reachable, newest ai_decision + trade timestamps, bot version |
| `/api/auth/check` | 200/401 — login screen validation |

Read-only enforcement: the API layer contains no INSERT/UPDATE/DELETE and
runs SELECT-only queries; (optional hardening later: dedicated Postgres
read-only role).

### 3.3 SPA (`dashboard/` Vite + React, built into the image)

- **Overview** — summary cards (PnL today, open count, win rate, breaker) +
  open-positions table + **Live AI Activity table**: time, token, source,
  route, scores (badge per dimensi), confidence, AI reasoning (expandable),
  and a funnel/outcome column: `AI: skip` → `⏸ cooldown` → `⛔ risk: <gate>`
  → `🔵 OPENED`. Polls every 5s.
- **Trades** — history with filters (reason/source/date) + pagination.
- **Analytics** — equity curve, win-rate by source/route/conf-band (bar
  charts), daily PnL.
- **Status** — DB/bot liveness, versions, config snapshot (non-secret).
- Simple login screen storing the token; all fetches send the Bearer header.

### 3.4 Deploy (B3, executed after the dry run finishes)

- `Dockerfile`: add a `node:20-slim` build stage for `dashboard/` → copy
  `dist/` into the runtime image; `pip install .[api]` (new optional extra:
  `fastapi`, `uvicorn`).
- `docker-compose.vps.yml`: new `dashboard` service — same image,
  `command: uvicorn zetryn_bot.api.app:app --host 0.0.0.0 --port 8140`,
  `ports: ["127.0.0.1:8140:8140"]`, same env_file + network.
- nginx vhost `zetryn.lemacore.com` → `proxy_pass http://127.0.0.1:8140` +
  certbot cert (same recipe as the three existing vhosts).
- New `.env` keys: `DASHBOARD_TOKEN` (required for the API to start),
  `AI_ACTIVITY_RETENTION_DAYS=14`.

## 4. Testing plan

- Unit/integration (existing `DATABASE_URL_TEST` fixture): `AiActivitySink`
  writes only analysis-bearing decisions; outcome transitions recorded;
  retention prune; each endpoint's shape + auth (401 without/with-wrong
  token); stats aggregation against seeded trades.
- SPA: build-level check in CI (`npm ci && npm run build` in the Docker
  build); component logic kept thin (fetch + render).
- DoD verification on VPS with REAL dry-run data.

## 5. Definition of Done

- [ ] Dashboard reachable at the vhost with TLS; wrong/missing token → 401.
- [ ] Live AI Activity shows real analyst decisions within one poll interval
      of them happening, including reasoning and the stop-stage.
- [ ] Trades/Analytics reproduce numbers already verified by SQL during the
      window-1 analysis (spot-check equality).
- [ ] Bot container untouched: no behaviour/log diff with the dashboard up
      or down; killing the dashboard container does not affect trading.
- [ ] CI builds the image including the SPA stage.

## 6. Sub-phases

- **B1a** — `ai_decisions` model + migration + `AiActivitySink` + outcome
  tracking (`evaluate_ex`) + tests. (Bot-side write path.)
- **B1b** — `zetryn_bot/api/` FastAPI app + all endpoints + auth + tests.
- **B2** — `dashboard/` SPA (4 pages, polling, login).
- **B3** — Docker stage + compose service + nginx/certbot deploy + `.env`
  keys + README section + release (merge after M10b lands; v0.10.0).
  **Execution scheduled AFTER the dry-run window completes (3×24h, per user
  2026-07-12)** — same milestone-merge sequence: window-2 analysis → fixes
  to main → M10b merge → M9 rebase → B3 deploy.

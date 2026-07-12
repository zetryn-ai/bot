# API Key Requirements — 7×24 Operation

**Living reference** (requested 2026-07-12). How many keys each provider
needs for the bot to run uninterrupted for a week, based on **measured
demand from VPS logs** (2026-07-12, routing enabled, dust pre-filter ON)
and **rate limits verified against vendor docs on the date noted**. Re-verify
limits before trusting this file after a few weeks — vendors drift (see
CLAUDE.md "External APIs" rule).

## Measured demand (steady state, 2026-07-12)

- Pipeline throughput: ~15–25 candidates/min → **~25–35k candidates/day**
  (pre-filter `PUMPFUN_MIN_CURVE_SOL=2.0` cuts the pump.fun firehose ~80%).
- Every candidate that reaches the pipeline costs ≈ 1 Helius + 1 GMGN +
  1 RugCheck call (+ Twitter when a symbol match exists, + Jupiter on demand).
- LLM analyst calls (scanner + graduation routes only): ~1–3/min →
  **~2–4k calls/day**.
- Position sweeps: 1 Jupiter quote per open position per 30 s.

## Scanners

| Provider | Env var | Limit (verified) | Demand | Keys for 7×24 | On VPS now |
|---|---|---|---|---|---|
| DexScreener | none | 60 rpm (profiles/boosts), 300 rpm (pairs) — per IP, 2026-07-12 | 3 pollers, ≤6 req/min | **0 (keyless)** | — |
| GeckoTerminal | none | 30 rpm per IP (2026-07-12) | 2 pollers @30/120 s ≈ 2.5 req/min | **0 (keyless)** | — |
| Birdeye | `BIRDEYE_API_KEYS` | **30k CU/MONTH/key** (2026-07-12); trending=40 CU, new_listing=30 CU per call | @1800 s cadence: ~3.4k CU/day ≈ **101k CU/month** | **≥4 keys**; 6–7 comfortable (burst + retry headroom) | 7 ✅ |
| PumpPortal (pump.fun WS) | `PUMPPORTAL_API_KEY` | push WS, no documented request limit | 1 socket | **1** | 1 ✅ |
| Raydium | none | public API, no key | 1 poller | **0** | — |
| Telegram (telethon) | `TELEGRAM_API_ID/HASH` + session | account-level flood limits | passive listener | **1 account** | 1 ✅ |

## Enrichers

| Provider | Env var | Limit (verified) | Demand | Keys for 7×24 | On VPS now |
|---|---|---|---|---|---|
| Helius | `HELIUS_API_KEYS` | Free: **1M credits/mo, 10 RPS RPC, 2 RPS Enhanced/DAS** (2026-07-12) | ~30k calls/day ≈ 900k+/mo, bursts >2 RPS | **≥2, rekomendasi 3** (the 2 RPS Enhanced cap is the real constraint, not credits) | 4 ✅ |
| RugCheck | none (public) | rate-limited, number **not published** (2026-07-12; unlimited via 1k $FLUXB per vendor X post) | ~20k/day | **0 keys**, but coverage is the risk, not quota — fails open; buys stay gated by `RISK_REQUIRE_SOURCES` (scanner/graduation) | — |
| GMGN OpenAPI | key-pair signing | limits not published (2026-07-12) | ~30k/day | **1 pair**, monitor 429s in logs | 1 ✅ |
| Jupiter (quotes) | none (lite tier) | **60 rpm per IP** (2026-07-12) | sweeps: 2/min per open position (max 5 pos = 10/min) + buys | **0 (keyless)**; paid tier only if live scaling needs it | — |
| Twitter/X | cookie session | account-level | ~50–100 lookups/day | **1 account** (2 if suspended-account risk matters) | 1 ✅ |

## LLM (failover chain, framework `LLMRouter`)

Per-key-per-model buckets; the chain fails over model→provider, so capacity
adds up across all 9 entries. Groq numbers from the framework limits table
(verified 2026-07-12); OpenRouter verified 2026-07-12.

| Provider | Env var | Free limit/key (verified) | Chain entries | Keys for 7×24 @4k calls/day | On VPS now |
|---|---|---|---|---|---|
| Groq | `GROQ_API_KEY` (CSV) | per-model RPD buckets: 70b ≈1k RPD, scout 30k TPM, gpt-oss-120b, 8b ≈14.4k RPD | 4 buckets | **≥5**; 17 = very comfortable | 17 ✅ |
| Cerebras | `CEREBRAS_API_KEY` | daily token quota per key (gpt-oss-120b) | 1 | 2 OK | 2 ✅ |
| SambaNova | `SAMBANOVA_API_KEY` | RPM-capped free tier | 1 | 1–2 | 1 ✅ |
| OpenRouter | `OPENROUTER_API_KEY` | **:free models: 20 RPM; 50 req/DAY** (1k/day only after $10 lifetime top-up) (2026-07-12) | 1 | 5 keys = 250 req/day — backup slot only, jangan andalkan | 5 ✅ |
| Gemini | `GEMINI_API_KEY` | small free RPD (flash/flash-lite) | 2 | last-resort slots; 5 OK | 5 ✅ |

**Bottom line (LLM)**: current pool ≈ 10–20× the measured 4k calls/day —
no additional keys needed for 7×24 paper. Re-check before live if the
candidate volume is deliberately raised.

## Ranked shopping list if anything runs dry

1. **Birdeye** — the only provider we have actually exhausted (CU is
   monthly; a dead key stays dead until the month resets). Cheapest fix:
   more free keys.
2. **Helius** — 2 RPS Enhanced/DAS per key is the tightest per-second cap
   in the enrichment path; a third key removes it as a bottleneck.
3. **Groq** — nothing needed now; add keys before intentionally scaling
   candidate volume for live.

## Operational notes

- Key pools rotate automatically (`utils/key_pool.py` for scanners/enrichers,
  framework `KeyPool` for LLM); a 429 cools one key, the pool moves on —
  adding keys to the CSV env vars is the whole procedure (restart required).
- Enrichers **fail open** (candidate flows on without that data);
  `RISK_REQUIRE_SOURCES=rugcheck` is the buy-side backstop on scanner/
  graduation routes; the sniper route is exempt (its agent gates safety).
- Watch for: `rate limited — cooldown` (key pool), `compute units exhausted`
  (Birdeye), `LLM unavailable` (chain exhausted — should never happen with
  the current pool).

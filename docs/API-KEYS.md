# API Key Requirements — 7×24 Operation

**Living reference.** Every number below carries its provenance:
- **[docs 2026-07-12]** — verified against the vendor's official docs/pricing
  page on that date.
- **[measured 2026-07-12]** — measured on the production VPS (v0.10.3,
  routing + dust pre-filter ON, 10-minute steady-state window + 12h DB data).
- **[config]** — derived arithmetically from the deployed poll cadence
  (not a guess: the bot cannot call faster than its configured interval).

No number here is an assumption. If a cell can't be verified, it says
**not published** — treat that provider as "monitor 429s in logs".
Re-verify [docs] rows after a few weeks — vendors drift (CLAUDE.md rule).

## Measured demand (VPS, 2026-07-12 04:25–04:35 UTC)

- **229 decisions / 10 min ≈ 23/min ≈ 33k candidates/day** — this is the
  enrichment demand: every candidate costs ≈ 1 Helius + 1 GMGN + 1 RugCheck
  call. Breakdown/10 min: gecko_new 96, raydium 71, dexscreener 20,
  gecko_trending 18, pumpfun_ws 13, boost 10, migration 1.
- Dust pre-filter effect: pumpfun_ws fell 155 → 13 per 10 min (−92%).
- **Twitter enrich: 11 / 10 min ≈ 1.6k/day** (only when a symbol matches).
- **LLM analyst calls: 74–137/hour ≈ 1.8–3.3k/day** (ai_decisions rows,
  12h of DB data; sniper is rule-only and calls no LLM).
- Jupiter: 2 quotes/min per open position (30 s sweep) + 1–2 per buy
  attempt [config].

## Scanners

| Provider | Env var | Limit | Demand [config] | Keys for 7×24 | On VPS |
|---|---|---|---|---|---|
| DexScreener | none | 60 rpm profiles/boosts, 300 rpm pairs, per IP [docs 2026-07-12] | 3 pollers @10/30/20 s = 11 req/min | **0 (keyless)** | — |
| GeckoTerminal | none | 30 rpm per IP [docs 2026-07-12] | 2 pollers @30/120 s = 2.5 req/min | **0 (keyless)** | — |
| Birdeye | `BIRDEYE_API_KEYS` | **30k CU/MONTH/key**; trending 40 CU, new_listing 30 CU per call [docs 2026-07-12] | @1800 s: (48×40)+(48×30) = 3 360 CU/day = **100.8k CU/month** | **≥4** (4×30k=120k); 7 = 210k, margin 2× | 7 ✅ |
| PumpPortal WS | `PUMPPORTAL_API_KEY` | push WS; request limit **not published** | 1 socket | **1** | 1 ✅ |
| Raydium | none | public, limit **not published** | 1 poller @15 s = 4 req/min | **0** | — |
| Telegram | `TELEGRAM_API_ID/HASH` + session | account flood limits (Telegram-internal) | passive listener | **1 account** | 1 ✅ |

## Enrichers

| Provider | Env var | Limit | Demand [measured] | Keys for 7×24 | On VPS |
|---|---|---|---|---|---|
| Helius | `HELIUS_API_KEYS` | Free: **1M credits/mo, 10 RPS RPC, 2 RPS Enhanced/DAS** per account [docs 2026-07-12] | 33k calls/day ≈ **990k/mo** — right at one key's monthly ceiling; 4 workers can burst to 4 concurrent calls > 2 RPS | **≥2 dari akun terpisah** (credits), **3–4** to clear the 2 RPS cap | 4 ✅ |
| RugCheck | none (public) | rate-limited; number **not published** [docs checked 2026-07-12 — swagger has no figure] | 33k calls/day attempted; coverage observed 29–43% on fresh mints (indexing lag, not quota) | **0 keys** — fails open; buys gated by `RISK_REQUIRE_SOURCES` (scanner/graduation) | — |
| GMGN OpenAPI | key pair | **not published** | 33k calls/day | **1 pair**; monitor 429 in logs | 1 ✅ |
| Jupiter quotes | none (lite) | **60 rpm per IP** [docs 2026-07-12] | max-positions 5 × 2/min = 10/min + buys | **0 (keyless)** | — |
| Twitter/X | cookie session | account-internal | 1.6k lookups/day | **1 account** | 1 ✅ |

## LLM (failover chain — framework `LLMRouter`)

Demand: **1.8–3.3k calls/day [measured]**. Capacity below is per
ACCOUNT/ORG/PROJECT — **multiple keys from the SAME account do NOT multiply
quota** (Groq: org-level; Gemini: per project; OpenRouter: per account —
all [docs 2026-07-12]). Our pools assume keys from separate accounts.

| Provider | Free limit | Chain entries | Capacity check vs 3.3k/day | On VPS |
|---|---|---|---|---|
| Groq | llama-3.3-70b: 30 RPM / 1k RPD / 12k TPM / 100k TPD; gpt-oss-120b: 30 RPM / 1k RPD / 8k TPM / 200k TPD; llama-3.1-8b: **14.4k RPD**; scout: 30k TPM — per org [docs 2026-07-12] | 4 buckets | 17 akun × (1k+1k+14.4k) RPD ≫ demand — primary bucket alone (17k RPD) covers 5× | 17 keys ✅ |
| Cerebras | gpt-oss-120b: **5 RPM, 30k TPM, 1M tokens/DAY** [docs 2026-07-12] | 1 | ~2M tokens/day (2 akun) ≈ 2–3k calls — full-day backup for the whole demand | 2 ✅ |
| SambaNova | Llama-3.3-70B: **10–30 RPM** free tier [docs 2026-07-12; exact RPD not published] | 1 | burst absorber, bukan volume | 1 ✅ |
| OpenRouter | :free — **20 RPM, 50 req/DAY**/account (1k/day hanya setelah top-up $10 lifetime) [docs 2026-07-12] | 1 | 5 akun × 50 = **250 req/day** — emergency slot ONLY | 5 ✅ |
| Gemini | 2.5-flash: **10 RPM / 250 RPD**; flash-lite: **15 RPM / 1k RPD** — per project [docs 2026-07-12] | 2 | 5 proyek × 1 250 = 6.2k RPD — last resort covers a full day | 5 ✅ |

**Verdict LLM [measured vs docs]**: primary groq bucket alone carries ~5×
today's demand; the whole chain carries >20×. No new keys needed for 7×24
paper at current volume. Re-run this math if candidate volume is scaled up
for live (the demand line above is the input).

## Ranked shopping list if anything runs dry

1. **Birdeye** — the only provider actually exhausted so far (CU is
   monthly: a dead key stays dead until month reset). More free keys =
   cheapest capacity.
2. **Helius** — 33k calls/day ≈ one key's full 1M/mo; keys #2–4 exist for
   the 2 RPS Enhanced cap and month-end headroom. Add a 5th only if
   candidate volume grows.
3. **Groq** — nothing needed now; add ACCOUNTS (not same-org keys) before
   deliberately scaling volume for live.

## Operational notes

- Key pools rotate automatically (`utils/key_pool.py` bot-side, framework
  `KeyPool` for LLM); a 429 cools one key, the pool moves on. Adding keys =
  append to the CSV env var + restart.
- Enrichers **fail open**; `RISK_REQUIRE_SOURCES=rugcheck` is the buy-side
  backstop on scanner/graduation; sniper is exempt
  (`RISK_REQUIRE_SOURCES_EXEMPT_ROUTES`).
- Alarm lines to watch: `rate limited — cooldown` (pool rotating),
  `compute units exhausted` (Birdeye monthly), `LLM unavailable`
  (chain exhausted — with the pool above this should never appear).

"""Scanner sources — fetch token signals from public DEX feeds.

Each scanner is a coroutine (poller or WebSocket stream) that pushes
normalised :class:`zetryn_bot.models.token.TokenCandidate` data onto a
Redis pub/sub channel for downstream consumers.

Orchestration (which scanners to enable, poll cadence, supervision) is
**not** wired here — the cdexio-era ``build_scanner_tasks`` belongs to a
specific orchestration shape that we'll redesign in Phase 2 around
``zetryn-trading`` agents. For now, import the individual scanners and
wire them yourself.

Available scanners:

- :mod:`.birdeye`       — BirdEye REST (trending / new listings, requires API key)
- :mod:`.dexscreener`   — DexScreener REST (new pairs / trending / boost)
- :mod:`.geckoterminal` — GeckoTerminal REST (new pools / trending)
- :mod:`.gmgn_openapi`  — GMGN OpenAPI (TLS-impersonated via curl-cffi)
- :mod:`.helius`        — Helius RPC + DAS API (token enrichment)
- :mod:`.jupiter`       — Jupiter price / quote REST
- :mod:`.pumpfun`       — Pump.fun WebSocket (new tokens + migration events)
- :mod:`.raydium`       — Raydium new-pool polling
- :mod:`.rugcheck`      — RugCheck safety scanner
- :mod:`.telegram`      — Telegram channel scraper (requires telethon session)
- :mod:`.twitter`       — Twitter scraper (requires twitter_login cookies)
"""

__all__: list[str] = []

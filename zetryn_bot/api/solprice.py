"""SOL/USD spot price for the dashboard's USD conversions.

Source: https://lite-api.jup.ag/price/v3 (verified live 2026-07-15,
    keyless, returns usdPrice for the wSOL mint). Cached 60s — the dashboard
    only needs a display-grade rate, not a trading oracle.
"""

from __future__ import annotations

import time

import aiohttp
from loguru import logger

_URL = "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112"
_WSOL = "So11111111111111111111111111111111111111112"
log = logger.bind(component="api.solprice")

_cache: dict[str, float] = {"price": 0.0, "ts": 0.0}


async def sol_usd() -> float:
    """Current SOL price in USD (60s cache; 0.0 when unavailable)."""
    now = time.monotonic()
    if _cache["price"] > 0 and now - _cache["ts"] < 60:
        return _cache["price"]
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(_URL, timeout=aiohttp.ClientTimeout(total=6)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                price = float(data.get(_WSOL, {}).get("usdPrice") or 0)
                if price > 0:
                    _cache["price"] = price
                    _cache["ts"] = now
    except Exception as exc:
        log.debug("SOL price fetch failed: {}", exc)
    return _cache["price"]

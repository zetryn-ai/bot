from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

JUPITER_PRICE_API = "https://lite-api.jup.ag/price/v3"
SOL_MINT = "So11111111111111111111111111111111111111112"

log = logger.bind(component="scanner.jupiter")


async def fetch_price(
    session: aiohttp.ClientSession, mint: str
) -> float | None:
    """Fetch token price in USD from Jupiter Price API."""
    try:
        async with session.get(
            JUPITER_PRICE_API,
            params={"ids": mint},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            # Price API v3: flat map keyed by mint, USD price under "usdPrice"
            price_data = data.get(mint, {})
            price = price_data.get("usdPrice")
            return float(price) if price else None
    except Exception as e:
        log.debug(f"Price fetch error for {mint}: {e}")
        return None


async def enrich_price(
    session: aiohttp.ClientSession,
    token: TokenCandidate,
) -> TokenCandidate:
    """Enrich TokenCandidate with latest price from Jupiter."""
    price = await fetch_price(session, token.address)
    if price is not None:
        token.price_usd = price
    if "jupiter" not in token.sources:
        token.sources.append("jupiter")
    return token

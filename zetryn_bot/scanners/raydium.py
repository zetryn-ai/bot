from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.storage.redis_client import publish_momentum

RAYDIUM_API = "https://api-v3.raydium.io"

log = logger.bind(component="scanner.raydium")


async def poll_raydium_new_pools(session: aiohttp.ClientSession, redis) -> None:
    """Fetch recently created Raydium pools (new liquidity events)."""
    url = f"{RAYDIUM_API}/pools/info/list"
    params = {
        "poolType": "all",
        "poolSortField": "default",
        "sortType": "desc",
        "pageSize": 50,
        "page": 1,
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"Raydium pools returned {resp.status}")
                return
            data = await resp.json()
            pools = data.get("data", {}).get("data", []) or []
            for pool in pools:
                token = _parse_raydium_pool(pool)
                if token:
                    await publish_momentum(redis, token.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"Raydium pool poll error: {e}")


async def fetch_pool_by_mint(
    session: aiohttp.ClientSession, mint: str
) -> dict | None:
    """Fetch specific pool data for a token mint address."""
    url = f"{RAYDIUM_API}/pools/info/mint"
    params = {"mint1": mint, "poolType": "all", "poolSortField": "liquidity", "sortType": "desc"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            pools = data.get("data", {}).get("data", []) or []
            return pools[0] if pools else None
    except Exception as e:
        log.debug(f"Raydium pool fetch error for {mint}: {e}")
        return None


def _parse_raydium_pool(pool: dict) -> TokenCandidate | None:
    """Parse Raydium pool response into TokenCandidate."""
    # Raydium pools have mintA and mintB — we want the non-SOL / non-USDC side
    sol_mint = "So11111111111111111111111111111111111111112"
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    mint_a = pool.get("mintA", {}) or {}
    mint_b = pool.get("mintB", {}) or {}

    base_mint = mint_a if mint_b.get("address") in (sol_mint, usdc_mint) else mint_b

    address = base_mint.get("address")
    if not address or address in (sol_mint, usdc_mint):
        return None

    day_data = pool.get("day", {}) or {}
    created_ts = pool.get("openTime")
    try:
        ts = int(created_ts) if created_ts else 0
        created_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else None
    except (ValueError, TypeError):
        created_at = None
    if created_at is None:
        return None  # no timestamp = established pool without creation record
    now = datetime.now(timezone.utc)
    age_seconds = int((now - created_at).total_seconds())
    if age_seconds > 86400:
        return None  # older than 24h — not a new pool target

    return TokenCandidate(
        address=address,
        symbol=base_mint.get("symbol", ""),
        name=base_mint.get("name", ""),
        created_at=created_at,
        sources=["raydium"],
        age_seconds=age_seconds,
        liquidity_usd=float(pool.get("tvl", 0) or 0),
        volume_1h_usd=float(day_data.get("volume", 0) or 0) / 24,
        price_usd=float(pool.get("price", 0) or 0),
    )

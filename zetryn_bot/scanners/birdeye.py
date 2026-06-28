from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.storage.redis_client import publish_sniper, publish_momentum
from zetryn_bot.utils.key_pool import BirdeyeKeyPool

BIRDEYE_BASE = "https://public-api.birdeye.so"

log = logger.bind(component="scanner.birdeye")

# Source label yang menandakan token dari Pump.fun ecosystem
_PUMP_SOURCES = {"pump_fun", "pumpswap"}


def _headers(api_key: str) -> dict:
    return {
        "X-API-KEY": api_key,
        "x-chain": "solana",
        "Accept": "application/json",
    }


async def poll_birdeye_trending(
    session: aiohttp.ClientSession,
    redis,
    key_pool: BirdeyeKeyPool,
) -> None:
    """
    Fetch trending Solana tokens from BirdEye sorted by 24h volume.
    Replaces GMGN trending — works from VPS without Cloudflare blocking.
    """
    key = await key_pool.acquire()
    if not key:
        log.error("BirdEye trending: no key available — skipping poll")
        return

    url = f"{BIRDEYE_BASE}/defi/tokenlist"
    params = {
        "sort_by": "v24hUSD",
        "sort_type": "desc",
        "offset": 0,
        "limit": 50,
        "min_liquidity": 5000,
    }
    try:
        async with session.get(
            url, headers=_headers(key), params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", 65))
                await key_pool.mark_rate_limited(key, retry_after)
                return
            if resp.status == 400:
                body = await resp.text()
                if "Compute units" in body:
                    await key_pool.mark_rate_limited(key, 86400)
                    log.warning("BirdEye trending: daily compute units exhausted — key marked for 24h")
                else:
                    log.error(f"BirdEye trending returned 400: {body[:120]}")
                return
            if resp.status != 200:
                log.error(f"BirdEye trending returned {resp.status}")
                return
            data = await resp.json()

        tokens = (data.get("data") or {}).get("tokens") or []
        published = 0
        for item in tokens:
            token = _parse_tokenlist_item(item)
            if token:
                await publish_momentum(redis, token.model_dump(mode="json"))
                published += 1

        if published:
            log.debug(f"BirdEye trending: published {published} tokens")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"BirdEye trending error: {e}")


async def poll_birdeye_new_listing(
    session: aiohttp.ClientSession,
    redis,
    key_pool: BirdeyeKeyPool,
) -> None:
    """
    Fetch newly listed Solana tokens from BirdEye.
    Primary: v2/tokens/new_listing. Fallback: tokenlist sorted by recentListingTime.
    """
    published = await _try_new_listing_v2(session, redis, key_pool)
    if published is None:
        await _try_new_listing_fallback(session, redis, key_pool)


async def _try_new_listing_v2(
    session: aiohttp.ClientSession,
    redis,
    key_pool: BirdeyeKeyPool,
) -> int | None:
    """Returns count published, or None if endpoint returned 400 (not available)."""
    key = await key_pool.acquire()
    if not key:
        return 0

    url = f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing"
    params = {"limit": 20, "offset": 0}
    try:
        async with session.get(
            url, headers=_headers(key), params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", 65))
                await key_pool.mark_rate_limited(key, retry_after)
                return 0
            if resp.status == 400:
                body = await resp.text()
                if "Compute units" in body:
                    await key_pool.mark_rate_limited(key, 86400)
                    log.warning("BirdEye new_listing: daily compute units exhausted — key marked for 24h")
                    return 0  # key exhausted — skip fallback too
                log.debug(f"BirdEye v2/new_listing 400 (tier restriction?): {body[:150]}")
                return None  # signal fallback
            if resp.status != 200:
                log.error(f"BirdEye new_listing returned {resp.status}")
                return 0
            data = await resp.json()

        items = (data.get("data") or {}).get("items") or []
        published = 0
        for item in items:
            token = _parse_new_listing_item(item)
            if token:
                await publish_sniper(redis, token.model_dump(mode="json"))
                published += 1

        if published:
            log.debug(f"BirdEye new_listing (v2): published {published} tokens")
        return published

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"BirdEye new_listing v2 error: {e}")
        return 0


async def _try_new_listing_fallback(
    session: aiohttp.ClientSession,
    redis,
    key_pool: BirdeyeKeyPool,
) -> None:
    """Fallback: tokenlist sorted by recentListingTime — available on Starter tier."""
    key = await key_pool.acquire()
    if not key:
        return

    url = f"{BIRDEYE_BASE}/defi/tokenlist"
    params = {
        "sort_by": "recentListingTime",
        "sort_type": "desc",
        "offset": 0,
        "limit": 20,
        "min_liquidity": 1000,
    }
    try:
        async with session.get(
            url, headers=_headers(key), params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", 65))
                await key_pool.mark_rate_limited(key, retry_after)
                return
            if resp.status == 400:
                body = await resp.text()
                if "Compute units" in body:
                    await key_pool.mark_rate_limited(key, 86400)
                    log.warning("BirdEye new_listing fallback: daily compute units exhausted — key marked for 24h")
                else:
                    log.error(f"BirdEye new_listing fallback returned 400: {body[:120]}")
                return
            if resp.status != 200:
                log.error(f"BirdEye new_listing fallback returned {resp.status}")
                return
            data = await resp.json()

        tokens_raw = (data.get("data") or {}).get("tokens") or []
        published = 0
        for item in tokens_raw:
            token = _parse_tokenlist_item(item)
            if token:
                token.sources = ["birdeye_new"]
                await publish_sniper(redis, token.model_dump(mode="json"))
                published += 1

        if published:
            log.debug(f"BirdEye new_listing (fallback tokenlist): published {published} tokens")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"BirdEye new_listing fallback error: {e}")


def _parse_tokenlist_item(item: dict) -> TokenCandidate | None:
    address = item.get("address")
    if not address:
        return None
    symbol = item.get("symbol", "")
    # Skip wrapped SOL, stablecoins
    if symbol.upper() in ("SOL", "WSOL", "USDC", "USDT", "USDE"):
        return None

    return TokenCandidate(
        address=address,
        symbol=symbol,
        name=item.get("name", ""),
        sources=["birdeye_trending"],
        liquidity_usd=float(item.get("liquidity") or 0),
        market_cap_usd=float(item.get("mc") or 0),
        price_usd=float(item.get("price") or 0),
        volume_1h_usd=float(item.get("v24hUSD") or 0) / 24,
    )


def _parse_new_listing_item(item: dict) -> TokenCandidate | None:
    address = item.get("address")
    if not address:
        return None

    source_raw = item.get("source", "")
    # Map BirdEye source names to our source labels
    if source_raw in _PUMP_SOURCES:
        source = "birdeye_new_pumpfun"
    else:
        source = "birdeye_new"

    # Parse listing time for age calculation
    added_raw = item.get("liquidityAddedAt")
    created_at = None
    age_seconds = 0
    if added_raw:
        try:
            created_at = datetime.fromisoformat(added_raw)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_seconds = int((datetime.now(tz=timezone.utc) - created_at).total_seconds())
        except ValueError:
            pass

    return TokenCandidate(
        address=address,
        symbol=item.get("symbol") or "",
        name=item.get("name") or "",
        sources=[source],
        created_at=created_at,
        age_seconds=max(0, age_seconds),
        liquidity_usd=float(item.get("liquidity") or 0),
    )

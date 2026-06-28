from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.storage.redis_client import publish_momentum

DEXSCREENER_BASE = "https://api.dexscreener.com"

log = logger.bind(component="scanner.dexscreener")


async def poll_dexscreener_new_pairs(session: aiohttp.ClientSession, redis) -> None:
    """Fetch latest token profiles (recently added to DexScreener)."""
    url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"DexScreener profiles returned {resp.status}")
                return
            items = await resp.json()
            for item in items:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item)
                if token:
                    await publish_momentum(redis, token.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"New pairs poll error: {e}")


async def poll_dexscreener_trending(session: aiohttp.ClientSession, redis) -> None:
    """Fetch top boosted tokens on Solana from DexScreener (cumulative boost leaders)."""
    url = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"DexScreener trending returned {resp.status}")
                return
            items = await resp.json()
            for item in items:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item)
                if token:
                    await publish_momentum(redis, token.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"Trending poll error: {e}")



async def poll_dexscreener_boost(session: aiohttp.ClientSession, redis) -> None:
    """Fetch most recent boost promotions on Solana from DexScreener."""
    url = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"DexScreener boost returned {resp.status}")
                return
            items = await resp.json()
            for item in items:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item, source="dexscreener_boost")
                if token:
                    await publish_momentum(redis, token.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"Boost poll error: {e}")


async def fetch_pair_by_address(session: aiohttp.ClientSession, address: str) -> dict | None:
    """Fetch full pair data for a specific token address."""
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{address}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            pairs = data.get("pairs") or []
            # Return the pair with highest liquidity
            if not pairs:
                return None
            return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    except Exception as e:
        log.warning(f"Pair fetch error for {address}: {e}")
        return None


def enrich_from_pair(token: TokenCandidate, pair: dict) -> TokenCandidate:
    """Enrich a TokenCandidate with full DexScreener pair data."""
    liquidity = pair.get("liquidity", {}) or {}
    volume = pair.get("volume", {}) or {}
    txns = pair.get("txns", {}) or {}
    price_change = pair.get("priceChange", {}) or {}

    token.liquidity_usd = float(liquidity.get("usd", 0) or 0)
    token.market_cap_usd = float(pair.get("marketCap", 0) or 0)
    token.price_usd = float(pair.get("priceUsd", 0) or 0)
    token.volume_5m_usd = float(volume.get("m5", 0) or 0)
    token.volume_1h_usd = float(volume.get("h1", 0) or 0)

    txns_5m = txns.get("m5", {}) or {}
    token.txns_5m = int(txns_5m.get("buys", 0) or 0) + int(txns_5m.get("sells", 0) or 0)
    token.buys_5m = int(txns_5m.get("buys", 0) or 0)
    token.sells_5m = int(txns_5m.get("sells", 0) or 0)

    pair_created = pair.get("pairCreatedAt")
    if pair_created:
        created_at = datetime.fromtimestamp(pair_created / 1000, tz=timezone.utc)
        token.created_at = created_at
        now = datetime.now(timezone.utc)
        token.age_seconds = int((now - created_at).total_seconds())

    if "dexscreener" not in token.sources:
        token.sources.append("dexscreener")
    return token


def _parse_profile(item: dict, source: str = "dexscreener") -> TokenCandidate | None:
    address = item.get("tokenAddress")
    if not address:
        return None
    # Boost API returns 'amount' (current boost) and 'totalAmount' (cumulative).
    # Boost = team/community paid for visibility — proxy for commitment.
    boost_amount = 0.0
    boost_total = 0.0
    try:
        boost_amount = float(item.get("amount") or 0)
        boost_total = float(item.get("totalAmount") or 0)
    except (TypeError, ValueError):
        pass
    return TokenCandidate(
        address=address,
        symbol=item.get("symbol", ""),
        name=item.get("description", ""),
        sources=[source],
        boost_amount=boost_amount,
        boost_total_amount=boost_total,
    )

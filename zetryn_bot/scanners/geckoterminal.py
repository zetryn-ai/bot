from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.storage.redis_client import publish_sniper, publish_momentum

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "solana"

log = logger.bind(component="scanner.geckoterminal")

_HEADERS = {
    "Accept": "application/json;version=20230302",
}


async def poll_geckoterminal_new_pools(
    session: aiohttp.ClientSession,
    redis,
) -> None:
    """Fetch newest Solana pools from GeckoTerminal — complementary to DexScreener."""
    url = f"{GECKO_BASE}/networks/{NETWORK}/new_pools"
    params = {"page": 1, "include": "base_token"}

    try:
        async with session.get(
            url, headers=_HEADERS, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                log.warning("GeckoTerminal rate limited — backing off 30s")
                await asyncio.sleep(30)
                return
            if resp.status != 200:
                log.warning(f"GeckoTerminal new_pools returned {resp.status}")
                return
            data = await resp.json()

        token_map = _build_token_map(data.get("included", []))
        published = 0
        for pool in data.get("data", []):
            token = _parse_pool(pool, token_map, source="geckoterminal_new")
            if token:
                await publish_sniper(redis, token.model_dump(mode="json"))
                published += 1

        if published:
            log.debug(f"GeckoTerminal new_pools: published {published} tokens")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"GeckoTerminal new_pools error: {e}")


async def poll_geckoterminal_trending(
    session: aiohttp.ClientSession,
    redis,
) -> None:
    """Fetch trending Solana pools from GeckoTerminal (web visits + on-chain activity)."""
    url = f"{GECKO_BASE}/networks/{NETWORK}/trending_pools"
    params = {"include": "base_token"}

    try:
        async with session.get(
            url, headers=_HEADERS, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                log.warning("GeckoTerminal rate limited — backing off 30s")
                await asyncio.sleep(30)
                return
            if resp.status != 200:
                log.warning(f"GeckoTerminal trending returned {resp.status}")
                return
            data = await resp.json()

        token_map = _build_token_map(data.get("included", []))
        published = skipped_old = 0
        for pool in data.get("data", []):
            token = _parse_pool(pool, token_map, source="geckoterminal_trending")
            if not token:
                continue
            if token.age_seconds > 86400:
                skipped_old += 1
                continue
            await publish_momentum(redis, token.model_dump(mode="json"))
            published += 1

        if published or skipped_old:
            log.debug(f"GeckoTerminal trending: published {published} tokens (skipped {skipped_old} older than 24h)")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"GeckoTerminal trending error: {e}")


def _build_token_map(included: list) -> dict[str, dict]:
    """Build id → token attributes map from the included array."""
    return {
        item["id"]: item.get("attributes", {})
        for item in included
        if item.get("type") == "token"
    }


def _parse_pool(pool: dict, token_map: dict, source: str) -> TokenCandidate | None:
    attrs = pool.get("attributes", {})
    rels = pool.get("relationships", {})

    # Extract base token mint address from relationship id: "solana_MINT_ADDRESS"
    base_token_rel = rels.get("base_token", {}).get("data", {})
    base_token_id = base_token_rel.get("id", "")
    if not base_token_id.startswith("solana_"):
        return None
    mint = base_token_id[len("solana_"):]
    if not mint:
        return None

    # Token metadata from included array
    tok_attrs = token_map.get(base_token_id, {})
    symbol = tok_attrs.get("symbol", "")
    name = tok_attrs.get("name", "")

    # Skip quote tokens (SOL, USDC, USDT) masquerading as base token
    if symbol.upper() in ("SOL", "WSOL", "USDC", "USDT"):
        return None

    # Timing
    created_raw = attrs.get("pool_created_at")
    created_at = None
    if created_raw:
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    age_seconds = 0
    if created_at:
        age_seconds = int((datetime.now(tz=timezone.utc) - created_at).total_seconds())

    # Metrics
    txns_m5   = attrs.get("transactions", {}).get("m5", {})
    buys_5m   = int(txns_m5.get("buys", 0) or 0)
    sells_5m  = int(txns_m5.get("sells", 0) or 0)
    vol_m5    = float(attrs.get("volume_usd", {}).get("m5", 0) or 0)
    liquidity = float(attrs.get("reserve_in_usd") or 0)
    mcap      = float(attrs.get("fdv_usd") or attrs.get("market_cap_usd") or 0)
    price     = float(attrs.get("base_token_price_usd") or 0)

    return TokenCandidate(
        address=mint,
        symbol=symbol,
        name=name,
        created_at=created_at,
        sources=[source],
        age_seconds=age_seconds,
        liquidity_usd=liquidity,
        market_cap_usd=mcap,
        price_usd=price,
        volume_5m_usd=vol_m5,
        txns_5m=buys_5m + sells_5m,
        buys_5m=buys_5m,
        sells_5m=sells_5m,
    )

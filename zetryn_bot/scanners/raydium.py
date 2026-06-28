"""Raydium — new-pool discovery polling.

Source: https://api-v3.raydium.io
Auth: None (public API)
Mechanism: REST polling every 15s for recently created pools.
Rate limits: Not formally published; conservative cadence used.
Emits: TokenCandidate via Scanner.stream(). Caller decides the sink.

Filters at the scanner level:
- Only base tokens that are NOT SOL or USDC are yielded.
- Pools older than 24 hours are skipped (this is a *new pools* scanner).

A module-level :func:`fetch_pool_by_mint` helper is preserved for callers
that want to look up a specific pool on demand (e.g. for cross-referencing
during decision time).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners._common import fetch_json, poll_loop

RAYDIUM_API = "https://api-v3.raydium.io"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class RaydiumNewPools:
    """Polling scanner for Raydium ``/pools/info/list`` — new pools."""

    name = "raydium.new_pools"

    def __init__(self, poll_interval_s: float = 15.0, page_size: int = 50) -> None:
        self._poll_interval_s = poll_interval_s
        self._page_size = page_size

    async def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        url = f"{RAYDIUM_API}/pools/info/list"
        params: dict[str, str | int] = {
            "poolType": "all",
            "poolSortField": "default",
            "sortType": "desc",
            "pageSize": self._page_size,
            "page": 1,
        }

        async def fetch() -> list[TokenCandidate]:
            data = await fetch_json(session, url, name=self.name, params=params)
            if not isinstance(data, dict):
                return []
            pools = data.get("data", {}).get("data", []) or []
            out: list[TokenCandidate] = []
            for pool in pools:
                token = _parse_raydium_pool(pool)
                if token:
                    out.append(token)
            return out

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


async def fetch_pool_by_mint(session: aiohttp.ClientSession, mint: str) -> dict | None:
    """Fetch the highest-liquidity Raydium pool for a given mint address."""
    log = logger.bind(component="raydium.fetch_pool")
    url = f"{RAYDIUM_API}/pools/info/mint"
    params: dict[str, str | int] = {
        "mint1": mint,
        "poolType": "all",
        "poolSortField": "liquidity",
        "sortType": "desc",
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            pools = data.get("data", {}).get("data", []) or []
            return pools[0] if pools else None
    except Exception as exc:
        log.debug(f"pool fetch error for {mint}: {exc}")
        return None


def _parse_raydium_pool(pool: dict) -> TokenCandidate | None:
    """Parse a Raydium pool dict into a :class:`TokenCandidate`.

    Returns ``None`` when:
    - the pool is SOL/USDC-only (no base memecoin),
    - the pool has no creation timestamp,
    - the pool is older than 24 hours.
    """
    mint_a = pool.get("mintA", {}) or {}
    mint_b = pool.get("mintB", {}) or {}

    # The "base" token is the side that is NOT SOL/USDC.
    base_mint = mint_a if mint_b.get("address") in (SOL_MINT, USDC_MINT) else mint_b

    address = base_mint.get("address")
    if not address or address in (SOL_MINT, USDC_MINT):
        return None

    day_data = pool.get("day", {}) or {}
    created_ts = pool.get("openTime")
    try:
        ts = int(created_ts) if created_ts else 0
        created_at = datetime.fromtimestamp(ts, tz=UTC) if ts > 0 else None
    except (ValueError, TypeError):
        created_at = None
    if created_at is None:
        # No timestamp = established pool with no creation record; skip.
        return None

    age_seconds = int((datetime.now(UTC) - created_at).total_seconds())
    if age_seconds > 86400:
        return None  # Older than 24h — not a new pool target.

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


__all__ = ["RaydiumNewPools", "fetch_pool_by_mint"]

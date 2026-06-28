"""GeckoTerminal — Solana new-pool and trending-pool polling.

Source: https://www.geckoterminal.com/dex-api
Auth: None (public API)
Mechanism: REST polling, two independent endpoints exposed as two
    Scanner classes (new pools / trending pools).
Rate limits: 30 RPM per IP (free tier). 429 triggers a 30s back-off.
Emits: TokenCandidate via Scanner.stream(). Caller decides the sink.

Two scanners:

- :class:`GeckoTerminalNewPools` — newest Solana pools. Default 15s
  interval. Complementary to DexScreener new pairs.
- :class:`GeckoTerminalTrending` — pools with high web visits and
  on-chain activity. Default 45s interval. Filters out pools older
  than 24 hours.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners._common import poll_loop

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "solana"

_HEADERS = {"Accept": "application/json;version=20230302"}


class GeckoTerminalNewPools:
    """Polling scanner for ``/networks/solana/new_pools``."""

    name = "geckoterminal.new_pools"

    def __init__(self, poll_interval_s: float = 15.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[TokenCandidate]:
        url = f"{GECKO_BASE}/networks/{NETWORK}/new_pools"
        params = {"page": 1, "include": "base_token"}

        async def fetch() -> list[TokenCandidate]:
            return await _fetch_pools(
                session, url, params, source="geckoterminal_new",
                max_age_seconds=None, name=self.name,
            )

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


class GeckoTerminalTrending:
    """Polling scanner for ``/networks/solana/trending_pools``."""

    name = "geckoterminal.trending"

    def __init__(self, poll_interval_s: float = 45.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[TokenCandidate]:
        url = f"{GECKO_BASE}/networks/{NETWORK}/trending_pools"
        params = {"include": "base_token"}

        async def fetch() -> list[TokenCandidate]:
            return await _fetch_pools(
                session, url, params, source="geckoterminal_trending",
                max_age_seconds=86400, name=self.name,
            )

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


async def _fetch_pools(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    *,
    source: str,
    max_age_seconds: int | None,
    name: str,
) -> list[TokenCandidate]:
    """Single fetch + parse. Handles GeckoTerminal's tight rate limits."""
    log = logger.bind(component=name)
    try:
        async with session.get(
            url, headers=_HEADERS, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                log.warning("rate limited — backing off 30s")
                await asyncio.sleep(30)
                return []
            if resp.status != 200:
                log.warning(f"status {resp.status}")
                return []
            data = await resp.json()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning(f"fetch error: {exc}")
        return []

    token_map = _build_token_map(data.get("included", []))
    out: list[TokenCandidate] = []
    for pool in data.get("data", []):
        token = _parse_pool(pool, token_map, source=source)
        if not token:
            continue
        if max_age_seconds is not None and token.age_seconds > max_age_seconds:
            continue
        out.append(token)
    return out


def _build_token_map(included: list) -> dict[str, dict]:
    """Build an ``id → attributes`` map from a GeckoTerminal ``included`` array."""
    return {
        item["id"]: item.get("attributes", {})
        for item in included
        if item.get("type") == "token"
    }


def _parse_pool(pool: dict, token_map: dict, *, source: str) -> TokenCandidate | None:
    attrs = pool.get("attributes", {})
    rels = pool.get("relationships", {})

    # Base token mint comes as "solana_<MINT>" in relationships.
    base_token_rel = rels.get("base_token", {}).get("data", {})
    base_token_id = base_token_rel.get("id", "")
    if not base_token_id.startswith("solana_"):
        return None
    mint = base_token_id[len("solana_"):]
    if not mint:
        return None

    tok_attrs = token_map.get(base_token_id, {})
    symbol = tok_attrs.get("symbol", "")
    name = tok_attrs.get("name", "")

    # Skip quote tokens (SOL, USDC, USDT) masquerading as base token.
    if symbol.upper() in ("SOL", "WSOL", "USDC", "USDT"):
        return None

    created_raw = attrs.get("pool_created_at")
    created_at: datetime | None = None
    if created_raw:
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    age_seconds = 0
    if created_at:
        age_seconds = int((datetime.now(tz=timezone.utc) - created_at).total_seconds())

    txns_m5 = attrs.get("transactions", {}).get("m5", {})
    buys_5m = int(txns_m5.get("buys", 0) or 0)
    sells_5m = int(txns_m5.get("sells", 0) or 0)
    vol_m5 = float(attrs.get("volume_usd", {}).get("m5", 0) or 0)
    liquidity = float(attrs.get("reserve_in_usd") or 0)
    mcap = float(attrs.get("fdv_usd") or attrs.get("market_cap_usd") or 0)
    price = float(attrs.get("base_token_price_usd") or 0)

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


__all__ = ["GeckoTerminalNewPools", "GeckoTerminalTrending"]

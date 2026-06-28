"""BirdEye — Solana trending and new-listing polling.

Source: https://docs.birdeye.so
Auth: BIRDEYE_API_KEYS (comma-separated; pool-rotated)
Mechanism: REST polling, two endpoints exposed as two Scanner classes.
Rate limits: Per-key RPM enforced by :class:`BirdeyeKeyPool`; HTTP 429
    triggers per-key cooldown. The Starter tier's daily compute-unit cap
    is detected from the 400 body and triggers a 24h key cooldown.
Emits: TokenCandidate via Scanner.stream(). Caller decides the sink.

Two scanners:

- :class:`BirdeyeTrending` — top tokens by 24h USD volume. Default 60s
  interval. Slower cadence because trending data updates slowly.
- :class:`BirdeyeNewListing` — newest listed tokens. Tries the v2
  endpoint first (Premium+ only), falls back to a tokenlist query
  sorted by ``recentListingTime`` (available on Starter). Default 45s.

Both scanners share a :class:`BirdeyeKeyPool` for rotation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners._common import poll_loop
from zetryn_bot.utils.key_pool import BirdeyeKeyPool

BIRDEYE_BASE = "https://public-api.birdeye.so"

# Source label set used by ``new_listing`` to flag Pump.fun-ecosystem tokens.
_PUMP_SOURCES = {"pump_fun", "pumpswap"}


class BirdeyeTrending:
    """Polling scanner for ``/defi/tokenlist`` sorted by 24h USD volume."""

    name = "birdeye.trending"

    def __init__(
        self,
        key_pool: BirdeyeKeyPool,
        poll_interval_s: float = 60.0,
        min_liquidity_usd: float = 5000.0,
        limit: int = 50,
    ) -> None:
        self._key_pool = key_pool
        self._poll_interval_s = poll_interval_s
        self._min_liquidity = min_liquidity_usd
        self._limit = limit

    async def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        url = f"{BIRDEYE_BASE}/defi/tokenlist"

        async def fetch() -> list[TokenCandidate]:
            params = {
                "sort_by": "v24hUSD",
                "sort_type": "desc",
                "offset": 0,
                "limit": self._limit,
                "min_liquidity": int(self._min_liquidity),
            }
            data = await _request_birdeye(session, url, params, self._key_pool, self.name)
            if not data:
                return []
            tokens = (data.get("data") or {}).get("tokens") or []
            return [t for raw in tokens if (t := _parse_tokenlist_item(raw))]

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


class BirdeyeNewListing:
    """Polling scanner for newly listed tokens (v2 endpoint with fallback)."""

    name = "birdeye.new_listing"

    def __init__(
        self,
        key_pool: BirdeyeKeyPool,
        poll_interval_s: float = 45.0,
    ) -> None:
        self._key_pool = key_pool
        self._poll_interval_s = poll_interval_s
        self._v2_available = True  # Optimistic; flipped to False on 400.

    async def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        async def fetch() -> list[TokenCandidate]:
            if self._v2_available:
                v2_result, status_400 = await self._fetch_v2(session)
                if status_400:
                    self._v2_available = False
                else:
                    return v2_result
            return await self._fetch_fallback(session)

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate

    async def _fetch_v2(self, session: aiohttp.ClientSession) -> tuple[list[TokenCandidate], bool]:
        """Returns ``(candidates, is_400_tier_restriction)``.

        When ``is_400_tier_restriction`` is True, the caller flips off v2
        for subsequent polls and uses the fallback instead.
        """
        log = logger.bind(component=self.name)
        url = f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing"
        params = {"limit": 20, "offset": 0}
        key = await self._key_pool.acquire()
        if not key:
            return [], False
        try:
            async with session.get(
                url,
                headers=_headers(key),
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 65))
                    await self._key_pool.mark_rate_limited(key, retry_after)
                    return [], False
                if resp.status == 400:
                    body = await resp.text()
                    if "Compute units" in body:
                        await self._key_pool.mark_rate_limited(key, 86400)
                        log.warning("daily compute units exhausted — key cooled 24h")
                        return [], False
                    # Tier restriction — switch to fallback for good.
                    log.debug(f"v2 400 (tier restriction?): {body[:120]}")
                    return [], True
                if resp.status != 200:
                    log.warning(f"v2 status {resp.status}")
                    return [], False
                data = await resp.json()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"v2 error: {exc}")
            return [], False

        items = (data.get("data") or {}).get("items") or []
        return (
            [t for raw in items if (t := _parse_new_listing_item(raw))],
            False,
        )

    async def _fetch_fallback(self, session: aiohttp.ClientSession) -> list[TokenCandidate]:
        """Fallback: tokenlist sorted by ``recentListingTime``."""
        url = f"{BIRDEYE_BASE}/defi/tokenlist"
        params = {
            "sort_by": "recentListingTime",
            "sort_type": "desc",
            "offset": 0,
            "limit": 20,
            "min_liquidity": 1000,
        }
        data = await _request_birdeye(session, url, params, self._key_pool, self.name + ".fallback")
        if not data:
            return []
        items = (data.get("data") or {}).get("tokens") or []
        out: list[TokenCandidate] = []
        for raw in items:
            token = _parse_tokenlist_item(raw)
            if token:
                # Mark as new listing instead of trending.
                token = token.model_copy(update={"sources": ["birdeye_new"]})
                out.append(token)
        return out


# ──────────────────────────────────────────────────────────────────────────
# Internal HTTP helper (shared by both scanners and the fallback path)
# ──────────────────────────────────────────────────────────────────────────


def _headers(api_key: str) -> dict:
    return {
        "X-API-KEY": api_key,
        "x-chain": "solana",
        "Accept": "application/json",
    }


async def _request_birdeye(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    key_pool: BirdeyeKeyPool,
    name: str,
) -> dict | None:
    """Authenticated GET with BirdEye's rate-limit + compute-unit handling.

    Returns the parsed JSON dict or ``None`` if the request couldn't be
    completed (no key, rate limit, network error, non-200).
    """
    log = logger.bind(component=name)
    key = await key_pool.acquire()
    if not key:
        log.debug("no key available — skipping poll")
        return None
    try:
        async with session.get(
            url,
            headers=_headers(key),
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", 65))
                await key_pool.mark_rate_limited(key, retry_after)
                return None
            if resp.status == 400:
                body = await resp.text()
                if "Compute units" in body:
                    await key_pool.mark_rate_limited(key, 86400)
                    log.warning("daily compute units exhausted — key cooled 24h")
                else:
                    log.warning(f"400: {body[:120]}")
                return None
            if resp.status != 200:
                log.warning(f"status {resp.status}")
                return None
            return await resp.json()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning(f"request error: {exc}")
        return None


def _parse_tokenlist_item(item: dict) -> TokenCandidate | None:
    address = item.get("address")
    if not address:
        return None
    symbol = item.get("symbol", "")
    # Skip wrapped SOL and stablecoins masquerading as base tokens.
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
    source = "birdeye_new_pumpfun" if source_raw in _PUMP_SOURCES else "birdeye_new"

    added_raw = item.get("liquidityAddedAt")
    created_at: datetime | None = None
    age_seconds = 0
    if added_raw:
        try:
            created_at = datetime.fromisoformat(added_raw)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            age_seconds = int((datetime.now(tz=UTC) - created_at).total_seconds())
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


__all__ = ["BirdeyeNewListing", "BirdeyeTrending"]

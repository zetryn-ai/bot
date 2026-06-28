"""DexScreener — token discovery and promotion-signal polling.

Source: https://docs.dexscreener.com
Auth: None (public API)
Mechanism: REST polling, three independent endpoints exposed as three
    Scanner classes (new pairs / trending / boost).
Rate limits: ~300 RPM combined across all endpoints (per docs).
Emits: TokenCandidate via Scanner.stream(). Caller decides the sink.

Three scanners in one module — each is a thin wrapper around a single
endpoint with its own poll cadence:

- :class:`DexscreenerNewPairs` — recently added token profiles. Default
  10s interval. Higher cadence because new tokens are time-sensitive.
- :class:`DexscreenerTrending` — top boost leaders (cumulative). Slower
  cadence; trending data updates slowly. Default 30s.
- :class:`DexscreenerBoost` — latest boost promotions (paid for
  visibility, proxy for team commitment). Default 20s.

Two module-level enrichment helpers are kept for callers that want to
top up a TokenCandidate with full pair data (used after the scanner has
discovered the address):

- :func:`fetch_pair_by_address`
- :func:`enrich_from_pair`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import aiohttp

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners._common import fetch_json, poll_loop

DEXSCREENER_BASE = "https://api.dexscreener.com"


# ──────────────────────────────────────────────────────────────────────────
# Scanner classes
# ──────────────────────────────────────────────────────────────────────────


class DexscreenerNewPairs:
    """Polling scanner for ``token-profiles/latest`` — newly added tokens."""

    name = "dexscreener.new_pairs"

    def __init__(self, poll_interval_s: float = 10.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[TokenCandidate]:
        url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"

        async def fetch() -> list[TokenCandidate]:
            data = await fetch_json(session, url, name=self.name)
            if not isinstance(data, list):
                return []
            out: list[TokenCandidate] = []
            for item in data:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item, source="dexscreener")
                if token:
                    out.append(token)
            return out

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


class DexscreenerTrending:
    """Polling scanner for ``token-boosts/top`` — cumulative boost leaders."""

    name = "dexscreener.trending"

    def __init__(self, poll_interval_s: float = 30.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[TokenCandidate]:
        url = f"{DEXSCREENER_BASE}/token-boosts/top/v1"

        async def fetch() -> list[TokenCandidate]:
            data = await fetch_json(session, url, name=self.name)
            if not isinstance(data, list):
                return []
            out: list[TokenCandidate] = []
            for item in data:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item, source="dexscreener")
                if token:
                    out.append(token)
            return out

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


class DexscreenerBoost:
    """Polling scanner for ``token-boosts/latest`` — latest paid promotions."""

    name = "dexscreener.boost"

    def __init__(self, poll_interval_s: float = 20.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[TokenCandidate]:
        url = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"

        async def fetch() -> list[TokenCandidate]:
            data = await fetch_json(session, url, name=self.name)
            if not isinstance(data, list):
                return []
            out: list[TokenCandidate] = []
            for item in data:
                if item.get("chainId") != "solana":
                    continue
                token = _parse_profile(item, source="dexscreener_boost")
                if token:
                    out.append(token)
            return out

        async for candidate in poll_loop(self.name, self._poll_interval_s, fetch):
            yield candidate


# ──────────────────────────────────────────────────────────────────────────
# Module-level enrichment helpers (still useful — used after discovery)
# ──────────────────────────────────────────────────────────────────────────


async def fetch_pair_by_address(
    session: aiohttp.ClientSession, address: str
) -> dict | None:
    """Fetch full pair data for a specific token address.

    Returns the pair with highest liquidity, or ``None`` if the token has
    no pairs or the request fails.
    """
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{address}"
    data = await fetch_json(session, url, name="dexscreener.fetch_pair")
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    return max(
        pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)
    )


def enrich_from_pair(token: TokenCandidate, pair: dict) -> TokenCandidate:
    """Return ``token`` enriched with DexScreener pair fields.

    Returns a new :class:`TokenCandidate` via ``model_copy(update=...)`` —
    the input is not mutated.
    """
    liquidity = pair.get("liquidity", {}) or {}
    volume = pair.get("volume", {}) or {}
    txns = pair.get("txns", {}) or {}

    updates: dict = {
        "liquidity_usd": float(liquidity.get("usd", 0) or 0),
        "market_cap_usd": float(pair.get("marketCap", 0) or 0),
        "price_usd": float(pair.get("priceUsd", 0) or 0),
        "volume_5m_usd": float(volume.get("m5", 0) or 0),
        "volume_1h_usd": float(volume.get("h1", 0) or 0),
    }

    txns_5m = txns.get("m5", {}) or {}
    buys_5m = int(txns_5m.get("buys", 0) or 0)
    sells_5m = int(txns_5m.get("sells", 0) or 0)
    updates["buys_5m"] = buys_5m
    updates["sells_5m"] = sells_5m
    updates["txns_5m"] = buys_5m + sells_5m

    pair_created = pair.get("pairCreatedAt")
    if pair_created:
        created_at = datetime.fromtimestamp(pair_created / 1000, tz=timezone.utc)
        updates["created_at"] = created_at
        updates["age_seconds"] = int(
            (datetime.now(timezone.utc) - created_at).total_seconds()
        )

    sources = list(token.sources)
    if "dexscreener" not in sources:
        sources.append("dexscreener")
    updates["sources"] = sources

    return token.model_copy(update=updates)


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


__all__ = [
    "DexscreenerBoost",
    "DexscreenerNewPairs",
    "DexscreenerTrending",
    "enrich_from_pair",
    "fetch_pair_by_address",
]

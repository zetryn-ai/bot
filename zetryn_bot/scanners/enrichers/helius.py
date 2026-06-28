"""Helius — holder distribution and token metadata enrichment.

Source: https://docs.helius.dev
Auth: HELIUS_API_KEYS (comma-separated; pool-rotated)
Mechanism: On-demand JSON-RPC calls (getTokenAccounts + getAsset) to
    the Helius mainnet RPC endpoint
Rate limits: Free tier ~10 RPS per key; pool rotation absorbs spikes
Populates: holder_count, top10_holder_pct, dev_wallet_pct, is_mintable,
    is_freezable, and (if missing) symbol + name
"""

from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.utils.key_pool import HeliusKeyPool

HELIUS_RPC = "https://mainnet.helius-rpc.com"


class HeliusEnricher:
    """On-demand enricher backed by Helius DAS + RPC endpoints.

    Two parallel calls per ``enrich()`` invocation:

    1. ``getTokenAccounts`` — top-1000 holders, used to compute
       ``holder_count``, ``top10_holder_pct``, and ``dev_wallet_pct``
       (largest holder share as a heuristic for dev concentration).
    2. ``getAsset`` — token metadata: symbol, name, and mint/freeze
       authority status (translated to ``is_mintable`` / ``is_freezable``
       booleans).

    The two requests run concurrently with :func:`asyncio.gather` so
    enrichment latency is bounded by the slower of the two, not the sum.
    """

    name = "helius"

    def __init__(self, api_keys: list[str] | HeliusKeyPool) -> None:
        """Construct an enricher with a key pool.

        Args:
            api_keys: Either a raw list of Helius API key strings (a
                :class:`HeliusKeyPool` is built internally) or a
                pre-built pool the caller manages externally.
        """
        if isinstance(api_keys, HeliusKeyPool):
            self._key_pool = api_keys
        else:
            self._key_pool = HeliusKeyPool(api_keys) if api_keys else None
        self._log = logger.bind(component=self.name)

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        """Return ``candidate`` enriched with Helius data, or unchanged on failure."""
        if self._key_pool is None:
            return candidate

        key = await self._key_pool.acquire()
        if not key:
            self._log.debug(f"no key available for {mint[:8]}...")
            return candidate

        holders_data, metadata = await asyncio.gather(
            self._fetch_holders(session, mint, key),
            self._fetch_token_metadata(session, mint, key),
            return_exceptions=True,
        )

        updates: dict = {}

        if isinstance(holders_data, dict):
            updates["holder_count"] = holders_data.get("total", 0)
            updates["top10_holder_pct"] = holders_data.get("top10_pct", 0.0)
            updates["dev_wallet_pct"] = holders_data.get("dev_pct", 0.0)

        if isinstance(metadata, dict):
            if not candidate.symbol:
                updates["symbol"] = metadata.get("symbol", "")
            if not candidate.name:
                updates["name"] = metadata.get("name", "")
            updates["is_mintable"] = metadata.get("mint_authority_disabled") is False
            updates["is_freezable"] = metadata.get("freeze_authority_disabled") is False

        sources = list(candidate.sources)
        if "helius" not in sources:
            sources.append("helius")
        updates["sources"] = sources

        return candidate.model_copy(update=updates)

    async def _fetch_holders(
        self,
        session: aiohttp.ClientSession,
        mint: str,
        api_key: str,
    ) -> dict:
        """Fetch holder distribution via Helius DAS API."""
        url = f"{HELIUS_RPC}/?api-key={api_key}"
        payload = {
            "jsonrpc": "2.0",
            "id": "holders",
            "method": "getTokenAccounts",
            "params": {
                "mint": mint,
                "limit": 1000,
                "displayOptions": {"showZeroBalance": False},
            },
        }
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
                accounts = data.get("result", {}).get("token_accounts", [])
                if not accounts:
                    return {}

                total_supply = sum(float(a.get("amount", 0)) for a in accounts)
                if total_supply == 0:
                    return {}

                sorted_accounts = sorted(
                    accounts, key=lambda a: float(a.get("amount", 0)), reverse=True
                )
                top10_supply = sum(
                    float(a.get("amount", 0)) for a in sorted_accounts[:10]
                )
                top10_pct = (top10_supply / total_supply) * 100

                # Largest single holder as a heuristic for dev concentration.
                dev_pct = (
                    (float(sorted_accounts[0].get("amount", 0)) / total_supply) * 100
                    if sorted_accounts
                    else 0
                )

                return {
                    "total": len(accounts),
                    "top10_pct": round(top10_pct, 2),
                    "dev_pct": round(dev_pct, 2),
                }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.debug(f"holder fetch error for {mint}: {exc}")
            return {}

    async def _fetch_token_metadata(
        self,
        session: aiohttp.ClientSession,
        mint: str,
        api_key: str,
    ) -> dict:
        """Fetch token metadata via Helius DAS ``getAsset``."""
        url = f"{HELIUS_RPC}/?api-key={api_key}"
        payload = {
            "jsonrpc": "2.0",
            "id": "metadata",
            "method": "getAsset",
            "params": {"id": mint},
        }
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
                result = data.get("result", {})
                content = result.get("content", {})
                metadata = content.get("metadata", {})
                token_info = result.get("token_info", {})

                return {
                    "symbol": token_info.get("symbol") or metadata.get("symbol", ""),
                    "name": metadata.get("name", ""),
                    "mint_authority_disabled": result.get("authorities", []) == [],
                    "freeze_authority_disabled": token_info.get("freeze_authority") is None,
                }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.debug(f"metadata fetch error for {mint}: {exc}")
            return {}


__all__ = ["HeliusEnricher"]

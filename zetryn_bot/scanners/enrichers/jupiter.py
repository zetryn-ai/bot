"""Jupiter — token price enrichment.

Source: https://lite-api.jup.ag/price/v3
Auth: None (public API)
Mechanism: On-demand REST GET for a single token mint
Rate limits: Not formally published; the lite-api endpoint is permissive
Populates: price_usd (only if currently zero)
"""

from __future__ import annotations

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

JUPITER_PRICE_API = "https://lite-api.jup.ag/price/v3"


class JupiterEnricher:
    """Fetch the current USD price for a mint via Jupiter and apply it.

    Only fills :attr:`TokenCandidate.price_usd` when the input candidate's
    price is zero — Jupiter is a fallback price source, not the primary;
    DEX-side scanners (DexScreener, GeckoTerminal) usually provide a more
    accurate per-pair price when available.
    """

    name = "jupiter"

    def __init__(self) -> None:
        self._log = logger.bind(component=self.name)

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        # Skip the network call when price is already populated.
        if candidate.price_usd > 0:
            return candidate

        price = await self._fetch_price(session, mint)
        if price is None:
            return candidate

        sources = list(candidate.sources)
        if "jupiter" not in sources:
            sources.append("jupiter")
        return candidate.model_copy(update={"price_usd": price, "sources": sources})

    async def _fetch_price(self, session: aiohttp.ClientSession, mint: str) -> float | None:
        try:
            async with session.get(
                JUPITER_PRICE_API,
                params={"ids": mint},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                price_data = data.get(mint, {})
                price = price_data.get("usdPrice")
                return float(price) if price else None
        except Exception as exc:
            self._log.debug(f"price fetch error for {mint}: {exc}")
            return None


__all__ = ["JupiterEnricher"]

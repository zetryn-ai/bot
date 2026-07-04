"""Jupiter quote client — read-only pricing for the paper executor.

Source: https://lite-api.jup.ag/swap/v1/quote (free tier; override via
``JUPITER_QUOTE_URL``). The legacy ``quote-api.jup.ag/v6`` host is dead (DNS no
longer resolves), so the endpoint is pinned to the current one and verified live
at build time. This client only *quotes* — it never builds or sends a swap
transaction (that is the M5 ``LiveExecutor``'s job).

All amounts are atomic base units (lamports for SOL, the token's smallest unit
otherwise). The executor works entirely in SOL terms, so token decimals never
need resolving: buy quotes SOL→mint (out = tokens received), sell/valuation
quotes mint→SOL (out = lamports back).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import aiohttp
from loguru import logger

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

_QUOTE_URL = os.environ.get("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote")

log = logger.bind(component="execution.jupiter")


@dataclass(frozen=True)
class Quote:
    """One Jupiter quote. ``out_amount`` is in the output mint's atomic units."""

    in_amount: int
    out_amount: int
    price_impact_pct: float


def sol_to_lamports(sol: float) -> int:
    return round(sol * LAMPORTS_PER_SOL)


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


class JupiterQuote:
    """Thin async wrapper over the Jupiter quote endpoint."""

    def __init__(self, quote_url: str = _QUOTE_URL) -> None:
        self._url = quote_url

    async def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_atomic: int,
        slippage_bps: int = 100,
    ) -> Quote | None:
        """Return a :class:`Quote`, or ``None`` on any error (log + continue)."""
        if amount_atomic <= 0:
            return None
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_atomic,
            "slippageBps": slippage_bps,
        }
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    self._url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp,
            ):
                if resp.status != 200:
                    log.debug("quote HTTP {}: {}", resp.status, (await resp.text())[:120])
                    return None
                body = await resp.json()
        except Exception as exc:
            log.debug("quote error {} -> {}: {}", input_mint[:6], output_mint[:6], exc)
            return None

        try:
            return Quote(
                in_amount=int(body["inAmount"]),
                out_amount=int(body["outAmount"]),
                price_impact_pct=float(body.get("priceImpactPct") or 0.0),
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("quote parse error: {}", exc)
            return None

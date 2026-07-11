"""Jupiter quote + swap-build client.

Source: https://lite-api.jup.ag/swap/v1/{quote,swap} (free tier; override via
``JUPITER_QUOTE_URL`` / ``JUPITER_SWAP_URL``). The legacy ``quote-api.jup.ag/v6``
host is dead (DNS no longer resolves), so the endpoint is pinned to the current
one and verified live at build time.

``quote()`` (used by both Paper and Live executors) only reads pricing.
``build_swap_tx()`` (M5, LiveExecutor only) additionally builds an unsigned
swap transaction for a given user pubkey — it never signs or sends anything;
that is ``LiveExecutor``'s job with the wallet's keypair.

All amounts are atomic base units (lamports for SOL, the token's smallest unit
otherwise). The executor works entirely in SOL terms, so token decimals never
need resolving: buy quotes SOL→mint (out = tokens received), sell/valuation
quotes mint→SOL (out = lamports back).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import aiohttp
from loguru import logger

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

_QUOTE_URL = os.environ.get("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote")
_SWAP_URL = os.environ.get("JUPITER_SWAP_URL", "https://lite-api.jup.ag/swap/v1/swap")

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
    """Thin async wrapper over the Jupiter quote + swap-build endpoints."""

    def __init__(self, quote_url: str = _QUOTE_URL, swap_url: str = _SWAP_URL) -> None:
        self._url = quote_url
        self._swap_url = swap_url
        self._last_429_warn = 0.0

    async def _quote_json(
        self, input_mint: str, output_mint: str, amount_atomic: int, slippage_bps: int
    ) -> tuple[dict | None, int | None]:
        """Return ``(json, http_status)``; json is None on failure, status is
        None on a network-level error."""
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
                if resp.status == 429:
                    # WARNING (not debug) so it reaches the M7 log bridge →
                    # Telegram. Free tier = 60 req / 60s sliding window
                    # (verified 2026-07-11 at developers.jup.ag). Throttled to
                    # one line/min so a storm doesn't flood the log file; the
                    # first VPS deploy had positions stuck 11h behind
                    # debug-level 429s nobody could see.
                    now = time.monotonic()
                    if now - self._last_429_warn >= 60.0:
                        self._last_429_warn = now
                        log.warning(
                            "Jupiter quote rate limited (HTTP 429) — free tier is 60 req/min"
                        )
                    return None, 429
                if resp.status != 200:
                    log.debug("quote HTTP {}: {}", resp.status, (await resp.text())[:120])
                    return None, resp.status
                return await resp.json(), 200
        except Exception as exc:
            log.debug("quote error {} -> {}: {}", input_mint[:6], output_mint[:6], exc)
            return None, None

    async def quote_or_status(
        self,
        input_mint: str,
        output_mint: str,
        amount_atomic: int,
        slippage_bps: int = 100,
    ) -> tuple[Quote | None, int | None]:
        """Like :meth:`quote`, but also reports the HTTP status on failure.

        Lets callers separate a transient failure (429 / 5xx / network →
        retry later) from a permanent one (400 no-route → the token is very
        likely dead and will never quote again).
        """
        if amount_atomic <= 0:
            return None, None
        body, status = await self._quote_json(input_mint, output_mint, amount_atomic, slippage_bps)
        if body is None:
            return None, status
        try:
            return Quote(
                in_amount=int(body["inAmount"]),
                out_amount=int(body["outAmount"]),
                price_impact_pct=float(body.get("priceImpactPct") or 0.0),
            ), status
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("quote parse error: {}", exc)
            return None, status

    async def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_atomic: int,
        slippage_bps: int = 100,
    ) -> Quote | None:
        """Return a :class:`Quote`, or ``None`` on any error (log + continue)."""
        q, _status = await self.quote_or_status(
            input_mint, output_mint, amount_atomic, slippage_bps
        )
        return q

    async def build_swap_tx(
        self,
        input_mint: str,
        output_mint: str,
        amount_atomic: int,
        user_pubkey: str,
        *,
        slippage_bps: int = 100,
        priority_fee_lamports: int | None = None,
    ) -> str | None:
        """Build an unsigned swap transaction for ``user_pubkey``.

        Returns the base64-encoded ``swapTransaction``, or ``None`` on any
        error. Never signs or sends — that's the caller's (LiveExecutor's) job.
        """
        if amount_atomic <= 0:
            return None
        quote_json = await self._quote_json(input_mint, output_mint, amount_atomic, slippage_bps)
        if quote_json is None:
            return None

        body: dict = {
            "quoteResponse": quote_json,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
        }
        if priority_fee_lamports is not None:
            body["prioritizationFeeLamports"] = priority_fee_lamports
        else:
            body["prioritizationFeeLamports"] = "auto"

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    self._swap_url, json=body, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp,
            ):
                if resp.status != 200:
                    log.warning("swap-build HTTP {}: {}", resp.status, (await resp.text())[:200])
                    return None
                swap_body = await resp.json()
        except Exception as exc:
            log.warning("swap-build error: {}", exc)
            return None

        tx = swap_body.get("swapTransaction")
        if not tx:
            log.warning("swap-build response missing swapTransaction")
            return None
        return tx

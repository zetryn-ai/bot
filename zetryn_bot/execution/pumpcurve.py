"""Pump.fun bonding-curve quotes — pricing for tokens Jupiter can't route yet.

Source: https://frontend-api-v3.pump.fun/coins/{mint} (verified live
    2026-07-12; the un-versioned frontend-api host returns HTTP 530)
Auth: none
Mechanism: on-demand REST lookup of the curve state (virtual reserves +
    ``complete`` flag), then constant-product math locally
Rate limits: not published — call volume is inherently low (only
    Jupiter-miss buys and sweeps of curve-phase positions) and a short TTL
    cache absorbs the 30s sweep cadence

Why this exists: every sniper BUY on 2026-07-12 died with "PAPER BUY aborted
— no quote". Tokens seconds old live on the pump.fun bonding curve, which
Jupiter has not indexed yet — so the executor could neither fill the entry
nor price the exit. The curve itself is a constant-product AMM over VIRTUAL
reserves, and its state is public, so paper fills can be computed exactly:

    buy : tokens_out = vT - (vS*vT) / (vS + sol_in*(1-fee))
    sell: sol_out    = (vS - (vS*vT) / (vT + tokens_in)) * (1-fee)

``complete=true`` means the curve closed (graduated to PumpSwap/Raydium);
quotes return None then — Jupiter takes over within minutes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import aiohttp
from loguru import logger

PUMP_API_BASE = "https://frontend-api-v3.pump.fun"
# Pump.fun charges 1% on curve trades; applied to keep paper fills honest.
FEE_BPS = 100

log = logger.bind(component="execution.pumpcurve")


@dataclass(frozen=True)
class CurveState:
    virtual_sol_reserves: int  # lamports
    virtual_token_reserves: int  # atomic token units
    complete: bool  # curve closed (graduated) — no longer tradable here


class PumpCurveQuote:
    """Curve-state fetcher + local constant-product quoting."""

    def __init__(self, *, cache_ttl_s: float = 3.0, base_url: str = PUMP_API_BASE) -> None:
        self._base = base_url
        self._ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, CurveState | None]] = {}

    async def state(self, mint: str) -> CurveState | None:
        """Fetch (or cache-hit) the curve state; None when unknown/unavailable."""
        hit = self._cache.get(mint)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        state: CurveState | None = None
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{self._base}/coins/{mint}",
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp,
            ):
                if resp.status == 200:
                    data = await resp.json()
                    vs = int(data.get("virtual_sol_reserves") or 0)
                    vt = int(data.get("virtual_token_reserves") or 0)
                    if vs > 0 and vt > 0:
                        state = CurveState(vs, vt, bool(data.get("complete")))
                elif resp.status != 404:
                    log.debug("curve state HTTP {} for {}", resp.status, mint[:8])
        except Exception as exc:
            log.debug("curve state error for {}: {}", mint[:8], exc)
        if len(self._cache) > 512:
            self._cache.pop(next(iter(self._cache)))
        self._cache[mint] = (time.monotonic(), state)
        return state

    async def buy_quote(self, mint: str, sol_lamports: int) -> int | None:
        """Tokens (atomic) received for ``sol_lamports``, after the 1% fee."""
        s = await self.state(mint)
        if s is None or s.complete or sol_lamports <= 0:
            return None
        return buy_out(s, sol_lamports)

    async def sell_quote(self, mint: str, tokens_atomic: int) -> int | None:
        """Lamports received for ``tokens_atomic``, after the 1% fee."""
        s = await self.state(mint)
        if s is None or s.complete or tokens_atomic <= 0:
            return None
        return sell_out(s, tokens_atomic)


def buy_out(s: CurveState, sol_lamports: int) -> int:
    """Constant-product buy against virtual reserves (fee on the SOL input)."""
    sol_eff = sol_lamports * (10_000 - FEE_BPS) // 10_000
    k = s.virtual_sol_reserves * s.virtual_token_reserves
    new_tokens = k // (s.virtual_sol_reserves + sol_eff)
    return max(0, s.virtual_token_reserves - new_tokens)


def sell_out(s: CurveState, tokens_atomic: int) -> int:
    """Constant-product sell against virtual reserves (fee on the SOL output)."""
    k = s.virtual_sol_reserves * s.virtual_token_reserves
    new_sol = k // (s.virtual_token_reserves + tokens_atomic)
    gross = max(0, s.virtual_sol_reserves - new_sol)
    return gross * (10_000 - FEE_BPS) // 10_000


__all__ = ["PumpCurveQuote", "CurveState", "buy_out", "sell_out"]

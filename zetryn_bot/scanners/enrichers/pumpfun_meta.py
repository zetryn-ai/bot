"""Pump.fun coin metadata — socials + live curve demand for the sniper v2.

Source: https://frontend-api-v3.pump.fun/coins/{mint} (verified live
    2026-07-15: response carries twitter/telegram/website, usd_market_cap,
    virtual reserves; the un-versioned host returns HTTP 530)
Auth: none
Mechanism: on-demand REST lookup, pumpfun_ws candidates only
Rate limits: not published — bounded by the (dust-filtered) sniper flow
Populates: has_website / has_twitter / has_telegram, bonding_curve_sol
    (refreshed), curve_velocity_sol_per_min (SOL inflow since launch / age),
    market_cap_usd (when missing)
"""

from __future__ import annotations

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

PUMP_API_BASE = "https://frontend-api-v3.pump.fun"
_VIRTUAL_SOL_FLOOR = 30.0  # pump.fun virtual reserve baseline (SOL)


class PumpfunMetaEnricher:
    """Socials + curve-velocity enrichment for fresh pump.fun launches."""

    name = "pumpfun_meta"

    def __init__(self, base_url: str = PUMP_API_BASE) -> None:
        self._base = base_url
        self._log = logger.bind(component=self.name)

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        # Scanner name is "pumpfun.ws" — match by prefix, same rule the
        # adapter's _map_source uses.
        if not candidate.sources or candidate.sources[0].split(".", 1)[0] != "pumpfun":
            return candidate  # sniper-route input only
        try:
            async with session.get(
                f"{self._base}/coins/{mint}",
                timeout=aiohttp.ClientTimeout(total=6),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    return candidate
                data = await resp.json()
        except Exception as exc:
            self._log.debug(f"meta fetch failed for {mint[:8]}: {exc}")
            return candidate

        curve_sol_now = max(
            0.0, float(data.get("virtual_sol_reserves") or 0) / 1e9 - _VIRTUAL_SOL_FLOOR
        )
        age_min = max(candidate.age_seconds, 5) / 60.0
        # Demand rate: real SOL that flowed into the curve since launch,
        # per minute. A 30s-old token at 3 SOL/min is being BOUGHT; the same
        # curve level reached over 20 minutes is drift.
        velocity = curve_sol_now / age_min if curve_sol_now > 0 else 0.0

        updates: dict = {
            "has_website": bool(data.get("website")),
            "has_twitter": bool(data.get("twitter")),
            "has_telegram": bool(data.get("telegram")),
            "curve_velocity_sol_per_min": round(velocity, 3),
        }
        if curve_sol_now > 0:
            updates["bonding_curve_sol"] = curve_sol_now
        if not candidate.market_cap_usd and data.get("usd_market_cap"):
            updates["market_cap_usd"] = float(data["usd_market_cap"])

        sources = list(candidate.sources)
        if self.name not in sources:
            sources.append(self.name)
        updates["sources"] = sources
        return candidate.model_copy(update=updates)


__all__ = ["PumpfunMetaEnricher"]

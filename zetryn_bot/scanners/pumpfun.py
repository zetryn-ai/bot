"""Pump.fun — WebSocket streaming for new-token and migration events.

Source: https://pumpportal.fun
Auth: PUMPPORTAL_API_KEY (optional but recommended for stability)
Mechanism: Single persistent WebSocket subscribing to two channels:
    - ``subscribeNewToken`` — every new token launched on Pump.fun
    - ``subscribeMigration`` — tokens graduating from Pump.fun to
      Raydium / PumpSwap (proven demand, ~85 SOL raised)
Rate limits: None observed; the WS is push-based.
Emits: TokenCandidate via Scanner.stream(). Caller routes by source
    label — new tokens carry ``"pumpfun_ws"``, migrations carry
    ``"pumpfun_migration"``.

The WebSocket reconnects on disconnect with a 5-second back-off. Inside
``stream()`` the loop is wrapped so that one disconnect doesn't kill the
iterator; the caller stays subscribed across reconnects.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import aiohttp
import websockets
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

PUMPPORTAL_WS_BASE = "wss://pumpportal.fun/api/data"

# Conservative USD-per-SOL estimate used for back-of-envelope mcap / liquidity
# conversions. Real-time price is the caller's concern; this is just a sanity
# baseline so downstream filters have a number to look at.
_SOL_PRICE_USD = 150.0


class PumpfunStream:
    """WebSocket scanner for Pump.fun new-token + migration events."""

    name = "pumpfun.ws"

    def __init__(self, api_key: str = "", reconnect_delay_s: float = 5.0) -> None:
        self._api_key = api_key
        self._reconnect_delay_s = reconnect_delay_s
        self._log = logger.bind(component=self.name)

    async def stream(
        self, session: aiohttp.ClientSession  # noqa: ARG002 — unused but required by Protocol
    ) -> AsyncIterator[TokenCandidate]:
        ws_url = (
            f"{PUMPPORTAL_WS_BASE}?api-key={self._api_key}"
            if self._api_key
            else PUMPPORTAL_WS_BASE
        )
        masked = f"...{self._api_key[-6:]}" if self._api_key else "no key"

        while True:
            try:
                self._log.info(f"connecting (key={masked})")
                async with websockets.connect(ws_url, ping_interval=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    self._log.info("subscribed to newToken + migration events")

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                        except json.JSONDecodeError as exc:
                            self._log.debug(f"non-JSON message: {exc}")
                            continue
                        token = _parse_event(data)
                        if token:
                            yield token
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect on any failure
                self._log.warning(
                    f"disconnected: {exc} — reconnecting in {self._reconnect_delay_s}s"
                )
                await asyncio.sleep(self._reconnect_delay_s)


def _parse_event(data: dict) -> TokenCandidate | None:
    """Route an incoming event to the correct parser based on payload shape."""
    # Migration payloads carry both 'mint' and 'pool' (the new venue's pool).
    if "pool" in data or ("mint" in data and "pool" in data):
        return _parse_migration_event(data)
    if "mint" in data:
        return _parse_newtoken_event(data)
    return None


def _parse_newtoken_event(data: dict) -> TokenCandidate | None:
    """Parse a ``subscribeNewToken`` event into a :class:`TokenCandidate`."""
    mint = data.get("mint")
    if not mint:
        return None

    created_ts = data.get("created_timestamp")
    created_at = (
        datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc)
        if created_ts
        else None
    )

    # Bonding-curve signals: Pump.fun's virtual reserve is ~30 SOL; graduation
    # threshold is ~85 SOL total → 55 more "real" SOL needed.
    v_sol = float(data.get("vSolInBondingCurve") or 0)
    creator_sol = float(data.get("solAmount") or 0)
    real_sol_in_curve = max(0.0, v_sol - 30.0)
    bonding_pct = (
        min(100.0, real_sol_in_curve / 55.0 * 100) if v_sol > 0 else 0.0
    )

    mcap_sol = float(data.get("marketCapSol") or data.get("market_cap") or 0)
    mcap_usd = mcap_sol * _SOL_PRICE_USD if mcap_sol > 0 else 0.0

    return TokenCandidate(
        address=mint,
        symbol=data.get("symbol", ""),
        name=data.get("name", ""),
        created_at=created_at,
        sources=["pumpfun_ws"],
        age_seconds=0,
        market_cap_usd=mcap_usd,
        liquidity_usd=real_sol_in_curve * _SOL_PRICE_USD,
        price_usd=float(data.get("price", 0) or 0),
        creator_sol_buy=creator_sol,
        bonding_curve_sol=real_sol_in_curve,
        bonding_curve_pct=round(bonding_pct, 1),
        is_mayhem_mode=bool(data.get("is_mayhem_mode", False)),
        creator_wallet=data.get("traderPublicKey", ""),
    )


def _parse_migration_event(data: dict) -> TokenCandidate | None:
    """Parse a ``subscribeMigration`` event into a :class:`TokenCandidate`.

    Migrations indicate a token has graduated from Pump.fun to Raydium /
    PumpSwap — proven demand (raised ~85 SOL) and real on-DEX liquidity.
    Graduation takes 24-72h on average, so ``age_seconds`` is floored at
    24h to avoid downstream filters mistaking these for fresh launches.
    """
    mint = data.get("mint") or data.get("newTokenMint") or data.get("tokenMint")
    if not mint:
        return None

    symbol = data.get("symbol", "")
    name = data.get("name", "")

    # Use event-supplied SOL amount when present, else fall back to the
    # Pump.fun graduation threshold (~85 SOL) as a baseline.
    sol_amount = float(
        data.get("sol_amount", 0) or data.get("initialSol", 0) or 85
    )
    liquidity_usd = sol_amount * _SOL_PRICE_USD

    age_seconds = max(int(data.get("age_seconds", 0) or 0), 86400)
    mcap = float(data.get("market_cap", 0) or data.get("usdMarketCap", 0) or 0)
    price = float(data.get("price", 0) or 0)

    return TokenCandidate(
        address=mint,
        symbol=symbol,
        name=name,
        sources=["pumpfun_migration"],
        age_seconds=age_seconds,
        liquidity_usd=liquidity_usd,
        market_cap_usd=mcap,
        price_usd=price,
    )


__all__ = ["PumpfunStream"]

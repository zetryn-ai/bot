from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import aiohttp
import websockets
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.storage.redis_client import publish_sniper, publish_migration

PUMPPORTAL_WS_BASE = "wss://pumpportal.fun/api/data"

log = logger.bind(component="scanner.pumpfun")


async def stream_pumpfun_events(redis, api_key: str = "", pause_event=None) -> None:
    """
    Single WebSocket connection subscribing to:
    - subscribeNewToken    : token baru launch di Pump.fun
    - subscribeMigration   : token graduate dari Pump.fun ke Raydium/PumpSwap

    When pause_event is set: WS stays connected (reconnect is expensive) but
    incoming messages are discarded without publishing to Redis.
    """
    ws_url = f"{PUMPPORTAL_WS_BASE}?api-key={api_key}" if api_key else PUMPPORTAL_WS_BASE
    masked = f"...{api_key[-6:]}" if api_key else "no key"
    log.info(f"Connecting to Pumpportal WebSocket (key={masked})")

    async with websockets.connect(ws_url, ping_interval=20) as ws:
        # Subscribe to both channels in single connection
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        await ws.send(json.dumps({"method": "subscribeMigration"}))
        log.info("Subscribed to newToken + migration events")

        async for raw_msg in ws:
            # Discard when paused — keeps WS alive, avoids reconnect cost
            if pause_event and pause_event.is_set():
                continue
            try:
                data = json.loads(raw_msg)
                token = _parse_event(data)
                if token:
                    is_migration = "pumpfun_migration" in token.sources
                    if is_migration:
                        await publish_migration(redis, token.model_dump(mode="json"))
                    else:
                        await publish_sniper(redis, token.model_dump(mode="json"))
                    src = token.sources[0] if token.sources else "?"
                    log.debug(f"[{src}] {token.symbol or '?'} ({token.address[:8]}...)")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"Parse error: {e}")


def _parse_event(data: dict) -> TokenCandidate | None:
    """Route event to the correct parser based on payload shape."""
    # Migration event has 'pool' or 'newTokenMint' or 'mint' + 'pool' fields
    if "pool" in data or ("mint" in data and "pool" in data):
        return _parse_migration_event(data)
    # New token event has 'mint' without 'pool'
    if "mint" in data:
        return _parse_newtoken_event(data)
    return None


def _parse_newtoken_event(data: dict) -> TokenCandidate | None:
    """Parse subscribeNewToken event."""
    mint = data.get("mint")
    if not mint:
        return None

    created_ts = data.get("created_timestamp")
    created_at = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc) if created_ts else None

    # Bonding curve signals — from vSolInBondingCurve
    # Pump.fun virtual reserve = ~30 SOL; graduation threshold = 85 SOL total
    # Real SOL in curve = vSolInBondingCurve - 30; graduation needs 55 more real SOL
    v_sol = float(data.get("vSolInBondingCurve") or 0)
    creator_sol = float(data.get("solAmount") or 0)
    sol_price_est = 150.0  # conservative USD estimate
    real_sol_in_curve = max(0.0, v_sol - 30.0)
    bonding_pct = min(100.0, real_sol_in_curve / 55.0 * 100) if v_sol > 0 else 0.0

    # Market cap: prefer marketCapSol field if available
    mcap_sol = float(data.get("marketCapSol") or data.get("market_cap") or 0)
    mcap_usd = mcap_sol * sol_price_est if mcap_sol > 0 else 0.0

    token = TokenCandidate(
        address=mint,
        symbol=data.get("symbol", ""),
        name=data.get("name", ""),
        created_at=created_at,
        sources=["pumpfun_ws"],
        age_seconds=0,
        market_cap_usd=mcap_usd,
        liquidity_usd=real_sol_in_curve * sol_price_est,
        price_usd=float(data.get("price", 0) or 0),
        # Bonding curve enrichment
        creator_sol_buy=creator_sol,
        bonding_curve_sol=real_sol_in_curve,
        bonding_curve_pct=round(bonding_pct, 1),
        is_mayhem_mode=bool(data.get("is_mayhem_mode", False)),
        creator_wallet=data.get("traderPublicKey", ""),
    )

    log.debug(
        f"[pumpfun_ws] {token.symbol or '?'} ({mint[:8]}...) "
        f"creator_buy={creator_sol:.1f}SOL curve={bonding_pct:.1f}%"
        + (" [MAYHEM]" if token.is_mayhem_mode else "")
    )
    return token


def _parse_migration_event(data: dict) -> TokenCandidate | None:
    """
    Parse subscribeMigration event — token graduated from Pump.fun to Raydium/PumpSwap.
    These tokens have proven demand (raised ~85 SOL) and real liquidity.
    Fast-tracked through L2 filter — graduation itself is the entry signal.
    """
    mint = data.get("mint") or data.get("newTokenMint") or data.get("tokenMint")
    if not mint:
        return None

    symbol = data.get("symbol", "")
    name = data.get("name", "")

    # Pump.fun graduation threshold is ~85 SOL. Use event data if available.
    sol_amount = float(data.get("sol_amount", 0) or data.get("initialSol", 0) or 85)
    sol_price = 150  # conservative estimate
    liquidity_usd = sol_amount * sol_price

    # Graduation takes 24-72h on average — use 86400s (24h) as floor for age
    # so holder/dev checks are not bypassed by age_seconds=0
    age_seconds = max(int(data.get("age_seconds", 0) or 0), 86400)

    mcap = float(data.get("market_cap", 0) or data.get("usdMarketCap", 0) or 0)
    price = float(data.get("price", 0) or 0)

    log.info(f"[MIGRATION] {symbol or mint[:8]}... graduated to Raydium | liq≈${liquidity_usd:.0f}")

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

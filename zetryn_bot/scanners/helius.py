from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.utils.key_pool import HeliusKeyPool

log = logger.bind(component="scanner.helius")

HELIUS_BASE = "https://api.helius.xyz/v0"
HELIUS_RPC = "https://mainnet.helius-rpc.com"


async def enrich_token_helius(
    session: aiohttp.ClientSession,
    token: TokenCandidate,
    key_pool: HeliusKeyPool | None,
) -> TokenCandidate:
    """Enrich TokenCandidate with on-chain data from Helius."""
    if not key_pool:
        return token

    key = await key_pool.acquire()
    if not key:
        log.debug(f"Helius: no key available for {token.address[:8]}...")
        return token

    holders_task = _fetch_holders(session, token.address, key)
    metadata_task = _fetch_token_metadata(session, token.address, key)

    holders_data, metadata = await asyncio.gather(
        holders_task, metadata_task, return_exceptions=True
    )

    if isinstance(holders_data, dict) and not isinstance(holders_data, Exception):
        token.holder_count = holders_data.get("total", 0)
        token.top10_holder_pct = holders_data.get("top10_pct", 0.0)
        token.dev_wallet_pct = holders_data.get("dev_pct", 0.0)

    if isinstance(metadata, dict) and not isinstance(metadata, Exception):
        if not token.symbol:
            token.symbol = metadata.get("symbol", "")
        if not token.name:
            token.name = metadata.get("name", "")
        token.is_mintable = metadata.get("mint_authority_disabled") is False
        token.is_freezable = metadata.get("freeze_authority_disabled") is False

    if "helius" not in token.sources:
        token.sources.append("helius")
    return token


async def _fetch_holders(
    session: aiohttp.ClientSession, mint: str, api_key: str
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
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            accounts = data.get("result", {}).get("token_accounts", [])
            if not accounts:
                return {}

            total_supply = sum(float(a.get("amount", 0)) for a in accounts)
            if total_supply == 0:
                return {}

            sorted_accounts = sorted(accounts, key=lambda a: float(a.get("amount", 0)), reverse=True)
            top10_supply = sum(float(a.get("amount", 0)) for a in sorted_accounts[:10])
            top10_pct = (top10_supply / total_supply) * 100

            # Dev wallet = largest single holder
            dev_pct = (float(sorted_accounts[0].get("amount", 0)) / total_supply) * 100 if sorted_accounts else 0

            return {
                "total": len(accounts),
                "top10_pct": round(top10_pct, 2),
                "dev_pct": round(dev_pct, 2),
            }
    except Exception as e:
        log.debug(f"Holder fetch error for {mint}: {e}")
        return {}


async def _fetch_token_metadata(
    session: aiohttp.ClientSession, mint: str, api_key: str
) -> dict:
    """Fetch token metadata via Helius DAS getAsset."""
    url = f"{HELIUS_RPC}/?api-key={api_key}"
    payload = {
        "jsonrpc": "2.0",
        "id": "metadata",
        "method": "getAsset",
        "params": {"id": mint},
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
    except Exception as e:
        log.debug(f"Metadata fetch error for {mint}: {e}")
        return {}

from __future__ import annotations

import asyncio
import json

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
_CACHE_TTL = 3600  # 1 hour — RugCheck safety data changes slowly

log = logger.bind(component="scanner.rugcheck")

# Map risk name keywords (lowercase) → TokenCandidate boolean field
_RISK_FLAG_MAP = {
    "mint":     "is_mintable",
    "freeze":   "is_freezable",
    "honeypot": "is_honeypot",
    "bundled":  "bundled_supply",
    "rug":      "dev_rug_history",
}


async def fetch_rugcheck_safety(
    session: aiohttp.ClientSession,
    address: str,
    redis=None,
) -> dict | None:
    """
    Fetch RugCheck summary report for a Solana token.
    Returns parsed safety dict or None on failure/not indexed yet.
    Rate limit: ~30 RPM (free, no API key). Results cached in Redis for 1h.
    """
    # Cache hit — skip API call entirely
    if redis is not None:
        cache_key = f"rugcheck:{address}"
        try:
            cached = await redis.get(cache_key)
            if cached:
                log.debug(f"RugCheck cache hit for {address[:8]}...")
                return json.loads(cached)
        except Exception:
            pass  # cache failure → proceed to API

    url = f"{RUGCHECK_BASE}/tokens/{address}/report/summary"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                return None
            if resp.status == 429:
                log.debug("RugCheck rate limited — will retry on next token")
                return None
            if resp.status != 200:
                log.debug(f"RugCheck {resp.status} for {address[:8]}...")
                return None
            data = await resp.json()
            result = _parse_report(data)

            # Store in Redis — only cache successful results (not 404/429)
            if redis is not None and result is not None:
                try:
                    await redis.setex(cache_key, _CACHE_TTL, json.dumps(result))
                except Exception:
                    pass  # cache write failure is non-fatal

            return result
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.debug(f"RugCheck error for {address[:8]}...: {e}")
        return None


def _parse_report(data: dict) -> dict:
    # score_normalised: 0-100, higher = more risky → invert for safety score
    score_normalised = float(data.get("score_normalised") or 0)
    safety_score = max(0.0, 100.0 - score_normalised)

    flags = {f: False for f in _RISK_FLAG_MAP.values()}
    for risk in data.get("risks", []):
        name_lower = risk.get("name", "").lower()
        level = risk.get("level", "")
        if level in ("danger", "warn"):
            for keyword, field in _RISK_FLAG_MAP.items():
                if keyword in name_lower:
                    flags[field] = True

    return {"safety_score": safety_score, **flags}


def enrich_from_rugcheck(token: TokenCandidate, rc_data: dict) -> TokenCandidate:
    """Apply RugCheck safety data to a TokenCandidate."""
    # Safety score: only set if no GMGN data already (GMGN takes priority)
    if token.gmgn_safety_score == 0:
        token.gmgn_safety_score = rc_data["safety_score"]
    # Boolean flags: additive — any True signal wins
    token.is_mintable    = token.is_mintable    or rc_data["is_mintable"]
    token.is_freezable   = token.is_freezable   or rc_data["is_freezable"]
    token.is_honeypot    = token.is_honeypot    or rc_data["is_honeypot"]
    token.bundled_supply = token.bundled_supply or rc_data["bundled_supply"]
    token.dev_rug_history = token.dev_rug_history or rc_data["dev_rug_history"]

    if "rugcheck" not in token.sources:
        token.sources.append("rugcheck")
    return token

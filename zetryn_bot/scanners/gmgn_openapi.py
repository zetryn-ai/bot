"""
GMGN OpenAPI client (openapi.gmgn.ai) — entity-labeled smart money, token info, security.

Auth: "exist" mode — X-APIKEY header + `timestamp` (unix seconds) and `client_id`
(UUID) query params. No request signing is needed for read endpoints; the private
key is only required for swap/order routes (we execute via Jupiter, so it is unused).

Response envelope: {"code": 0, "data": ...}. code != 0 means an API-level error.
"""
from __future__ import annotations

import os
import time
import uuid

import aiohttp
from curl_cffi.requests import AsyncSession as CurlSession
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

GMGN_OPENAPI_HOST = os.environ.get("GMGN_API_HOST", "https://openapi.gmgn.ai").rstrip("/")
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_CACHE_TTL_SEC = 90.0   # token info doesn't change every second; reuse across scanners
_DEFAULT_COOLDOWN_SEC = 10.0  # fallback when 429 lacks a reset header

# Module-level shared state. Free-tier GMGN has a strict per-second cap; bursty
# batch enrichment (30 tokens × 2 endpoints) flooded the limit on first live run,
# yielding 0 successful calls. We respond by (a) caching responses by mint and
# (b) honouring 429 reset headers globally so we stop hammering during cooldown.
_cache: dict[tuple[str, str], tuple[float, dict | list]] = {}
_cooldown_until: float = 0.0  # monotonic; while time.monotonic() < this, skip all calls

log = logger.bind(component="scanner.gmgn_openapi")


def _is_cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def _enter_cooldown(reset_unix_str: str | None) -> None:
    global _cooldown_until
    delay = _DEFAULT_COOLDOWN_SEC
    if reset_unix_str:
        try:
            delay = max(0.5, float(reset_unix_str) - time.time() + 0.5)
        except (TypeError, ValueError):
            pass
    target = time.monotonic() + delay
    # Only log on transitions out of cooldown into a new (longer) one — avoids 540-line spam.
    was_active = _cooldown_until > time.monotonic()
    _cooldown_until = max(_cooldown_until, target)
    if not was_active:
        log.warning(f"GMGN OpenAPI rate limited — backing off {delay:.1f}s")


def _cache_key(sub_path: str, query: dict) -> tuple[str, str]:
    return (sub_path, str(query.get("address", "")))


async def _get(
    session: aiohttp.ClientSession,
    api_key: str,
    sub_path: str,
    query: dict,
) -> dict | list | None:
    """Exist-auth GET against the GMGN OpenAPI. Returns `data` payload or None.

    Short-circuits during rate-limit cooldown; serves from in-memory cache when fresh.
    """
    # Serve cache first — saves the request entirely
    key = _cache_key(sub_path, query)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    # Honour active cooldown — skip silently rather than hammer (and re-extend) the ban
    if _is_cooling_down():
        return None

    params = dict(query)
    params["timestamp"] = int(time.time())
    params["client_id"] = str(uuid.uuid4())
    headers = {"X-APIKEY": api_key, "User-Agent": _USER_AGENT}
    try:
        # curl_cffi impersonates Chrome TLS fingerprint — bypasses Cloudflare Bot Fight Mode.
        # aiohttp's TLS fingerprint is recognized as a bot and returns 403.
        async with CurlSession(impersonate="chrome120") as curl:
            resp = await curl.get(
                f"{GMGN_OPENAPI_HOST}{sub_path}",
                params=params,
                headers=headers,
                timeout=8,
            )
            if resp.status_code == 429:
                _enter_cooldown(resp.headers.get("x-ratelimit-reset"))
                return None
            if resp.status_code != 200:
                log.debug(f"GMGN OpenAPI {sub_path} HTTP {resp.status_code}")
                return None
            body = resp.json()
            if body.get("code") != 0:
                log.debug(f"GMGN OpenAPI {sub_path} code={body.get('code')} error={body.get('error')}")
                return None
            data = body.get("data")
            if data is not None:
                _cache[key] = (time.time() + _CACHE_TTL_SEC, data)
            return data
    except Exception as e:
        log.debug(f"GMGN OpenAPI {sub_path} error: {type(e).__name__}: {e}")
        return None


async def fetch_gmgn_token_info(
    session: aiohttp.ClientSession,
    api_key: str,
    address: str,
    chain: str = "sol",
) -> dict | None:
    """GET /v1/token/info — fundamentals + wallet_tags_stat (entity-labeled wallet counts)."""
    data = await _get(session, api_key, "/v1/token/info", {"chain": chain, "address": address})
    return data if isinstance(data, dict) else None


async def fetch_gmgn_security(
    session: aiohttp.ClientSession,
    api_key: str,
    address: str,
    chain: str = "sol",
) -> dict | None:
    """GET /v1/token/security — honeypot, mint/freeze authority, taxes, holder concentration."""
    data = await _get(session, api_key, "/v1/token/security", {"chain": chain, "address": address})
    return data if isinstance(data, dict) else None


def _safety_score_from_security(sec: dict) -> float:
    """Derive a 0-100 safety score (higher = safer) from GMGN security flags.

    Mirrors RugCheck semantics so it can populate the same `gmgn_safety_score` field.
    """
    if bool(sec.get("honeypot")) or bool(sec.get("is_honeypot")):
        return 0.0
    score = 100.0
    # Mint / freeze authority still live = upgradeable rug surface
    if not sec.get("renounced_mint"):
        score -= 25
    if not sec.get("renounced_freeze_account"):
        score -= 15
    # Trading taxes
    try:
        buy_tax = float(sec.get("buy_tax", 0) or 0)
        sell_tax = float(sec.get("sell_tax", 0) or 0)
    except (TypeError, ValueError):
        buy_tax = sell_tax = 0.0
    if max(buy_tax, sell_tax) >= 0.10:
        score -= 20
    elif max(buy_tax, sell_tax) > 0:
        score -= 5
    # Holder concentration
    try:
        top10 = float(sec.get("top_10_holder_rate", 0) or 0)
    except (TypeError, ValueError):
        top10 = 0.0
    if top10 >= 0.80:
        score -= 25
    elif top10 >= 0.60:
        score -= 10
    return max(0.0, min(100.0, score))


def enrich_from_gmgn_info(
    token: TokenCandidate,
    info: dict,
    security: dict | None = None,
) -> TokenCandidate:
    """Enrich a TokenCandidate from GMGN /v1/token/info (+ optional /v1/token/security).

    Fills entity-labeled smart-money breakdown, fundamentals (price/liquidity/holders),
    and safety signals — reducing reliance on Birdeye (smart wallets) and RugCheck (safety).
    Only fills a field when GMGN has a usable value; never overwrites good data with zero.
    """
    tags = info.get("wallet_tags_stat") or {}

    def _int(d: dict, key: str) -> int:
        try:
            return int(d.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    # Entity-labeled wallet counts (in-memory / Redis only — not DB columns)
    token.gmgn_smart_wallets = _int(tags, "smart_wallets")
    token.gmgn_kol_wallets = _int(tags, "renowned_wallets")
    token.gmgn_sniper_wallets = _int(tags, "sniper_wallets")
    token.gmgn_bundler_wallets = _int(tags, "bundler_wallets")
    token.gmgn_whale_wallets = _int(tags, "whale_wallets")

    # Headline smart-money signal -> existing field consumed by L2/L3/L4 (persists in DB)
    if token.gmgn_smart_wallets > 0:
        token.smart_wallet_buys = max(token.smart_wallet_buys, token.gmgn_smart_wallets)

    def _flt(d: dict, key: str) -> float:
        try:
            return float(d.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    # Fundamentals — only fill when missing/zero locally (never overwrite good data).
    # GMGN nests price + per-window volume/trade counts under the `price` object.
    price_obj = info.get("price") if isinstance(info.get("price"), dict) else {}
    px = _flt(price_obj, "price")
    if px > 0 and token.price_usd == 0:
        token.price_usd = px
    v5 = _flt(price_obj, "volume_5m")
    if v5 > 0 and token.volume_5m_usd == 0:
        token.volume_5m_usd = v5
    v1h = _flt(price_obj, "volume_1h")
    if v1h > 0 and token.volume_1h_usd == 0:
        token.volume_1h_usd = v1h
    buys5 = _int(price_obj, "buys_5m")
    sells5 = _int(price_obj, "sells_5m")
    if (buys5 + sells5) > 0 and token.txns_5m == 0:
        token.buys_5m = buys5
        token.sells_5m = sells5
        token.txns_5m = buys5 + sells5

    liq = _flt(info, "liquidity")
    if liq > 0 and token.liquidity_usd == 0:
        token.liquidity_usd = liq
    hc = _int(info, "holder_count")
    if hc > 0 and token.holder_count == 0:
        token.holder_count = hc

    # Holder distribution from GMGN stat (fractions 0-1 -> percent). Filling dev_wallet_pct
    # here keeps the dev-behavior signal alive even when Helius is skipped (holder_count set).
    stat = info.get("stat") or {}
    try:
        top10_rate = float(stat.get("top_10_holder_rate", 0) or 0)
        if top10_rate > 0 and token.top10_holder_pct == 0:
            token.top10_holder_pct = top10_rate * 100
    except (TypeError, ValueError):
        pass
    try:
        dev_rate = float(stat.get("dev_team_hold_rate", 0) or 0) or float(stat.get("creator_hold_rate", 0) or 0)
        if dev_rate > 0 and token.dev_wallet_pct == 0:
            token.dev_wallet_pct = dev_rate * 100
    except (TypeError, ValueError):
        pass

    # Safety — prefer GMGN security; populate booleans + derived 0-100 score
    if security:
        token.is_honeypot = token.is_honeypot or bool(security.get("honeypot")) or bool(security.get("is_honeypot"))
        # Authority still live = mintable/freezable
        if security.get("renounced_mint") is not None:
            token.is_mintable = token.is_mintable or (not security.get("renounced_mint"))
        if security.get("renounced_freeze_account") is not None:
            token.is_freezable = token.is_freezable or (not security.get("renounced_freeze_account"))
        if token.gmgn_safety_score == 0:
            token.gmgn_safety_score = _safety_score_from_security(security)

    if "gmgn" not in token.sources:
        token.sources.append("gmgn")
    return token

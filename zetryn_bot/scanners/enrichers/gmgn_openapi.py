"""GMGN OpenAPI — entity-labeled smart-money + safety enrichment.

Source: https://openapi.gmgn.ai
Auth: GMGN_API_KEY (X-APIKEY header). Free tier has a strict per-second cap.
Mechanism: Two on-demand REST calls per :meth:`enrich`:

    /v1/token/info     — fundamentals + wallet_tags_stat (entity-labeled
                          wallet counts: smart / KOL / sniper / bundler / whale)
    /v1/token/security — honeypot, mint/freeze authority, trading taxes,
                          holder concentration

Transport quirk: GMGN gates aiohttp's TLS fingerprint as bot traffic and
returns 403. We impersonate Chrome via :mod:`curl_cffi` to bypass
Cloudflare Bot Fight Mode. That means the ``session`` argument passed to
:meth:`enrich` is **unused** — we open our own :class:`CurlSession`. The
arg is kept only to satisfy the :class:`TokenEnricher` Protocol.

Rate limits: A module-level cooldown + 90-second response cache. The
cooldown is shared across all :class:`GmgnEnricher` instances because
GMGN's limits are global per key, not per HTTP client. Don't try to
work around this by spinning up many enricher instances.

Populates: gmgn_smart_wallets / gmgn_kol_wallets / gmgn_sniper_wallets /
gmgn_bundler_wallets / gmgn_whale_wallets; price_usd / volume_5m_usd /
volume_1h_usd / buys_5m / sells_5m / txns_5m / liquidity_usd /
holder_count / top10_holder_pct / dev_wallet_pct (only when missing);
is_honeypot / is_mintable / is_freezable / gmgn_safety_score (safety).
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
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CACHE_TTL_SEC = 90.0
_DEFAULT_COOLDOWN_SEC = 10.0

# Module-level shared state. GMGN's free tier has a strict per-second cap;
# bursty batch enrichment used to flood the limit. The shared cache + cooldown
# protect downstream callers from runaway 429 spirals.
_cache: dict[tuple[str, str], tuple[float, dict | list]] = {}
_cooldown_until: float = 0.0


class GmgnEnricher:
    """On-demand enricher backed by GMGN's OpenAPI (info + security endpoints).

    Construction takes an API key string. The ``session`` arg passed to
    :meth:`enrich` is ignored — see the module docstring for the TLS
    impersonation rationale.
    """

    name = "gmgn"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._log = logger.bind(component=self.name)

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        if not self._api_key:
            return candidate

        info = await self._fetch_token_info(mint)
        if info is None:
            return candidate
        security = await self._fetch_security(mint)

        return _apply(candidate, info, security)

    async def _fetch_token_info(self, mint: str) -> dict | None:
        data = await self._get("/v1/token/info", {"chain": "sol", "address": mint})
        return data if isinstance(data, dict) else None

    async def _fetch_security(self, mint: str) -> dict | None:
        data = await self._get("/v1/token/security", {"chain": "sol", "address": mint})
        return data if isinstance(data, dict) else None

    async def _get(self, sub_path: str, query: dict) -> dict | list | None:
        """Authenticated GET with module-level cache + cooldown handling."""
        key = (sub_path, str(query.get("address", "")))
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]

        if _is_cooling_down():
            return None

        params = dict(query)
        params["timestamp"] = int(time.time())
        params["client_id"] = str(uuid.uuid4())
        headers = {"X-APIKEY": self._api_key, "User-Agent": _USER_AGENT}

        try:
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
                    self._log.debug(f"{sub_path} HTTP {resp.status_code}")
                    return None
                body = resp.json()
                if body.get("code") != 0:
                    self._log.debug(f"{sub_path} code={body.get('code')} error={body.get('error')}")
                    return None
                data = body.get("data")
                if data is not None:
                    _cache[key] = (time.time() + _CACHE_TTL_SEC, data)
                return data
        except Exception as exc:
            self._log.debug(f"{sub_path} error: {type(exc).__name__}: {exc}")
            return None


# ──────────────────────────────────────────────────────────────────────────
# Module-level cooldown helpers (shared state across enricher instances)
# ──────────────────────────────────────────────────────────────────────────


def _is_cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def _enter_cooldown(reset_unix_str: str | None) -> None:
    """Globally back off until ``reset_unix_str`` (or a default delay)."""
    global _cooldown_until
    delay = _DEFAULT_COOLDOWN_SEC
    if reset_unix_str:
        try:
            delay = max(0.5, float(reset_unix_str) - time.time() + 0.5)
        except (TypeError, ValueError):
            pass
    target = time.monotonic() + delay
    was_active = _cooldown_until > time.monotonic()
    _cooldown_until = max(_cooldown_until, target)
    if not was_active:
        logger.bind(component="gmgn").warning(f"rate limited — backing off {delay:.1f}s")


# ──────────────────────────────────────────────────────────────────────────
# Token-candidate enrichment (pure data transform)
# ──────────────────────────────────────────────────────────────────────────


def _apply(candidate: TokenCandidate, info: dict, security: dict | None) -> TokenCandidate:
    """Return ``candidate`` with GMGN info + security fields applied."""
    updates: dict = {}

    # Entity-labeled wallet counts (in-memory only — not DB columns).
    tags = info.get("wallet_tags_stat") or {}
    updates["gmgn_smart_wallets"] = _i(tags, "smart_wallets")
    updates["gmgn_kol_wallets"] = _i(tags, "renowned_wallets")
    updates["gmgn_sniper_wallets"] = _i(tags, "sniper_wallets")
    updates["gmgn_bundler_wallets"] = _i(tags, "bundler_wallets")
    updates["gmgn_whale_wallets"] = _i(tags, "whale_wallets")

    # Headline smart-money signal.
    smart_now = updates["gmgn_smart_wallets"]
    if smart_now > 0:
        updates["smart_wallet_buys"] = max(candidate.smart_wallet_buys, smart_now)

    # Fundamentals — only fill when missing locally (never overwrite good data).
    price_obj = info.get("price") if isinstance(info.get("price"), dict) else {}
    px = _f(price_obj, "price")
    if px > 0 and candidate.price_usd == 0:
        updates["price_usd"] = px
    v5 = _f(price_obj, "volume_5m")
    if v5 > 0 and candidate.volume_5m_usd == 0:
        updates["volume_5m_usd"] = v5
    v1h = _f(price_obj, "volume_1h")
    if v1h > 0 and candidate.volume_1h_usd == 0:
        updates["volume_1h_usd"] = v1h
    buys5 = _i(price_obj, "buys_5m")
    sells5 = _i(price_obj, "sells_5m")
    if (buys5 + sells5) > 0 and candidate.txns_5m == 0:
        updates["buys_5m"] = buys5
        updates["sells_5m"] = sells5
        updates["txns_5m"] = buys5 + sells5

    liq = _f(info, "liquidity")
    if liq > 0 and candidate.liquidity_usd == 0:
        updates["liquidity_usd"] = liq
    hc = _i(info, "holder_count")
    if hc > 0 and candidate.holder_count == 0:
        updates["holder_count"] = hc

    # Holder distribution.
    stat = info.get("stat") or {}
    top10_rate = _f(stat, "top_10_holder_rate")
    if top10_rate > 0 and candidate.top10_holder_pct == 0:
        updates["top10_holder_pct"] = top10_rate * 100
    dev_rate = _f(stat, "dev_team_hold_rate") or _f(stat, "creator_hold_rate")
    if dev_rate > 0 and candidate.dev_wallet_pct == 0:
        updates["dev_wallet_pct"] = dev_rate * 100

    # Safety — prefer GMGN security; populate booleans + derived 0-100 score.
    if security:
        is_honeypot = (
            candidate.is_honeypot
            or bool(security.get("honeypot"))
            or bool(security.get("is_honeypot"))
        )
        if is_honeypot != candidate.is_honeypot:
            updates["is_honeypot"] = is_honeypot

        if security.get("renounced_mint") is not None:
            mintable = candidate.is_mintable or (not security.get("renounced_mint"))
            if mintable != candidate.is_mintable:
                updates["is_mintable"] = mintable
        if security.get("renounced_freeze_account") is not None:
            freezable = candidate.is_freezable or (not security.get("renounced_freeze_account"))
            if freezable != candidate.is_freezable:
                updates["is_freezable"] = freezable

        if candidate.gmgn_safety_score == 0:
            updates["gmgn_safety_score"] = _safety_score_from_security(security)

    sources = list(candidate.sources)
    if "gmgn" not in sources:
        sources.append("gmgn")
    updates["sources"] = sources

    return candidate.model_copy(update=updates)


def _safety_score_from_security(sec: dict) -> float:
    """Derive a 0-100 safety score (higher = safer) from GMGN security flags."""
    if bool(sec.get("honeypot")) or bool(sec.get("is_honeypot")):
        return 0.0
    score = 100.0
    if not sec.get("renounced_mint"):
        score -= 25
    if not sec.get("renounced_freeze_account"):
        score -= 15
    try:
        buy_tax = float(sec.get("buy_tax", 0) or 0)
        sell_tax = float(sec.get("sell_tax", 0) or 0)
    except (TypeError, ValueError):
        buy_tax = sell_tax = 0.0
    if max(buy_tax, sell_tax) >= 0.10:
        score -= 20
    elif max(buy_tax, sell_tax) > 0:
        score -= 5
    try:
        top10 = float(sec.get("top_10_holder_rate", 0) or 0)
    except (TypeError, ValueError):
        top10 = 0.0
    if top10 >= 0.80:
        score -= 25
    elif top10 >= 0.60:
        score -= 10
    return max(0.0, min(100.0, score))


def _i(d: dict, key: str) -> int:
    try:
        return int(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["GmgnEnricher"]

"""Rugcheck — safety analysis enrichment.

Source: https://api.rugcheck.xyz
Auth: None (public API)
Mechanism: On-demand REST lookup of a token's safety report summary
Rate limits: ~30 RPM (free, no API key). Results cached in Redis for 1h
    when a Redis client is provided.
Populates: gmgn_safety_score (inverted from rugcheck's risk score, only
    if not already populated by GMGN), is_mintable, is_freezable,
    is_honeypot, bundled_supply, dev_rug_history (additive — any True
    wins).
"""

from __future__ import annotations

import asyncio
import json
import time

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
_CACHE_TTL = 3600  # 1 hour — Rugcheck safety data changes slowly

# Map risk-name keywords (lowercase substring) → TokenCandidate field name
_RISK_FLAG_MAP = {
    "mint": "is_mintable",
    "freeze": "is_freezable",
    "honeypot": "is_honeypot",
    "bundled": "bundled_supply",
    "rug": "dev_rug_history",
}


class RugcheckEnricher:
    """On-demand safety enricher backed by RugCheck.xyz.

    A single REST call per :meth:`enrich` invocation. The endpoint
    returns a normalised risk score (0-100, higher = riskier) plus a
    list of named risks — translated here into ``gmgn_safety_score``
    (inverted to 0-100 where higher = safer, for consistency with GMGN)
    and a set of boolean flags on the candidate.

    Optional Redis-backed result caching (1 hour TTL): pass a
    :class:`redis.asyncio.Redis` instance to the constructor; the
    enricher will read/write per-mint cache entries to avoid hammering
    RugCheck's tight rate limits.
    """

    name = "rugcheck"

    def __init__(self, redis=None) -> None:
        """Construct an enricher with optional Redis caching.

        Args:
            redis: Optional :class:`redis.asyncio.Redis` client. When
                provided, results are cached per mint for one hour.
                Cache failures are non-fatal — the enricher falls back
                to a fresh API call.
        """
        self._redis = redis
        self._log = logger.bind(component=self.name)
        # In-process fallback cache (mint -> (monotonic_ts, parsed data)).
        # Production runs WITHOUT Redis, and trending sources re-emit the same
        # mints every few minutes — each re-analysis was a fresh call against
        # RugCheck's ~30 RPM public limit, so coverage flapped and the
        # fail-closed buy gate randomly blocked known-good tokens (observed
        # 2026-07-12: POINTLESS conf 0.76 blocked repeatedly; it did +1194%).
        self._local_cache: dict[str, tuple[float, dict]] = {}

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        """Return ``candidate`` enriched with RugCheck flags, or unchanged on failure."""
        rc_data = await self._fetch(session, mint)
        if rc_data is None:
            return candidate

        updates: dict = {}

        # Safety score: only fill if not already set by GMGN.
        if candidate.gmgn_safety_score == 0:
            updates["gmgn_safety_score"] = rc_data["safety_score"]

        # Boolean flags are additive — any True signal wins.
        for field in (
            "is_mintable",
            "is_freezable",
            "is_honeypot",
            "bundled_supply",
            "dev_rug_history",
        ):
            current = getattr(candidate, field)
            if rc_data.get(field) and not current:
                updates[field] = True

        sources = list(candidate.sources)
        if "rugcheck" not in sources:
            sources.append("rugcheck")
        updates["sources"] = sources

        return candidate.model_copy(update=updates)

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        mint: str,
    ) -> dict | None:
        """Fetch parsed RugCheck data, with local + Redis cache checks first."""
        cache_key = f"rugcheck:{mint}"

        local = self._local_cache.get(mint)
        if local is not None and time.monotonic() - local[0] < _CACHE_TTL:
            return local[1]

        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    self._log.debug(f"cache hit for {mint[:8]}...")
                    return json.loads(cached)
            except Exception:
                pass

        url = f"{RUGCHECK_BASE}/tokens/{mint}/report/summary"
        result = None
        try:
            # 429 gets two bounded retries (1s, 2s): a missing RugCheck report
            # means the candidate is evaluated with NO contract-safety data
            # (fail-open), so a transient rate limit is worth a short wait.
            # The final failure is a WARNING — "rate limited" flows to the M7
            # log bridge → Telegram (deduped) instead of vanishing at debug.
            for attempt in range(3):
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 404:
                        return None
                    if resp.status == 429:
                        if attempt < 2:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        self._log.warning(
                            "RugCheck rate limited (3 attempts) — {} evaluated without "
                            "contract-safety data",
                            mint[:8],
                        )
                        return None
                    if resp.status != 200:
                        self._log.debug(f"status {resp.status} for {mint[:8]}...")
                        return None
                    data = await resp.json()
                    result = _parse_report(data)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.debug(f"fetch error for {mint[:8]}...: {exc}")
            return None
        if result is None:
            return None

        # Cache successful parses only.
        if len(self._local_cache) > 4096:  # bounded: drop oldest insertion
            self._local_cache.pop(next(iter(self._local_cache)))
        self._local_cache[mint] = (time.monotonic(), result)
        if self._redis is not None:
            try:
                await self._redis.setex(cache_key, _CACHE_TTL, json.dumps(result))
            except Exception:
                pass

        return result


def _parse_report(data: dict) -> dict:
    """Translate a RugCheck report summary into safety_score + risk flags.

    ``score_normalised`` from the API is 0-100 with higher = more risky;
    we invert to give a 0-100 score where higher = safer (matches GMGN's
    convention).
    """
    score_normalised = float(data.get("score_normalised") or 0)
    safety_score = max(0.0, 100.0 - score_normalised)

    flags = {field: False for field in _RISK_FLAG_MAP.values()}
    for risk in data.get("risks", []):
        name_lower = risk.get("name", "").lower()
        level = risk.get("level", "")
        if level in ("danger", "warn"):
            for keyword, field in _RISK_FLAG_MAP.items():
                if keyword in name_lower:
                    flags[field] = True

    return {"safety_score": safety_score, **flags}


__all__ = ["RugcheckEnricher"]

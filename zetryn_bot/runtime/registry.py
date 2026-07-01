"""Config → instance wiring for scanners and enrichers.

The single place that reads :class:`~zetryn_bot.config.Settings` and decides
which sources run. Contract (per M3 design doc, decision #3):

- Zero-arg scanners always run.
- Key-requiring scanners run only when their key/config is present; otherwise
  they are skipped with a warning (mirrors ``config.py``'s "scanners skipped
  at runtime when the keys they need are missing").
- ``SCANNERS_ENABLED`` (a list of scanner ``.name``s) narrows the set when set.
"""

from __future__ import annotations

from loguru import logger

from zetryn_bot.config import Settings
from zetryn_bot.scanners.birdeye import BirdeyeNewListing, BirdeyeTrending
from zetryn_bot.scanners.dexscreener import (
    DexscreenerBoost,
    DexscreenerNewPairs,
    DexscreenerTrending,
)
from zetryn_bot.scanners.enrichers.gmgn_openapi import GmgnEnricher
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher
from zetryn_bot.scanners.enrichers.jupiter import JupiterEnricher
from zetryn_bot.scanners.enrichers.rugcheck import RugcheckEnricher
from zetryn_bot.scanners.geckoterminal import (
    GeckoTerminalNewPools,
    GeckoTerminalTrending,
)
from zetryn_bot.scanners.protocol import Scanner, TokenEnricher
from zetryn_bot.scanners.pumpfun import PumpfunStream
from zetryn_bot.scanners.raydium import RaydiumNewPools
from zetryn_bot.scanners.telegram import TelegramScanner, build_channels_from_config
from zetryn_bot.utils.key_pool import BirdeyeKeyPool

log = logger.bind(component="runtime.registry")


def build_enabled_scanners(settings: Settings) -> list[Scanner]:
    """Build the list of scanner instances enabled by ``settings``.

    Zero-arg scanners are always included. Key-requiring scanners are added
    only when their key/config is present. If ``settings.scanners_enabled`` is
    non-empty, the result is filtered to those ``.name``s.
    """
    scanners: list[Scanner] = [
        DexscreenerNewPairs(),
        DexscreenerTrending(),
        DexscreenerBoost(),
        GeckoTerminalNewPools(),
        GeckoTerminalTrending(),
        RaydiumNewPools(),
    ]

    if settings.birdeye_api_keys:
        pool = BirdeyeKeyPool(settings.birdeye_api_keys)
        scanners += [BirdeyeTrending(pool), BirdeyeNewListing(pool)]
    else:
        log.warning("birdeye scanners skipped — BIRDEYE_API_KEYS empty")

    if settings.pumpportal_api_key:
        scanners.append(PumpfunStream(settings.pumpportal_api_key))
    else:
        log.warning("pumpfun scanner skipped — PUMPPORTAL_API_KEY empty")

    if settings.telegram_api_id and settings.telegram_api_hash:
        channels = build_channels_from_config(settings)
        if channels:
            scanners.append(
                TelegramScanner(
                    settings.telegram_api_id,
                    settings.telegram_api_hash,
                    settings.telegram_session_path,
                    channels,
                )
            )
        else:
            log.warning("telegram scanner skipped — TELEGRAM_CHANNELS empty")
    else:
        log.warning("telegram scanner skipped — TELEGRAM_API_ID / TELEGRAM_API_HASH empty")

    if settings.scanners_enabled:
        wanted = set(settings.scanners_enabled)
        scanners = [s for s in scanners if s.name in wanted]
        log.info("SCANNERS_ENABLED filter applied → {} scanner(s)", len(scanners))

    log.info("enabled scanners: {}", [s.name for s in scanners])
    return scanners


def build_enrichers(settings: Settings) -> list[TokenEnricher]:
    """Build the enricher chain enabled by ``settings``.

    Order matters (per M2 design doc §5): Helius fills holders/metadata first,
    then Rugcheck, GMGN, and Jupiter (price fallback). The Twitter enricher is
    deferred in M3 — it needs an async ``TwitterAccountPool.initialize()`` and
    cookie files, which the synchronous registry can't wire cleanly.
    """
    enrichers: list[TokenEnricher] = []

    if settings.helius_api_keys:
        enrichers.append(HeliusEnricher(settings.helius_api_keys))
    else:
        log.warning("helius enricher skipped — HELIUS_API_KEYS empty")

    # Rugcheck: no key required (optional Redis cache; None disables caching).
    enrichers.append(RugcheckEnricher())

    if settings.gmgn_api_key:
        enrichers.append(GmgnEnricher(settings.gmgn_api_key))
    else:
        log.warning("gmgn enricher skipped — GMGN_API_KEY empty")

    # Jupiter: zero-arg price fallback, runs last among the wired enrichers.
    enrichers.append(JupiterEnricher())

    log.info("enabled enrichers: {}", [e.name for e in enrichers])
    return enrichers

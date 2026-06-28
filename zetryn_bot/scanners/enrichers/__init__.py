"""On-demand enricher implementations.

Each module in this package implements
:class:`zetryn_bot.scanners.protocol.TokenEnricher` — a different protocol
from the streaming :class:`Scanner` used by the top-level
:mod:`zetryn_bot.scanners` modules. Enrichers take a mint address plus a
partial :class:`TokenCandidate` and return an immutable enriched copy.

Available enrichers:

- :class:`.helius.HeliusEnricher` — holder distribution + token metadata
  via Helius DAS API.
- :class:`.rugcheck.RugcheckEnricher` — safety analysis (mint/freeze
  authority, honeypot, bundled supply, dev rug history).
- :class:`.jupiter.JupiterEnricher` — token price lookup (fallback price
  source when DEX scanners don't supply one).
- :class:`.gmgn_openapi.GmgnEnricher` — entity-labeled smart-money +
  safety from GMGN OpenAPI.
- :class:`.twitter.TwitterEnricher` — social-signal aggregation via
  twitter_login (cookie-based, no API key).
"""

from __future__ import annotations

from zetryn_bot.scanners.enrichers.gmgn_openapi import GmgnEnricher
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher
from zetryn_bot.scanners.enrichers.jupiter import JupiterEnricher
from zetryn_bot.scanners.enrichers.rugcheck import RugcheckEnricher
from zetryn_bot.scanners.enrichers.twitter import (
    TwitterAccountPool,
    TwitterEnricher,
    build_twitter_pool_from_config,
)

__all__ = [
    "GmgnEnricher",
    "HeliusEnricher",
    "JupiterEnricher",
    "RugcheckEnricher",
    "TwitterAccountPool",
    "TwitterEnricher",
    "build_twitter_pool_from_config",
]

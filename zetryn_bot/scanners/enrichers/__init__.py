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
"""

from __future__ import annotations

from zetryn_bot.scanners.enrichers.helius import HeliusEnricher
from zetryn_bot.scanners.enrichers.rugcheck import RugcheckEnricher

__all__ = ["HeliusEnricher", "RugcheckEnricher"]

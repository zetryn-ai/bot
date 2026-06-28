"""Scanner sources for the bot template.

Two Protocols, two patterns:

- :class:`Scanner` — continuous source. Implementations live in the
  top-level scanner modules (:mod:`.birdeye`, :mod:`.dexscreener`, etc.).
  Caller pattern::

      async for candidate in DexscreenerNewPairs().stream(session):
          await sink(candidate)

- :class:`TokenEnricher` — on-demand lookup. Implementations live in
  :mod:`.enrichers`. Caller pattern::

      enriched = await HeliusEnricher(keys).enrich(mint, candidate, session)
"""

from __future__ import annotations

from zetryn_bot.scanners.protocol import Scanner, TokenEnricher

__all__ = ["Scanner", "TokenEnricher"]

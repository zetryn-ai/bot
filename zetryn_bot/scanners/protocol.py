"""Scanner and TokenEnricher Protocols.

Two contracts every source in this package implements:

- :class:`Scanner` — a continuous source that yields :class:`TokenCandidate`
  values over time (polling, WebSocket streaming, social-feed scraping).
- :class:`TokenEnricher` — an on-demand lookup that takes a mint address
  plus a partial :class:`TokenCandidate` and returns an enriched copy.

Both are runtime-checkable :class:`typing.Protocol`s — implementers don't
need to inherit from them; the engine just duck-types on the method
signatures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import aiohttp

from zetryn_bot.models.token import TokenCandidate


@runtime_checkable
class Scanner(Protocol):
    """A source of token candidates.

    Implementations yield :class:`TokenCandidate` values as they are
    discovered (polling), streamed (WebSocket), or surfaced from social
    feeds. The caller decides what to do with each candidate — publish to
    Redis, feed into a ``zetryn-trading`` agent, log, filter, etc.

    Contract:

    - Implementations must not call any sink (Redis, network) other than
      the source they scan. No direct publishing.
    - Implementations must be cancellable: ``async for`` callers can
      break out at any time; the scanner must release HTTP / WebSocket
      / DB resources cleanly via context managers or ``finally`` blocks.
    - Implementations must handle transient errors gracefully — log and
      continue the loop; don't propagate one-off HTTP failures to the
      caller.
    - Implementations must sleep between polls when polling; use
      :func:`asyncio.sleep`, not blocking sleep.
    """

    name: str
    """Stable identifier, used in logs and supervision (e.g. ``"dexscreener.new_pairs"``)."""

    def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        """Yield :class:`TokenCandidate` values indefinitely.

        Args:
            session: A shared :class:`aiohttp.ClientSession`. The scanner
                must not close this session — the caller owns its
                lifecycle.

        Yields:
            One :class:`TokenCandidate` per discovered token. Duplicate
            handling within a single scanner is the scanner's
            responsibility; cross-scanner dedup is the caller's.
        """
        ...


@runtime_checkable
class TokenEnricher(Protocol):
    """An on-demand token-detail lookup.

    Unlike :class:`Scanner`, an enricher does not stream candidates over
    time — it takes a mint address and returns enriched data, used to top
    up a :class:`TokenCandidate` already obtained from a Scanner.

    Contract:

    - Implementations must treat the input ``candidate`` as immutable.
      Return a new :class:`TokenCandidate` via ``model_copy(update=...)``;
      do not mutate the argument.
    - Implementations should only populate the fields they own.
      Other fields pass through unchanged.
    - Transient errors should raise — the caller decides whether to retry,
      skip, or abort. Don't silently return a half-enriched candidate.
    """

    name: str
    """Stable identifier, used in logs (e.g. ``"helius"``)."""

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        """Return a new candidate with enriched fields populated.

        Args:
            mint: SPL mint address.
            candidate: Current :class:`TokenCandidate` for this mint. Pass
                a fresh ``TokenCandidate(address=mint)`` if you only have
                the address.
            session: Shared :class:`aiohttp.ClientSession`.

        Returns:
            A new :class:`TokenCandidate` with this enricher's fields
            populated. Other fields are passed through unchanged.

        Raises:
            Implementation-specific. Callers should expect transient HTTP
            failures and decide whether to retry, skip, or abort.
        """
        ...


__all__ = ["Scanner", "TokenEnricher"]

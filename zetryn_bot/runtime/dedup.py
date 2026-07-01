"""In-memory dedup cache for token mints seen across scanners.

The same token surfaces from several scanners within seconds (Dexscreener +
Birdeye + a social feed). Processing each copy through the pipeline (and,
with an LLM, paying for each) is wasteful. `DedupCache` collapses repeats
inside a sliding TTL window.

Intentionally simple: a single-process dict, no locking (asyncio is
single-threaded and `seen()` does not await). The clock is injectable so
tests control the window without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class DedupCache:
    """Tracks recently-seen mints within a TTL window.

    ``seen(mint)`` returns ``True`` if the mint was seen within the last
    ``ttl_s`` seconds, and otherwise records it (returning ``False``). Expired
    entries are dropped lazily on lookup and opportunistically swept when the
    map grows past ``max_entries``.
    """

    def __init__(
        self,
        ttl_s: float = 60.0,
        *,
        now_fn: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        self._ttl_s = ttl_s
        self._now = now_fn
        self._max_entries = max_entries
        self._seen: dict[str, float] = {}

    def seen(self, mint: str) -> bool:
        """Return whether ``mint`` is a duplicate within the window; record it if new."""
        now = self._now()
        last = self._seen.get(mint)
        if last is not None and (now - last) < self._ttl_s:
            # Refresh the timestamp so an actively-repeating mint stays deduped.
            self._seen[mint] = now
            return True
        self._seen[mint] = now
        if len(self._seen) > self._max_entries:
            self._sweep(now)
        return False

    def _sweep(self, now: float) -> None:
        """Drop entries older than the TTL window."""
        cutoff = now - self._ttl_s
        self._seen = {m: t for m, t in self._seen.items() if t >= cutoff}

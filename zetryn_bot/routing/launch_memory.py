"""``LaunchMemory`` — remembers pump.fun launch times to date their graduations.

The pumpfun WebSocket already delivers BOTH events: a token's launch
(``sources=["pumpfun_ws"]``) and, minutes-to-hours later, its migration to
Raydium (``sources=["pumpfun_migration"]``). The framework's graduation agent
wants ``bonding_curve_fill_seconds`` — how fast the curve filled, a strong
quality signal — which neither event carries alone. Remembering launch
timestamps bridges the two.

In-memory only by design: a restart loses pending launches, so migrations of
tokens launched before the restart report ``fill_seconds=0.0`` ("unknown", a
neutral value for the agent) rather than a wrong number.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class LaunchMemory:
    """TTL map ``mint -> launch timestamp`` (monotonic)."""

    def __init__(
        self,
        *,
        ttl_s: float = 86_400.0,
        max_entries: int = 50_000,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._now = now_fn
        self._launches: dict[str, float] = {}

    def record(self, mint: str) -> None:
        """Remember that ``mint`` launched now (first sighting wins)."""
        self._prune()
        self._launches.setdefault(mint, self._now())

    def fill_seconds(self, mint: str) -> float:
        """Seconds from recorded launch to now, or ``0.0`` if unknown/expired."""
        ts = self._launches.get(mint)
        if ts is None:
            return 0.0
        elapsed = self._now() - ts
        if elapsed > self._ttl_s:
            del self._launches[mint]
            return 0.0
        return elapsed

    def _prune(self) -> None:
        if len(self._launches) < self._max_entries:
            return
        now = self._now()
        expired = [m for m, ts in self._launches.items() if now - ts > self._ttl_s]
        for m in expired:
            del self._launches[m]
        # Still full of live entries (pathological): drop the oldest half so
        # record() never grows unbounded.
        if len(self._launches) >= self._max_entries:
            for m in sorted(self._launches, key=self._launches.get)[: self._max_entries // 2]:
                del self._launches[m]

    def __len__(self) -> int:
        return len(self._launches)

from __future__ import annotations

import asyncio
import time
from loguru import logger


class APIKeyPool:
    """
    Generic round-robin key pool with per-key RPM/RPD tracking.
    Skips keys in cooldown (429) or exhausted (RPD).
    """

    COOLDOWN_429 = 65
    COOLDOWN_RPD = 3600

    def __init__(self, keys: list[str], rpm_limit: int, rpd_limit: int, name: str = "api") -> None:
        if not keys:
            raise ValueError(f"{name} KeyPool requires at least one API key")
        self._name = name
        self._rpm_limit = rpm_limit
        self._rpd_limit = rpd_limit
        self._keys = keys
        self._index = 0
        self._lock = asyncio.Lock()

        self._cooldown_until: dict[str, float] = {k: 0.0 for k in keys}
        self._rpm_window: dict[str, list[float]] = {k: [] for k in keys}
        self._rpd_count: dict[str, int] = {k: 0 for k in keys}
        self._rpd_reset_at: float = self._next_midnight()
        self._last_unavailable_log: float = 0.0  # suppress repeated "all keys unavailable" spam

        log = logger.bind(component=f"utils.key_pool.{name}")
        log.info(f"{name} key pool initialized with {len(keys)} key(s) | RPM={rpm_limit} RPD={rpd_limit}")

    async def acquire(self) -> str | None:
        async with self._lock:
            return self._try_acquire_locked()

    def _try_acquire_locked(self) -> str | None:
        """Inner acquire — must be called with self._lock held."""
        self._maybe_reset_rpd()
        now = time.monotonic()
        log = logger.bind(component=f"utils.key_pool.{self._name}")

        for _ in range(len(self._keys)):
            key = self._keys[self._index]
            self._index = (self._index + 1) % len(self._keys)

            if now < self._cooldown_until[key]:
                remaining = self._cooldown_until[key] - now
                log.debug(f"Key ...{key[-6:]} in cooldown for {remaining:.0f}s")
                continue

            if self._rpd_count[key] >= self._rpd_limit:
                self._cooldown_until[key] = now + self.COOLDOWN_RPD
                log.error(f"Key ...{key[-6:]} RPD exhausted — cooling down 1h")
                continue

            minute_ago = now - 60
            self._rpm_window[key] = [t for t in self._rpm_window[key] if t > minute_ago]
            if len(self._rpm_window[key]) >= self._rpm_limit:
                log.debug(f"Key ...{key[-6:]} RPM full ({len(self._rpm_window[key])}/{self._rpm_limit})")
                continue

            self._rpm_window[key].append(now)
            self._rpd_count[key] += 1
            return key

        if now - self._last_unavailable_log >= 300:
            log.warning(f"All {self._name} keys unavailable (suppressing for 5m)")
            self._last_unavailable_log = now
        return None

    def _min_cooldown_remaining(self) -> float:
        """Returns seconds until the earliest key becomes available. Assumes RPD not exhausted."""
        now = time.monotonic()
        return min(
            (max(0.0, self._cooldown_until[k] - now) for k in self._keys),
            default=0.0,
        )

    async def acquire_or_wait(self, max_wait_sec: float = 30.0) -> str | None:
        """
        Like acquire(), but if all keys are in short cooldown (<= max_wait_sec),
        wait for the earliest key to become available and retry once.
        If the shortest cooldown exceeds max_wait_sec (e.g. RPD exhausted), return None.
        """
        log = logger.bind(component=f"utils.key_pool.{self._name}")

        async with self._lock:
            key = self._try_acquire_locked()
            if key is not None:
                return key

            wait = self._min_cooldown_remaining()

        if 0 < wait <= max_wait_sec:
            log.debug(f"All {self._name} keys in short cooldown — waiting {wait:.1f}s for next key")
            await asyncio.sleep(wait + 0.2)
            async with self._lock:
                return self._try_acquire_locked()

        return None

    async def mark_rate_limited(self, key: str, retry_after: int = 0) -> None:
        async with self._lock:
            cooldown = retry_after if retry_after > 0 else self.COOLDOWN_429
            self._cooldown_until[key] = time.monotonic() + cooldown
            log = logger.bind(component=f"utils.key_pool.{self._name}")
            level = "warning" if cooldown >= 3600 else "error"
            getattr(log, level)(f"Key ...{key[-6:]} rate limited — cooldown {cooldown}s")

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            key[-6:]: {
                "rpd_used": self._rpd_count[key],
                "rpd_remaining": max(0, self._rpd_limit - self._rpd_count[key]),
                "rpm_used": len([t for t in self._rpm_window[key] if t > now - 60]),
                "cooldown_remaining": max(0, self._cooldown_until[key] - now),
            }
            for key in self._keys
        }

    def _maybe_reset_rpd(self) -> None:
        now = time.time()
        if now >= self._rpd_reset_at:
            for key in self._keys:
                self._rpd_count[key] = 0
                self._cooldown_until[key] = 0.0
            self._rpd_reset_at = self._next_midnight()
            logger.bind(component=f"utils.key_pool.{self._name}").info("RPD counters reset (new day)")

    @staticmethod
    def _next_midnight() -> float:
        import datetime
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        return datetime.datetime.combine(tomorrow, datetime.time.min).timestamp()


class BirdeyeKeyPool(APIKeyPool):
    # BirdEye Starter: ~100 RPM. Set conservatively — actual limit enforced via 429 cooldown.
    def __init__(self, keys: list[str]) -> None:
        super().__init__(keys, rpm_limit=60, rpd_limit=50_000, name="birdeye")


class HeliusKeyPool(APIKeyPool):
    # Helius free RPC: 10 RPS = 600 RPM. Use 300 as conservative headroom.
    # Actual 429 responses will trigger per-key cooldown via mark_rate_limited.
    def __init__(self, keys: list[str]) -> None:
        super().__init__(keys, rpm_limit=300, rpd_limit=100_000, name="helius")


# LLM-provider key pools (Gemini, Groq, OpenRouter) intentionally removed —
# `zetryn-trading` provides `LLMRouter` + `KeyPool` for that purpose. Mixing
# scanner key pools with LLM pools here would duplicate that abstraction.
# Subclass `APIKeyPool` directly if you need another scanner-specific pool.

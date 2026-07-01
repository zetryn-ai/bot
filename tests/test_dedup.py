"""Unit tests for DedupCache — deterministic via an injected clock."""

from __future__ import annotations

from zetryn_bot.runtime.dedup import DedupCache


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_first_sighting_is_not_a_duplicate():
    cache = DedupCache(ttl_s=60.0, now_fn=_FakeClock())
    assert cache.seen("mintA") is False


def test_repeat_within_window_is_a_duplicate():
    clock = _FakeClock()
    cache = DedupCache(ttl_s=60.0, now_fn=clock)
    assert cache.seen("mintA") is False
    clock.t = 30.0
    assert cache.seen("mintA") is True


def test_repeat_after_ttl_is_fresh_again():
    clock = _FakeClock()
    cache = DedupCache(ttl_s=60.0, now_fn=clock)
    assert cache.seen("mintA") is False
    clock.t = 61.0
    assert cache.seen("mintA") is False


def test_distinct_mints_are_independent():
    cache = DedupCache(ttl_s=60.0, now_fn=_FakeClock())
    assert cache.seen("mintA") is False
    assert cache.seen("mintB") is False
    assert cache.seen("mintA") is True


def test_active_repeat_refreshes_the_window():
    clock = _FakeClock()
    cache = DedupCache(ttl_s=60.0, now_fn=clock)
    cache.seen("mintA")  # t=0
    clock.t = 50.0
    assert cache.seen("mintA") is True  # refreshes last-seen to 50
    clock.t = 100.0  # 50s after the refresh, still < ttl
    assert cache.seen("mintA") is True

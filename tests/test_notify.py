"""Unit tests for Notifier implementations: dedup logic + no-op safety."""

from __future__ import annotations

import pytest

from zetryn_bot.notify.protocol import Notifier
from zetryn_bot.notify.telegram import NullNotifier, TelegramNotifier


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.mark.asyncio
async def test_null_notifier_is_a_true_noop():
    notifier = NullNotifier()
    await notifier.notify("hello")
    await notifier.notify("hello", dedup_key="x")
    assert isinstance(notifier, Notifier)


@pytest.mark.asyncio
async def test_telegram_notifier_satisfies_protocol():
    assert isinstance(TelegramNotifier("token", "chat"), Notifier)


def test_dedup_none_always_allows_send():
    clock = _FakeClock()
    notifier = TelegramNotifier("token", "chat", now_fn=clock)
    assert notifier._should_send(None) is True
    assert notifier._should_send(None) is True  # no dedup_key -> never throttled


def test_dedup_key_collapses_within_window():
    clock = _FakeClock()
    notifier = TelegramNotifier("token", "chat", dedup_window_s=900.0, now_fn=clock)

    assert notifier._should_send("rate-limit:raydium") is True
    assert notifier._should_send("rate-limit:raydium") is False  # same window

    clock.t += 899.0
    assert notifier._should_send("rate-limit:raydium") is False  # still inside window

    clock.t += 2.0
    assert notifier._should_send("rate-limit:raydium") is True  # window elapsed


def test_dedup_keys_are_independent():
    clock = _FakeClock()
    notifier = TelegramNotifier("token", "chat", now_fn=clock)

    assert notifier._should_send("error:db") is True
    assert notifier._should_send("error:wallet") is True  # distinct key, not throttled
    assert notifier._should_send("error:db") is False

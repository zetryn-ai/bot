"""Unit tests for the ERROR+/WARNING log_bridge sinks."""

from __future__ import annotations

import asyncio

import pytest
from loguru import logger

from zetryn_bot.notify.log_bridge import install_log_bridge

log = logger.bind(component="test.log_bridge")


class _ListNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None]] = []

    async def notify(self, text: str, *, dedup_key: str | None = None) -> None:
        self.messages.append((text, dedup_key))


@pytest.mark.asyncio
async def test_error_always_forwarded():
    notifier = _ListNotifier()
    install_log_bridge(notifier)
    log.error("database unreachable")
    await asyncio.sleep(0.05)  # let the async loguru sink flush
    assert any("database unreachable" in m for m, _ in notifier.messages)


@pytest.mark.asyncio
async def test_warning_forwarded_only_when_rate_limit_or_rotation_keyword():
    notifier = _ListNotifier()
    install_log_bridge(notifier)
    log.warning("this is a routine notice, nothing special")
    log.warning("Raydium rate-limited — backing off")
    await asyncio.sleep(0.05)
    texts = [m for m, _ in notifier.messages]
    assert not any("routine notice" in t for t in texts)
    assert any("rate-limited" in t for t in texts)


@pytest.mark.asyncio
async def test_info_never_forwarded():
    notifier = _ListNotifier()
    install_log_bridge(notifier)
    log.info("scan complete, 5 candidates")
    await asyncio.sleep(0.05)
    assert notifier.messages == []

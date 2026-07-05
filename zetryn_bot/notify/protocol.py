"""``Notifier`` Protocol — a single async method, so any channel can implement it.

Mirrors the ``DecisionSink``/``Executor`` Protocol pattern: callers depend only
on this shape, never on ``TelegramNotifier`` directly, so a Discord/Slack
notifier could be added later without touching call sites.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Push one message. Implementations must never raise into the caller."""

    async def notify(self, text: str, *, dedup_key: str | None = None) -> None: ...

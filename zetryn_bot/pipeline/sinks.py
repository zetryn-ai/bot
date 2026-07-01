"""Where a ``Decision`` goes once the framework has made one.

``DecisionSink`` is a Protocol so M3 can add a Redis sink for fan-out
without touching ``BotPipeline`` or anything upstream of it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from loguru import logger
from trading.schemas import Decision

from zetryn_bot.models.token import TokenCandidate


@runtime_checkable
class DecisionSink(Protocol):
    """Consumes one (candidate, decision) pair. No return value expected."""

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None: ...


class LogSink:
    """Production default for M2 — logs every decision at info level."""

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        logger.info(
            "decision mint={} action={} confidence={:.2f} reasons={}",
            candidate.address,
            decision.action,
            decision.confidence,
            decision.reasons,
        )


class ListSink:
    """Test fixture — accumulates every (candidate, decision) pair in memory."""

    def __init__(self) -> None:
        self.decisions: list[tuple[TokenCandidate, Decision]] = []

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        self.decisions.append((candidate, decision))

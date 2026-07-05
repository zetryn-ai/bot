"""Where a ``Decision`` goes once the framework has made one.

``DecisionSink`` is a Protocol so M3 can add a Redis sink for fan-out
without touching ``BotPipeline`` or anything upstream of it.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from loguru import logger
from trading.schemas import Decision

from zetryn_bot.models.token import TokenCandidate

# Bind a component so records pass setup_logger's ``"component" in extra`` filter
# — without it every decision log is silently dropped by both sinks.
log = logger.bind(component="pipeline.sink")


def _format_ai(decision: Decision) -> str:
    """Render the AI analyst verdict, or note it was skipped (hard-gate reject).

    ``decision.analysis`` is only populated when a candidate survives the three
    hard gates and reaches the LLM analyst. Hard-gate rejects never call the
    LLM, so there is genuinely no AI score/reason for them — say so explicitly.
    """
    analysis = decision.analysis
    if analysis is None:
        return "ai=skipped(no-llm-or-hard-gate)"
    reason = (analysis.reasoning or "").replace("\n", " ").strip()[:200]
    return (
        f'ai_score={analysis.final_score:.2f} ai_rec={analysis.recommendation} ai_reason="{reason}"'
    )


@runtime_checkable
class DecisionSink(Protocol):
    """Consumes one (candidate, decision) pair. No return value expected."""

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None: ...


class LogSink:
    """Production default for M2 — logs every decision at info level."""

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        scores = {k: round(v, 3) for k, v in decision.scores.items()}
        log.info(
            "decision source={} mint={} action={} conf={:.2f} scores={} {} reasons={}",
            ",".join(candidate.sources) or "?",
            candidate.address,
            decision.action,
            decision.confidence,
            scores,
            _format_ai(decision),
            decision.reasons,
        )


class ListSink:
    """Test fixture — accumulates every (candidate, decision) pair in memory."""

    def __init__(self) -> None:
        self.decisions: list[tuple[TokenCandidate, Decision]] = []

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        self.decisions.append((candidate, decision))


class TeeSink:
    """Fan one decision out to several sinks, isolating per-sink errors.

    Used in M4 so decisions are both logged (LogSink) and executed
    (ExecutionSink) — one sink failing must not stop the others.
    """

    def __init__(self, sinks: list[DecisionSink]) -> None:
        self._sinks = sinks

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        for sink in self._sinks:
            try:
                await sink.emit(candidate, decision)
            except Exception:
                log.exception("sink {} failed", type(sink).__name__)


class ExecutionSink:
    """M4: route an ``alert`` through RiskManager → Executor → PositionTracker.

    Non-alert decisions and gate rejects are no-ops here (LogSink already
    records them). Skips a mint already held so we never stack positions.
    """

    def __init__(self, risk, executor, tracker, *, notifier=None) -> None:
        self._risk = risk
        self._executor = executor
        self._tracker = tracker
        from zetryn_bot.notify.telegram import NullNotifier

        self._notifier = notifier or NullNotifier()
        # Serialize check→buy→add so concurrent workers can't each pass the
        # max-positions / already-held checks during the buy's await window and
        # overshoot the cap (or double-buy the same mint).
        self._lock = asyncio.Lock()

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        async with self._lock:
            if self._tracker.holds(candidate.address):
                return
            req = self._risk.evaluate(candidate, decision, self._tracker.open_count())
            if req is None:
                return
            position = await self._executor.buy(req)
            if position is not None:
                await self._tracker.add(position)
                await self._notifier.notify(
                    f"\U0001f7e2 opened {position.symbol or position.mint} "
                    f"size={position.size_sol:.4f} SOL conf={position.confidence:.2f}"
                )

"""Where a ``Decision`` goes once the framework has made one.

``DecisionSink`` is a Protocol so M3 can add a Redis sink for fan-out
without touching ``BotPipeline`` or anything upstream of it.
"""

from __future__ import annotations

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

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


class AiActivitySink:
    """M9: persist decisions to `ai_decisions` for the dashboard live feed.

    Two kinds of rows land here:
    - every decision carrying a ``FullAnalysis`` (reached the LLM analyst) —
      the original M9 contract; and
    - every decision from ``record_routes`` (sniper/graduation) even when
      rule-only, so the specialist routes are visible in the live feed
      (user requirement 2026-07-12). Rule rows get ``rule_skip``/
      ``rule_abort`` outcomes and empty reasoning; their rule reasons land
      in ``reasons``.

    Scanner-route hard-gate rejects stay excluded (they are the 33k/day
    firehose). The ExecutionSink, which runs AFTER this sink in the TeeSink
    order, reports the post-verdict outcome ("stopped where") via
    :meth:`set_outcome`. Any DB failure is logged and dropped — this sink
    must never disturb trading (M6 fallback pattern).
    """

    _PENDING_CAP = 500  # mint -> row id map, bounded

    def __init__(self, repo, *, record_routes: tuple[str, ...] = ("sniper", "graduation")) -> None:
        self._repo = repo  # AiActivityRepo (duck-typed for tests)
        self._pending: dict[str, int] = {}
        self._record_routes = record_routes

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        analysis = decision.analysis
        route = str(decision.meta.get("route", "")) if decision.meta else ""
        if analysis is None and route not in self._record_routes:
            return
        # Terminal verdicts get their outcome at insert time; buyable actions
        # start empty and are resolved by the ExecutionSink.
        prefix = "ai" if analysis is not None else "rule"
        outcome = f"{prefix}_{decision.action}" if decision.action in ("skip", "abort") else ""
        try:
            row_id = await self._repo.insert(
                mint=candidate.address,
                symbol=candidate.symbol,
                primary_source=candidate.sources[0] if candidate.sources else "",
                route=route,
                action=decision.action,
                confidence=decision.confidence,
                # Rule rows have no analyst score; confidence is the honest
                # stand-in (the column is non-null).
                final_score=analysis.final_score if analysis is not None else decision.confidence,
                scores={k: round(v, 4) for k, v in decision.scores.items()},
                reasoning=(analysis.reasoning or "") if analysis is not None else "",
                reasons=list(decision.reasons),
                outcome=outcome,
            )
        except Exception:
            log.exception("ai-activity insert failed (trading unaffected)")
            return
        if not outcome:
            if len(self._pending) >= self._PENDING_CAP:
                self._pending.pop(next(iter(self._pending)))
            self._pending[candidate.address] = row_id

    async def set_outcome(self, mint: str, outcome: str, detail: str = "") -> None:
        """Record how far ``mint`` got after its AI verdict. Best-effort."""
        row_id = self._pending.pop(mint, None)
        if row_id is None:
            return
        try:
            await self._repo.set_outcome(row_id, outcome, detail)
        except Exception:
            log.exception("ai-activity outcome update failed (trading unaffected)")


class ExecutionSink:
    """M4: route an ``alert`` through RiskManager → Executor → PositionTracker.

    Non-alert decisions and gate rejects are no-ops here (LogSink already
    records them). Skips a mint already held so we never stack positions.
    """

    def __init__(self, risk, executor, tracker, *, notifier=None, activity=None) -> None:
        self._risk = risk
        self._executor = executor
        self._tracker = tracker
        self._activity = activity  # AiActivitySink | None — M9 outcome reporting
        from zetryn_bot.notify.telegram import NullNotifier

        self._notifier = notifier or NullNotifier()
        # Serialize check→buy→add so concurrent workers can't each pass the
        # max-positions / already-held checks during the buy's await window and
        # overshoot the cap (or double-buy the same mint).
        self._lock = asyncio.Lock()

    async def _outcome(self, mint: str, outcome: str, detail: str = "") -> None:
        if self._activity is not None:
            await self._activity.set_outcome(mint, outcome, detail)

    async def emit(self, candidate: TokenCandidate, decision: Decision) -> None:
        async with self._lock:
            if self._tracker.holds(candidate.address):
                await self._outcome(candidate.address, "already_held")
                return
            if self._tracker.in_cooldown(candidate.address):
                # churn guard: recently closed this mint, don't re-enter yet
                await self._outcome(candidate.address, "cooldown")
                return
            req, code, detail = self._risk.evaluate_ex(
                candidate, decision, self._tracker.open_count()
            )
            if req is None:
                outcome = "not_buy_action" if code == "buy_criteria" else "risk_rejected"
                await self._outcome(candidate.address, outcome, f"{code}: {detail}")
                return
            position = await self._executor.buy(req)
            if position is None:
                await self._outcome(candidate.address, "buy_failed")
                return
            await self._tracker.add(position)
            await self._outcome(candidate.address, "opened")
            from zetryn_bot.notify.format import format_open

            await self._notifier.notify(format_open(position))

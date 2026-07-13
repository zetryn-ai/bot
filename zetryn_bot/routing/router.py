"""``RoutedPipeline`` — first-match dispatcher over per-route pipelines.

Presents the same ``process(candidate, session)`` surface as
:class:`~zetryn_bot.pipeline.runner.BotPipeline`, so the Orchestrator's worker
loop does not change: routing is an internal concern of the pipeline object it
was handed.

Rules are evaluated in order; the first predicate that matches wins; no match
falls through to the fallback (generalist scanner) route. Every ``pumpfun_ws``
candidate is also recorded into :class:`LaunchMemory` BEFORE routing, so a
later migration of the same mint can report its bonding-curve fill time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import aiohttp
from loguru import logger
from trading.schemas import Decision

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.routing.launch_memory import LaunchMemory

log = logger.bind(component="routing.router")


@runtime_checkable
class CandidatePipeline(Protocol):
    """Anything that can take a candidate through decision + sink."""

    async def process(
        self, candidate: TokenCandidate, session: aiohttp.ClientSession
    ) -> Decision: ...


@dataclass(frozen=True)
class Route:
    """One dispatch rule: first matching predicate routes the candidate."""

    name: str
    predicate: Callable[[TokenCandidate], bool]
    pipeline: CandidatePipeline


def primary_source(candidate: TokenCandidate) -> str:
    """The discovering scanner — first entry in ``sources`` (enrichers append after)."""
    return candidate.sources[0] if candidate.sources else ""


class GatedPipeline:
    """Route-specific pre-filter in front of a pipeline (M12).

    A failed gate emits a synthetic rule skip to the shared sink (visible in
    logs) WITHOUT enriching or calling the LLM — that is the point: laggards
    and stale calls stop costing enricher/LLM budget.
    """

    def __init__(self, inner: CandidatePipeline, gate, sink, route_label: str) -> None:
        self._inner = inner
        self._gate = gate  # Callable[[TokenCandidate], tuple[bool, str]]
        self._sink = sink
        self._route_label = route_label

    async def process(self, candidate: TokenCandidate, session: aiohttp.ClientSession) -> Decision:
        ok, why = self._gate(candidate)
        if ok:
            return await self._inner.process(candidate, session)
        decision = Decision(action="skip", confidence=0.0, reasons=[f"route gate: {why}"])
        decision.meta["route"] = self._route_label
        await self._sink.emit(candidate, decision)
        return decision


def live_age_seconds(candidate: TokenCandidate) -> float:
    """Candidate age computed NOW, not the snapshot stamped at parse time.

    ``age_seconds`` is frozen when the scanner parses the event; any queue
    latency between parse and routing would let a stale launch through a
    max-age predicate. Falls back to the snapshot when ``created_at`` is
    unavailable.
    """
    if candidate.created_at is not None:
        created = candidate.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - created).total_seconds())
    return float(candidate.age_seconds)


class RoutedPipeline:
    """First-match router over :class:`Route` entries + a fallback pipeline."""

    def __init__(
        self,
        routes: list[Route],
        fallback: Route,
        *,
        launch_memory: LaunchMemory | None = None,
    ) -> None:
        self._routes = routes
        self._fallback = fallback
        self._launch_memory = launch_memory

    def route_for(self, candidate: TokenCandidate) -> Route:
        for route in self._routes:
            if route.predicate(candidate):
                return route
        return self._fallback

    async def process(self, candidate: TokenCandidate, session: aiohttp.ClientSession) -> Decision:
        # Feed launch memory before routing — the launch event itself may also
        # route (to the sniper), but its timestamp must be recorded regardless.
        if self._launch_memory is not None and primary_source(candidate) == "pumpfun_ws":
            self._launch_memory.record(candidate.address)

        route = self.route_for(candidate)
        log.debug("routing {} -> {}", candidate.address[:8], route.name)
        return await route.pipeline.process(candidate, session)

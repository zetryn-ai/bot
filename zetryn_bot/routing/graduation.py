"""``GraduationPipeline`` — pumpfun_migration candidates → framework graduation agent.

Mirrors :class:`~zetryn_bot.pipeline.runner.BotPipeline`'s ``process``
contract (enrich → adapt → run graph → emit to the shared sink; synthetic
abort on failure) but hands the framework a ``GraduationContext`` instead of
a ``TradingContext``.

The ``GraduationEvent`` is best-effort (see the M10b design doc §3.3): fields
our feeds don't carry yet (unique buyers, LP-burned at graduation, premium)
are zeroed and the matching ``GraduationConfig`` gates relaxed — the agent
judges on what is real (fill speed from ``LaunchMemory``, curve SOL raised,
plus everything in ``TokenInput``). Tightening is a config change once those
feeds exist (M10c).
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
from loguru import logger
from trading.schemas import Decision, GraduationConfig, GraduationContext, GraduationEvent
from zetryn.core import Graph, GraphExecutionError, State

from zetryn_bot.adapters.token_input import to_token_input
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.enrich import enrich_candidate
from zetryn_bot.pipeline.sinks import DecisionSink, LogSink
from zetryn_bot.routing.gates import graduation_gate
from zetryn_bot.routing.launch_memory import LaunchMemory
from zetryn_bot.scanners.protocol import TokenEnricher

log = logger.bind(component="routing.graduation")


def build_graduation_event(
    candidate: TokenCandidate, launch_memory: LaunchMemory
) -> GraduationEvent:
    """Map a ``pumpfun_migration`` candidate to a best-effort ``GraduationEvent``."""
    return GraduationEvent(
        mint=candidate.address,
        pair_address=candidate.address,  # dedicated pair address not in the feed
        detected_at_ts=time.time(),
        # The migration event fires the moment the DEX pool is created, so the
        # PAIR is seconds old at detection. candidate.age_seconds is the TOKEN
        # age (floored at 24h by the scanner — graduation takes 24-72h) and
        # would trip max_pair_age_seconds on every single migration.
        pair_age_seconds=0.0,
        bonding_curve_fill_seconds=launch_memory.fill_seconds(candidate.address),
        bonding_curve_unique_buyers=0,  # feed unavailable — gate relaxed in config
        bonding_curve_sol_raised=candidate.bonding_curve_sol,
        bonding_curve_premium_pct=0.0,  # not derivable yet
        initial_liquidity_sol=0.0,  # gate relaxed; USD liquidity lives in TokenInput
        initial_liquidity_token_pct=0.0,
        lp_burned=False,  # unknown at graduation time — require_lp_burned=False
    )


class GraduationPipeline:
    """Enrich, build ``GraduationContext``, run the graduation agent, emit."""

    def __init__(
        self,
        agent: Graph,
        *,
        enrichers: list[TokenEnricher] | None = None,
        sink: DecisionSink | None = None,
        config: GraduationConfig | None = None,
        launch_memory: LaunchMemory,
        route_label: str = "graduation",
        confirm_delay_s: float = 20.0,
        min_liquidity_usd: float = 2000.0,
    ) -> None:
        self.agent = agent
        self.enrichers = enrichers or []
        self.sink: DecisionSink = sink or LogSink()
        self.config = config or GraduationConfig()
        self._launch_memory = launch_memory
        self._route_label = route_label
        # Wait-and-confirm (rework): after migration, wait this long BEFORE
        # enriching+deciding so the initial sniper dump reveals itself (and
        # Jupiter has time to index). Then graduation_gate skips anything that
        # dropped / has thin liquidity / is being sold into.
        self._confirm_delay_s = confirm_delay_s
        self._min_liquidity_usd = min_liquidity_usd

    async def _skip(self, enriched: TokenCandidate, reason: str) -> Decision:
        decision = Decision(action="skip", confidence=0.0, reasons=[reason])
        decision.meta["route"] = self._route_label
        await self.sink.emit(enriched, decision)
        return decision

    async def process(self, candidate: TokenCandidate, session: aiohttp.ClientSession) -> Decision:
        # Confirmation window: let the post-migration dump play out before we
        # commit any capital. Enrichment AFTER the wait sees the settled state.
        if self._confirm_delay_s > 0:
            await asyncio.sleep(self._confirm_delay_s)
        enriched = await enrich_candidate(candidate, self.enrichers, session)

        # Anti-dump entry guard on the post-confirmation snapshot.
        ok, why = graduation_gate(enriched, min_liquidity_usd=self._min_liquidity_usd)
        if not ok:
            return await self._skip(enriched, f"graduation guard: {why}")

        try:
            context = GraduationContext(
                token=to_token_input(enriched),
                event=build_graduation_event(enriched, self._launch_memory),
                config=self.config,
            )
        except Exception:
            log.exception("graduation adapter failed for {}; synthetic abort", enriched.address)
            decision = Decision(
                action="abort",
                reasons=["adapter failed to build GraduationContext"],
                flags={"synthetic": True, "source": "bot_adapter"},
            )
            decision.meta["route"] = self._route_label
            await self.sink.emit(enriched, decision)
            return decision

        state = State(context=context)
        try:
            state = await self.agent.run(state)
        except GraphExecutionError:
            log.exception("graduation agent failed for {}; abort decision", enriched.address)
            decision = Decision(
                action="abort",
                reasons=["agent execution failed"],
                flags={"llm_failed": True},
            )
        else:
            decision = state.output

        decision.meta["route"] = self._route_label
        await self.sink.emit(enriched, decision)
        return decision

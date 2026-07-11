"""``BotPipeline`` — wires one candidate through enrich -> adapt -> agent -> sink.

Agent-agnostic: accepts any compiled ``zetryn.core.Graph``, so swapping
``build_scanner`` for ``build_sniper`` / ``build_graduation`` later is a
constructor argument, not a code change here.
"""

from __future__ import annotations

import aiohttp
from loguru import logger
from trading.schemas import Decision, ScannerConfig, TradingContext
from zetryn.core import Graph, GraphExecutionError, State

from zetryn_bot.adapters.token_input import to_token_input
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.enrich import enrich_candidate
from zetryn_bot.pipeline.sinks import DecisionSink, LogSink
from zetryn_bot.scanners.protocol import Scanner, TokenEnricher

# Bind a component so error records survive setup_logger's component filter.
log = logger.bind(component="pipeline.runner")


class BotPipeline:
    """One compiled agent + a set of enrichers + a sink, run over candidates."""

    def __init__(
        self,
        agent: Graph,
        *,
        enrichers: list[TokenEnricher] | None = None,
        sink: DecisionSink | None = None,
        config: ScannerConfig | None = None,
        route_label: str | None = None,
    ) -> None:
        self.agent = agent
        self.enrichers = enrichers or []
        self.sink: DecisionSink = sink or LogSink()
        self.config = config or ScannerConfig()
        # M10b: stamped into decision.meta["route"] before the sink runs so
        # per-route risk policy (size multiplier, confidence floor) can see it.
        # None (default) = pre-routing behaviour, no stamp.
        self.route_label = route_label

    async def process(self, candidate: TokenCandidate, session: aiohttp.ClientSession) -> Decision:
        """Enrich, adapt, and run one candidate through the agent; emit + return the Decision."""
        enriched = await enrich_candidate(candidate, self.enrichers, session)
        log.debug(
            "enriched source={} mint={} liq=${:,.0f} mcap=${:,.0f} vol1h=${:,.0f} "
            "holders={} top10={:.0f}% safety={} smart={} kol={} sniper={} bundler={}",
            ",".join(enriched.sources) or "?",
            enriched.address,
            enriched.liquidity_usd,
            enriched.market_cap_usd,
            enriched.volume_1h_usd,
            enriched.holder_count,
            enriched.top10_holder_pct,
            enriched.gmgn_safety_score,
            enriched.gmgn_smart_wallets,
            enriched.gmgn_kol_wallets,
            enriched.gmgn_sniper_wallets,
            enriched.gmgn_bundler_wallets,
        )

        try:
            context = TradingContext(token=to_token_input(enriched), config=self.config)
        except Exception:
            log.exception("adapter failed for {}; emitting synthetic abort", enriched.address)
            decision = Decision(
                action="abort",
                reasons=["adapter failed to build TokenInput"],
                flags={"synthetic": True, "source": "bot_adapter"},
            )
            if self.route_label:
                decision.meta["route"] = self.route_label
            await self.sink.emit(enriched, decision)
            return decision

        state = State(context=context)
        try:
            state = await self.agent.run(state)
        except GraphExecutionError:
            log.exception("agent run failed for {}; emitting abort decision", enriched.address)
            decision = Decision(
                action="abort",
                reasons=["agent execution failed"],
                flags={"llm_failed": True},
            )
        else:
            decision = state.output

        if self.route_label:
            decision.meta["route"] = self.route_label
        await self.sink.emit(enriched, decision)
        return decision

    async def run_scanner(self, scanner: Scanner, session: aiohttp.ClientSession) -> None:
        """Drive one Scanner's stream through :meth:`process`, forever."""
        async for candidate in scanner.stream(session):
            await self.process(candidate, session)

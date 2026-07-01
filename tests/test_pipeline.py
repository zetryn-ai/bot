"""Unit tests for enrich/sink/runner — mocked framework, no network."""

from __future__ import annotations

import aiohttp
import pytest
from trading.schemas import Decision, ScannerConfig
from zetryn.core import END, Graph, RuleNode

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.enrich import enrich_candidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink, LogSink


class _StubEnricher:
    """Adds a fixed field, ignoring the network session."""

    name = "stub"

    def __init__(self, symbol: str = "STUB") -> None:
        self._symbol = symbol

    async def enrich(self, mint, candidate, session):
        return candidate.model_copy(update={"symbol": self._symbol})


class _RaisingEnricher:
    name = "raising"

    async def enrich(self, mint, candidate, session):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_enrich_candidate_applies_enrichers_in_order():
    candidate = TokenCandidate(address="Mint1")
    async with aiohttp.ClientSession() as session:
        result = await enrich_candidate(
            candidate, [_StubEnricher("A"), _StubEnricher("B")], session
        )
    assert result.symbol == "B"


@pytest.mark.asyncio
async def test_enrich_candidate_skips_raising_enricher():
    candidate = TokenCandidate(address="Mint1")
    async with aiohttp.ClientSession() as session:
        result = await enrich_candidate(
            candidate, [_StubEnricher("A"), _RaisingEnricher(), _StubEnricher("C")], session
        )
    assert result.symbol == "C"


@pytest.mark.asyncio
async def test_list_sink_accumulates_pairs():
    sink = ListSink()
    candidate = TokenCandidate(address="Mint1")
    decision = Decision(action="watch")
    await sink.emit(candidate, decision)
    assert sink.decisions == [(candidate, decision)]


@pytest.mark.asyncio
async def test_log_sink_does_not_raise():
    await LogSink().emit(TokenCandidate(address="Mint1"), Decision(action="skip"))


def _passthrough_agent(output_action: str) -> Graph:
    """Minimal one-node Graph — stands in for a real build_scanner() agent."""

    def _node(state):
        state.output = Decision(action=output_action)

    g = Graph("stub_agent")
    g.add_node(RuleNode("only", _node))
    g.set_entry("only")
    g.add_edge("only", END)
    return g.compile()


@pytest.mark.asyncio
async def test_pipeline_process_enriches_adapts_runs_and_emits():
    sink = ListSink()
    pipeline = BotPipeline(
        _passthrough_agent("alert"),
        enrichers=[_StubEnricher("ENRICHED")],
        sink=sink,
        config=ScannerConfig(),
    )
    candidate = TokenCandidate(address="Mint1", liquidity_usd=10_000)

    async with aiohttp.ClientSession() as session:
        decision = await pipeline.process(candidate, session)

    assert decision.action == "alert"
    assert len(sink.decisions) == 1
    emitted_candidate, emitted_decision = sink.decisions[0]
    assert emitted_candidate.symbol == "ENRICHED"
    assert emitted_decision is decision


@pytest.mark.asyncio
async def test_pipeline_process_emits_synthetic_abort_on_adapter_failure(monkeypatch):
    def _broken_to_token_input(candidate):
        raise ValueError("schema mismatch")

    monkeypatch.setattr("zetryn_bot.pipeline.runner.to_token_input", _broken_to_token_input)

    sink = ListSink()
    pipeline = BotPipeline(_passthrough_agent("alert"), sink=sink)
    candidate = TokenCandidate(address="Mint1")

    async with aiohttp.ClientSession() as session:
        decision = await pipeline.process(candidate, session)

    assert decision.action == "abort"
    assert decision.flags == {"synthetic": True, "source": "bot_adapter"}
    assert len(sink.decisions) == 1

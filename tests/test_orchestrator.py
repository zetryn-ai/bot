"""Unit tests for the Orchestrator — fake scanners, no network/LLM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from trading.schemas import Decision
from zetryn.core import END, Graph, RuleNode

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink
from zetryn_bot.runtime.orchestrator import Orchestrator


class _FakeScanner:
    """Yields the given candidates, then stays alive (so supervise doesn't restart it)."""

    def __init__(self, name: str, candidates: list[TokenCandidate]) -> None:
        self.name = name
        self._candidates = candidates

    async def stream(self, session) -> AsyncIterator[TokenCandidate]:
        for candidate in self._candidates:
            yield candidate
        # Keep the producer coroutine alive so `supervise` does not treat a
        # normal return as "finished" and restart it in a tight loop.
        await asyncio.sleep(3600)


class _RaisingScanner:
    name = "raising"

    async def stream(self, session) -> AsyncIterator[TokenCandidate]:
        raise RuntimeError("scanner boom")
        yield  # pragma: no cover - makes this an async generator


def _passthrough_pipeline(sink: ListSink) -> BotPipeline:
    def _node(state):
        state.output = Decision(action="watch")

    g = Graph("stub")
    g.add_node(RuleNode("only", _node))
    g.set_entry("only")
    g.add_edge("only", END)
    return BotPipeline(g.compile(), sink=sink)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


def _cand(address: str) -> TokenCandidate:
    return TokenCandidate(address=address, sources=["dexscreener.new_pairs"])


@pytest.mark.asyncio
async def test_all_candidates_flow_to_the_sink():
    sink = ListSink()
    scanner = _FakeScanner("fake", [_cand("A"), _cand("B"), _cand("C")])
    orch = Orchestrator(_passthrough_pipeline(sink), [scanner], workers=2)
    await orch.start()
    try:
        await _wait_for(lambda: len(sink.decisions) == 3)
    finally:
        await orch.shutdown()
    assert {c.address for c, _ in sink.decisions} == {"A", "B", "C"}


@pytest.mark.asyncio
async def test_duplicate_mints_are_processed_once():
    sink = ListSink()
    scanner = _FakeScanner("fake", [_cand("A"), _cand("A"), _cand("B")])
    orch = Orchestrator(_passthrough_pipeline(sink), [scanner], workers=2, dedup_ttl_s=60.0)
    await orch.start()
    try:
        await _wait_for(lambda: len(sink.decisions) == 2)
        # Give any erroneous third item a chance to appear before asserting.
        await asyncio.sleep(0.05)
    finally:
        await orch.shutdown()
    assert len(sink.decisions) == 2
    assert {c.address for c, _ in sink.decisions} == {"A", "B"}


@pytest.mark.asyncio
async def test_crashing_scanner_does_not_stop_the_runtime():
    sink = ListSink()
    good = _FakeScanner("good", [_cand("A"), _cand("B")])
    bad = _RaisingScanner()
    orch = Orchestrator(_passthrough_pipeline(sink), [bad, good], workers=2)
    await orch.start()
    try:
        # The good scanner's candidates are processed even though `bad` crashed
        # (supervise isolates the crash and backs off before any retry).
        await _wait_for(lambda: len(sink.decisions) == 2)
    finally:
        await orch.shutdown()
    assert {c.address for c, _ in sink.decisions} == {"A", "B"}

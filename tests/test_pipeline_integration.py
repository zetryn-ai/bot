"""One integration test against a real ``build_scanner()`` agent.

Rule-only path (``llm_client=None``) — no API key, no network call into the
framework's LLM layer. Verifies the bot's TokenCandidate -> TokenInput
bridge actually satisfies the framework's schema contract end to end.
"""

from __future__ import annotations

import aiohttp
import pytest
from strategies.agents.scanner import build_scanner
from trading.schemas import ScannerConfig

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink


@pytest.mark.asyncio
async def test_healthy_candidate_reaches_finalize_without_llm():
    agent = build_scanner(llm_client=None)
    sink = ListSink()
    pipeline = BotPipeline(
        agent,
        sink=sink,
        config=ScannerConfig(min_liquidity_usd=5_000, min_volume_1h=10_000, min_holders=50),
    )
    candidate = TokenCandidate(
        address="Mint1",
        symbol="GOOD",
        sources=["dexscreener.new_pairs"],
        liquidity_usd=50_000,
        volume_1h_usd=40_000,
        holder_count=200,
        top10_holder_pct=15.0,
    )

    async with aiohttp.ClientSession() as session:
        decision = await pipeline.process(candidate, session)

    # No LLM client -> the candidate clears the hard gates (unlike the rug
    # case below) but finalize() has no FullAnalysis to work with, so it
    # defensively skips rather than fabricating a verdict. What this test
    # actually pins down: the bridge reaches finalize at all — i.e. the
    # TokenInput built by the adapter satisfied every hard gate — and the
    # skip here is distinguishable from a hard-gate skip (no "hard_gate" flag).
    assert decision.action == "skip"
    assert decision.flags.get("llm_failed") is True
    assert "hard_gate" not in decision.flags
    assert sink.decisions[0][1] is decision


@pytest.mark.asyncio
async def test_dangerous_candidate_is_rejected_by_hard_gates():
    agent = build_scanner(llm_client=None)
    pipeline = BotPipeline(agent)
    candidate = TokenCandidate(
        address="Mint2",
        symbol="RUG",
        is_honeypot=True,
        liquidity_usd=50_000,
        volume_1h_usd=40_000,
    )

    async with aiohttp.ClientSession() as session:
        decision = await pipeline.process(candidate, session)

    assert decision.action == "skip"
    assert decision.flags.get("hard_gate") is True

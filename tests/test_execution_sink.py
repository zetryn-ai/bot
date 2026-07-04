"""Unit tests for ExecutionSink + TeeSink."""

from __future__ import annotations

import pytest
from trading.schemas import Decision

from zetryn_bot.execution.executor import PaperExecutor
from zetryn_bot.execution.jupiter import Quote, sol_to_lamports
from zetryn_bot.execution.position import PositionTracker
from zetryn_bot.execution.risk import RiskConfig, RiskManager
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.sinks import ExecutionSink, TeeSink


class _FakeJupiter:
    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        # buy: return 1e6 tokens; sell/value: return the entry SOL back (flat)
        out = 1_000_000 if output_mint != _FakeJupiter.SOL else sol_to_lamports(0.2)
        return Quote(in_amount=amount_atomic, out_amount=out, price_impact_pct=0.0)


_FakeJupiter.SOL = "So11111111111111111111111111111111111111112"


def _sink() -> tuple[ExecutionSink, PositionTracker]:
    jup = _FakeJupiter()
    risk = RiskManager(RiskConfig(base_size_sol=0.2, min_confidence=0.6, max_positions=5))
    tracker = PositionTracker(PaperExecutor(jup), jup, risk)
    return ExecutionSink(risk, PaperExecutor(jup), tracker), tracker


def _cand(addr="MintA") -> TokenCandidate:
    return TokenCandidate(address=addr, symbol="AAA")


@pytest.mark.asyncio
async def test_alert_opens_a_position():
    sink, tracker = _sink()
    await sink.emit(_cand(), Decision(action="alert", confidence=0.8))
    assert tracker.open_count() == 1


@pytest.mark.asyncio
async def test_non_alert_is_noop():
    sink, tracker = _sink()
    await sink.emit(_cand(), Decision(action="watch", confidence=0.9))
    await sink.emit(_cand(), Decision(action="skip", confidence=0.0))
    assert tracker.open_count() == 0


@pytest.mark.asyncio
async def test_already_held_mint_is_not_restacked():
    sink, tracker = _sink()
    await sink.emit(_cand("MintA"), Decision(action="alert", confidence=0.8))
    await sink.emit(_cand("MintA"), Decision(action="alert", confidence=0.9))
    assert tracker.open_count() == 1


@pytest.mark.asyncio
async def test_tee_sink_fans_out_and_isolates_errors():
    seen: list[str] = []

    class _Good:
        async def emit(self, c, d):
            seen.append("good")

    class _Bad:
        async def emit(self, c, d):
            raise RuntimeError("boom")

    tee = TeeSink([_Bad(), _Good()])
    await tee.emit(_cand(), Decision(action="alert", confidence=0.8))
    assert seen == ["good"]  # good ran despite bad raising

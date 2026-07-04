"""Unit tests for PaperExecutor — buy/sell/PnL with a mocked Jupiter."""

from __future__ import annotations

import pytest

from zetryn_bot.execution.executor import PaperExecutor, Position, SwapRequest
from zetryn_bot.execution.jupiter import SOL_MINT, Quote, sol_to_lamports


class _FakeJupiter:
    """Returns queued out_amounts per call, in order."""

    def __init__(self, out_amounts: list[int]) -> None:
        self._out = list(out_amounts)
        self.calls: list[tuple] = []

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        self.calls.append((input_mint, output_mint, amount_atomic))
        if not self._out:
            return None
        return Quote(in_amount=amount_atomic, out_amount=self._out.pop(0), price_impact_pct=0.01)


def _req(size_sol=0.2) -> SwapRequest:
    return SwapRequest(
        mint="MintA",
        symbol="AAA",
        size_sol=size_sol,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
    )


@pytest.mark.asyncio
async def test_buy_creates_position_with_tokens():
    jup = _FakeJupiter([1_000_000])  # 1e6 tokens for the buy
    pos = await PaperExecutor(jup).buy(_req(size_sol=0.2))
    assert pos is not None
    assert pos.tokens_atomic == 1_000_000
    assert pos.size_sol == 0.2
    # buy quotes SOL -> mint with the SOL size in lamports
    assert jup.calls[0][:2] == (SOL_MINT, "MintA")
    assert jup.calls[0][2] == sol_to_lamports(0.2)


@pytest.mark.asyncio
async def test_buy_aborts_when_no_quote():
    assert await PaperExecutor(_FakeJupiter([])).buy(_req()) is None


@pytest.mark.asyncio
async def test_sell_computes_profit():
    # sell quote returns 0.30 SOL worth of lamports for a 0.20 SOL position
    jup = _FakeJupiter([sol_to_lamports(0.30)])
    pos = Position(
        mint="MintA",
        symbol="AAA",
        size_sol=0.20,
        tokens_atomic=1_000_000,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
    )
    trade = await PaperExecutor(jup).sell(pos, "take_profit")
    assert trade is not None
    assert round(trade.pnl_sol, 4) == 0.10
    assert round(trade.pnl_pct, 2) == 0.50
    assert trade.reason == "take_profit"


@pytest.mark.asyncio
async def test_sell_computes_loss():
    jup = _FakeJupiter([sol_to_lamports(0.17)])
    pos = Position(
        mint="MintA",
        symbol="AAA",
        size_sol=0.20,
        tokens_atomic=1_000_000,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
    )
    trade = await PaperExecutor(jup).sell(pos, "stop_loss")
    assert round(trade.pnl_sol, 4) == -0.03

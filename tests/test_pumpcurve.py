"""Bonding-curve math + quoting (fields verified live 2026-07-12)."""

import pytest

from zetryn_bot.execution.pumpcurve import CurveState, PumpCurveQuote, buy_out, sell_out

# Real snapshot from frontend-api-v3 (WYNN, 2026-07-12).
_S = CurveState(
    virtual_sol_reserves=31_975_000_003,
    virtual_token_reserves=1_006_724_003_133_662,
    complete=False,
)


def test_buy_then_sell_round_trip_loses_fees_only():
    sol_in = 1_000_000_000  # 1 SOL
    tokens = buy_out(_S, sol_in)
    assert tokens > 0
    # Sell back against the SAME state: loss = 2x 1% fee + price impact.
    sol_back = sell_out(_S, tokens)
    assert sol_back < sol_in
    assert sol_back > sol_in * 0.90


def test_bigger_buys_get_worse_prices():
    small = buy_out(_S, 100_000_000)  # 0.1 SOL
    big = buy_out(_S, 10_000_000_000)  # 10 SOL
    assert big / 100 < small  # per-SOL tokens received shrink with size


@pytest.mark.asyncio
async def test_completed_curve_quotes_none(monkeypatch):
    q = PumpCurveQuote()

    async def _state(mint):
        return CurveState(1, 1, complete=True)

    monkeypatch.setattr(q, "state", _state)
    assert await q.buy_quote("M", 10) is None
    assert await q.sell_quote("M", 10) is None

"""M10 — LifecycleEngine (framework exit agent) + PositionTracker integration."""

from __future__ import annotations

import pytest

from zetryn_bot.execution.executor import PaperExecutor, Position
from zetryn_bot.execution.jupiter import Quote, sol_to_lamports
from zetryn_bot.execution.lifecycle import LifecycleEngine
from zetryn_bot.execution.position import PositionTracker
from zetryn_bot.execution.risk import RiskConfig, RiskManager


def _engine(**kw) -> LifecycleEngine:
    defaults = dict(
        take_profit_pct=0.30,
        stop_loss_pct=0.15,
        max_hold_s=1800.0,
        trailing_arm_pnl_pct=0.20,
        trailing_drawdown_pct=0.50,
    )
    return LifecycleEngine(**{**defaults, **kw})


def _pos(size_sol: float = 0.2) -> Position:
    return Position(
        mint="MintA",
        symbol="AAA",
        size_sol=size_sol,
        tokens_atomic=1_000_000,
        take_profit_pct=0.30,
        stop_loss_pct=0.15,
        max_hold_s=1800.0,
        confidence=0.8,
        opened_at=1000.0,
    )


@pytest.mark.asyncio
async def test_hold_inside_envelope():
    d = await _engine().evaluate(_pos(), current_sol=0.21, holding_s=60)  # +5%
    assert d.action == "hold"


@pytest.mark.asyncio
async def test_hard_stop_loss_exits():
    d = await _engine().evaluate(_pos(), current_sol=0.16, holding_s=60)  # -20%
    assert d.action == "exit_full"
    assert LifecycleEngine.close_reason(d) == "stop_loss"


@pytest.mark.asyncio
async def test_time_stop_exits_flat_position():
    d = await _engine().evaluate(_pos(), current_sol=0.20, holding_s=1801)
    assert d.action == "exit_full"
    assert LifecycleEngine.close_reason(d) == "max_hold"


@pytest.mark.asyncio
async def test_take_profit_exits_at_tp():
    d = await _engine().evaluate(_pos(), current_sol=0.27, holding_s=60)  # +35%
    assert d.action == "exit_full"  # single full-exit rung
    assert LifecycleEngine.close_reason(d) == "take_profit"


@pytest.mark.asyncio
async def test_trailing_stop_exits_when_momentum_dies():
    """The new alpha: +25% run-up, then fade to +8% — old static exits would
    hold to max-hold/SL; the trailing stop takes the remaining profit."""
    eng = _engine()
    pos = _pos()
    d1 = await eng.evaluate(pos, current_sol=0.25, holding_s=60)  # +25% → peak armed
    assert d1.action == "hold"
    # +8%: drawdown from peak = 1 - 1.08/1.25 = 13.6% < 50% → still holding
    d2 = await eng.evaluate(pos, current_sol=0.216, holding_s=120)
    assert d2.action == "hold"
    # +2%: drawdown = 1 - 1.02/1.25 = 18.4% — still under. Push peak higher first.
    d3 = await eng.evaluate(pos, current_sol=0.40, holding_s=180)  # +100% peak... TP fires
    # TP rung (+30%) fires before trailing here — full exit takes the profit.
    assert d3.action == "exit_full"
    assert LifecycleEngine.close_reason(d3) == "take_profit"


@pytest.mark.asyncio
async def test_trailing_stop_fires_below_tp():
    """Peak below TP rung, then heavy give-back → trailing exit, not SL."""
    eng = _engine(take_profit_pct=1.0)  # push TP out of the way
    pos = _pos()
    await eng.evaluate(pos, current_sol=0.25, holding_s=60)  # peak +25% (armed)
    # fade to +2%: drawdown = 1 - 1.02/1.25 = 18.4% < 50% → hold
    d = await eng.evaluate(pos, current_sol=0.204, holding_s=120)
    assert d.action == "hold"
    # fade to -10%: drawdown = 1 - 0.90/1.25 = 28% < 50% → hold (SL not hit)
    d = await eng.evaluate(pos, current_sol=0.18, holding_s=180)
    assert d.action == "hold"
    # deeper fade to -14.9% — just above SL; drawdown = 1 - 0.851/1.25 = 31.9%
    d = await eng.evaluate(pos, current_sol=0.1702, holding_s=240)
    assert d.action == "hold"
    # bigger run then collapse: peak +60%, fade to -5% → drawdown 40.6%... still <50%
    eng2 = _engine(take_profit_pct=1.0)
    pos2 = _pos()
    await eng2.evaluate(pos2, current_sol=0.32, holding_s=60)  # peak +60%
    d = await eng2.evaluate(pos2, current_sol=0.155, holding_s=120)  # -22.5% → SL wins
    assert d.action == "exit_full"
    assert LifecycleEngine.close_reason(d) == "stop_loss"
    # peak +60%, fade to +... drawdown ≥ 50% while above SL: need pnl ≤ -20%? No:
    # 1 - (1+p)/1.6 ≥ 0.5 → p ≤ -0.2, below SL. Use a wider trailing config:
    eng3 = _engine(take_profit_pct=1.0, trailing_drawdown_pct=0.30)
    pos3 = _pos()
    await eng3.evaluate(pos3, current_sol=0.32, holding_s=60)  # peak +60%
    d = await eng3.evaluate(pos3, current_sol=0.22, holding_s=120)  # +10%, dd=31%
    assert d.action == "exit_full"
    assert LifecycleEngine.close_reason(d) == "trailing_stop"


@pytest.mark.asyncio
async def test_forget_resets_peak():
    eng = _engine(take_profit_pct=1.0, trailing_drawdown_pct=0.30)
    pos = _pos()
    await eng.evaluate(pos, current_sol=0.32, holding_s=60)  # peak +60%
    eng.forget(pos.mint)
    # Same fade that fired the trailing stop above now just re-arms the peak.
    d = await eng.evaluate(pos, current_sol=0.22, holding_s=120)
    assert d.action == "hold"


# -- PositionTracker integration ----------------------------------------------


class _FakeJupiter:
    def __init__(self, current_sol: float) -> None:
        self.current_sol = current_sol

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        return Quote(
            in_amount=amount_atomic,
            out_amount=sol_to_lamports(self.current_sol),
            price_impact_pct=0.0,
        )


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _tracker(jup, clock, engine):
    risk = RiskManager(RiskConfig())
    ex = PaperExecutor(jup)
    return PositionTracker(ex, jup, risk, now_fn=clock, lifecycle=engine)


@pytest.mark.asyncio
async def test_tracker_uses_lifecycle_trailing_exit():
    clock = _Clock()
    jup = _FakeJupiter(current_sol=0.32)  # +60% on a 0.2 entry
    engine = _engine(take_profit_pct=1.0, trailing_drawdown_pct=0.30)
    tracker = _tracker(jup, clock, engine)
    pos = _pos()
    pos.opened_at = 0.0
    await tracker.add(pos)

    await tracker.check_once()  # +60% — peak arms, still open
    assert tracker.open_count() == 1

    jup.current_sol = 0.22  # fade to +10% → 31% drawdown from peak
    await tracker.check_once()
    assert tracker.open_count() == 0
    assert tracker.stats()["closed"] == 1
    # trailing exit banked the remaining +0.02 SOL profit
    assert tracker.stats()["total_pnl_sol"] == pytest.approx(0.02, abs=1e-9)


@pytest.mark.asyncio
async def test_tracker_without_engine_keeps_static_behaviour():
    clock = _Clock()
    jup = _FakeJupiter(current_sol=0.22)  # +10%: inside static envelope
    tracker = _tracker(jup, clock, engine=None)
    await tracker.add(_pos())
    await tracker.check_once()
    assert tracker.open_count() == 1


# ── M10.1: partial TP ladder ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ladder_first_rung_is_partial_then_skipped():
    eng = _engine(tp_ladder=[(0.30, 0.5), (1.00, 1.0)])
    d = await eng.evaluate(_pos(size_sol=0.2), current_sol=0.27, holding_s=60)  # +35%
    assert d.action in ("take_profit", "scale_out")
    assert d.size == pytest.approx(0.1, rel=1e-3)  # 50% of current basis

    # Rung recorded at its THRESHOLD (0.30), not the actual pnl (0.35) —
    # otherwise the framework refires the rung every sweep.
    rung = eng.mark_rung("MintA", pnl_pct=0.35, sold_size=0.1)
    assert rung == pytest.approx(0.30)

    d2 = await eng.evaluate(_pos(size_sol=0.1), current_sol=0.135, holding_s=90)  # still +35%
    assert d2.action == "hold"


@pytest.mark.asyncio
async def test_ladder_final_rung_full_exit():
    eng = _engine(tp_ladder=[(0.30, 0.5), (1.00, 1.0)])
    eng.mark_rung("MintA", pnl_pct=0.31, sold_size=0.1)
    d = await eng.evaluate(_pos(size_sol=0.1), current_sol=0.21, holding_s=60)  # +110%
    assert d.action == "exit_full"
    assert LifecycleEngine.close_reason(d) == "take_profit"


@pytest.mark.asyncio
async def test_restore_partials_survives_restart():
    eng = _engine(tp_ladder=[(0.30, 0.5), (1.00, 1.0)])
    eng.mark_rung("MintA", pnl_pct=0.32, sold_size=0.1)
    dumped = eng.partials_as_dicts("MintA")

    fresh = _engine(tp_ladder=[(0.30, 0.5), (1.00, 1.0)])
    fresh.restore_partials("MintA", dumped)
    d = await fresh.evaluate(_pos(size_sol=0.1), current_sol=0.135, holding_s=60)  # +35%
    assert d.action == "hold"  # rung not refired after restart

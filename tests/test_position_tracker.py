"""Unit tests for PositionTracker — exit rules, stats (mocked quote + clock)."""

from __future__ import annotations

import pytest

from zetryn_bot.execution.executor import PaperExecutor, Position
from zetryn_bot.execution.jupiter import Quote, sol_to_lamports
from zetryn_bot.execution.position import PositionTracker
from zetryn_bot.execution.risk import RiskConfig, RiskManager


class _FakeJupiter:
    """Returns a fixed current SOL value (as lamports) for every quote."""

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


def _tracker(current_sol, clock):
    jup = _FakeJupiter(current_sol)
    risk = RiskManager(RiskConfig())
    ex = PaperExecutor(jup)
    return PositionTracker(ex, jup, risk, now_fn=clock), risk


async def _open(tracker, size_sol=0.2, tp=0.3, sl=0.15, max_hold=1800):
    pos = Position(
        mint="MintA",
        symbol="AAA",
        size_sol=size_sol,
        tokens_atomic=1_000_000,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        max_hold_s=max_hold,
        confidence=0.8,
    )
    await tracker.add(pos)


@pytest.mark.asyncio
async def test_take_profit_closes_position():
    clock = _Clock()
    tracker, _risk = _tracker(current_sol=0.30, clock=clock)  # +50% vs 0.20 entry
    await _open(tracker)
    await tracker.check_once()
    assert tracker.open_count() == 0
    assert tracker.stats()["closed"] == 1
    assert tracker.stats()["total_pnl_sol"] == pytest.approx(0.10, abs=1e-6)


@pytest.mark.asyncio
async def test_stop_loss_closes_position():
    clock = _Clock()
    tracker, _ = _tracker(current_sol=0.16, clock=clock)  # -20% vs 0.20, past -15% SL
    await _open(tracker)
    await tracker.check_once()
    assert tracker.open_count() == 0
    assert tracker.stats()["closed"] == 1


@pytest.mark.asyncio
async def test_holds_within_thresholds():
    clock = _Clock()
    tracker, _ = _tracker(current_sol=0.21, clock=clock)  # +5%, below TP, above SL
    await _open(tracker)
    await tracker.check_once()
    assert tracker.open_count() == 1  # still open


@pytest.mark.asyncio
async def test_max_hold_closes_even_when_flat():
    clock = _Clock()
    tracker, _ = _tracker(current_sol=0.20, clock=clock)  # flat: no TP/SL
    await _open(tracker, max_hold=300)
    await tracker.check_once()
    assert tracker.open_count() == 1  # not yet
    clock.t += 301  # advance past max_hold
    await tracker.check_once()
    assert tracker.open_count() == 0
    assert tracker.stats()["closed"] == 1


@pytest.mark.asyncio
async def test_win_rate_stat():
    clock = _Clock()
    tracker, _ = _tracker(current_sol=0.30, clock=clock)
    await _open(tracker)
    await tracker.check_once()
    assert tracker.stats()["win_rate"] == 1.0


class _FailingJupiter:
    """quote_or_status returns a configurable failure; quote() mirrors it."""

    def __init__(self, status: int | None) -> None:
        self.status = status

    async def quote_or_status(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        return None, self.status

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        return None


def _tracker_with(jup, clock, dead_route_after=3):
    risk = RiskManager(RiskConfig())
    ex = PaperExecutor(jup)
    return PositionTracker(ex, jup, risk, now_fn=clock, dead_route_after=dead_route_after)


@pytest.mark.asyncio
async def test_dead_route_closes_expired_position_at_zero():
    clock = _Clock()
    tracker = _tracker_with(_FailingJupiter(status=400), clock, dead_route_after=3)
    await _open(tracker, size_sol=0.2, max_hold=1800)
    clock.t += 1801  # past max hold

    await tracker.check_once()  # fail 1
    await tracker.check_once()  # fail 2
    assert tracker.open_count() == 1  # below threshold — still open
    await tracker.check_once()  # fail 3 → dead_route close

    assert tracker.open_count() == 0
    stats = tracker.stats()
    assert stats["closed"] == 1
    assert stats["total_pnl_sol"] == pytest.approx(-0.2)  # total loss, exit at 0


@pytest.mark.asyncio
async def test_rate_limit_never_force_closes():
    clock = _Clock()
    tracker = _tracker_with(_FailingJupiter(status=429), clock, dead_route_after=3)
    await _open(tracker, max_hold=1800)
    clock.t += 1801  # past max hold

    for _ in range(10):  # way past the dead-route threshold
        await tracker.check_once()

    assert tracker.open_count() == 1  # 429 is transient — never close blind


@pytest.mark.asyncio
async def test_dead_route_not_closed_before_max_hold():
    clock = _Clock()
    tracker = _tracker_with(_FailingJupiter(status=400), clock, dead_route_after=3)
    await _open(tracker, max_hold=1800)  # still young

    for _ in range(10):
        await tracker.check_once()

    assert tracker.open_count() == 1  # no-route alone isn't enough — needs age too


@pytest.mark.asyncio
async def test_route_fail_counter_resets_on_success():
    clock = _Clock()
    jup = _FailingJupiter(status=400)
    tracker = _tracker_with(jup, clock, dead_route_after=3)
    await _open(tracker, size_sol=0.2, max_hold=1800)
    clock.t += 1801

    await tracker.check_once()  # fail 1
    await tracker.check_once()  # fail 2

    # Route comes back at a flat price — max_hold closes it via the normal path.
    async def _good_quote(input_mint, output_mint, amount_atomic, slippage_bps=100):
        return Quote(
            in_amount=amount_atomic,
            out_amount=sol_to_lamports(0.2),
            price_impact_pct=0.0,
        ), 200

    jup.quote_or_status = _good_quote

    async def _good_plain(input_mint, output_mint, amount_atomic, slippage_bps=100):
        q, _ = await _good_quote(input_mint, output_mint, amount_atomic, slippage_bps)
        return q

    jup.quote = _good_plain

    await tracker.check_once()
    assert tracker.open_count() == 0
    trade = tracker.stats()
    assert trade["closed"] == 1

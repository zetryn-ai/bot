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


async def _open(tracker, size_sol=0.2, tp=0.3, sl=0.15, max_hold=1800, route=""):
    pos = Position(
        mint="MintA",
        symbol="AAA",
        size_sol=size_sol,
        tokens_atomic=1_000_000,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        max_hold_s=max_hold,
        confidence=0.8,
        route=route,
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


@pytest.mark.asyncio
async def test_reentry_cooldown_blocks_then_expires():
    clock = _Clock()
    jup = _FakeJupiter(current_sol=0.30)  # +50% -> instant TP
    risk = RiskManager(RiskConfig())
    tracker = PositionTracker(PaperExecutor(jup), jup, risk, now_fn=clock, reentry_cooldown_s=14400)
    await _open(tracker)
    await tracker.check_once()  # TP close -> cooldown starts
    assert tracker.open_count() == 0
    assert tracker.in_cooldown("MintA") is True
    assert tracker.in_cooldown("MintB") is False

    clock.t += 14401
    assert tracker.in_cooldown("MintA") is False  # expired


@pytest.mark.asyncio
async def test_cooldown_disabled_by_default():
    clock = _Clock()
    jup = _FakeJupiter(current_sol=0.30)
    risk = RiskManager(RiskConfig())
    tracker = PositionTracker(PaperExecutor(jup), jup, risk, now_fn=clock)
    await _open(tracker)
    await tracker.check_once()
    assert tracker.in_cooldown("MintA") is False


# ── M10.1: partial TP via tracker ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_tp_sells_half_and_keeps_riding():
    from zetryn_bot.execution.lifecycle import LifecycleEngine

    class _PropJupiter:
        """Out-amount scales with the tokens sold (partials sell fractions)."""

        async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
            return Quote(
                in_amount=amount_atomic,
                out_amount=sol_to_lamports(0.27 * amount_atomic / 1_000_000),
                price_impact_pct=0.0,
            )

    clock = _Clock()
    jup = _PropJupiter()  # +35% on a 0.2 SOL basis at full size
    risk = RiskManager(RiskConfig())
    lifecycle = LifecycleEngine(
        take_profit_pct=0.30,
        stop_loss_pct=0.15,
        max_hold_s=1800.0,
        tp_ladder=[(0.30, 0.5), (1.00, 1.0)],
    )
    tracker = PositionTracker(PaperExecutor(jup), jup, risk, now_fn=clock, lifecycle=lifecycle)
    await _open(tracker, size_sol=0.2)

    await tracker.check_once()
    assert tracker.open_count() == 1  # remainder still open
    pos = tracker._open["MintA"]
    assert pos.tokens_atomic == 500_000
    assert pos.size_sol == pytest.approx(0.1)
    assert pos.take_profit_pct == pytest.approx(1.0)  # bar retargets to next rung
    s = tracker.stats()
    assert s["closed"] == 1  # the partial slice is a realized trade
    assert s["total_pnl_sol"] == pytest.approx(0.035, rel=0.05)  # 0.135 out vs 0.1 basis

    # Same price next sweep: rung already hit -> no second partial.
    await tracker.check_once()
    assert tracker.open_count() == 1
    assert tracker.stats()["closed"] == 1


# ── curve fallback pricing ───────────────────────────────────────────────────


class _NoRouteJupiter:
    async def quote(self, *a, **kw):
        return None

    async def quote_or_status(self, *a, **kw):
        return None, 400  # permanent no-route


class _FakeCurve:
    def __init__(self, sol_out_lamports: int) -> None:
        self.sol_out = sol_out_lamports

    async def sell_quote(self, mint, tokens_atomic):
        return self.sol_out

    async def buy_quote(self, mint, sol_lamports):
        return 1_000_000


@pytest.mark.asyncio
async def test_curve_fallback_prices_and_exits_when_jupiter_has_no_route():
    clock = _Clock()
    jup = _NoRouteJupiter()
    risk = RiskManager(RiskConfig())
    curve = _FakeCurve(sol_to_lamports(0.27))  # +35% -> static TP fires
    tracker = PositionTracker(PaperExecutor(jup, curve=curve), jup, risk, now_fn=clock, curve=curve)
    await _open(tracker, size_sol=0.2, tp=0.3, route="sniper")  # curve fallback = sniper-only
    await tracker.check_once()
    assert tracker.open_count() == 0
    trade = tracker._closed[0]
    assert trade.reason == "take_profit"
    assert trade.exit_sol == pytest.approx(0.27)


@pytest.mark.asyncio
async def test_curve_fallback_ignored_for_non_sniper_routes():
    # Graduation/launch tokens have LEFT the curve — a curve fill would price
    # at the stale initial reserves (the phantom +13 SOL incident 2026-07-15).
    clock = _Clock()
    jup = _NoRouteJupiter()
    risk = RiskManager(RiskConfig())
    curve = _FakeCurve(sol_to_lamports(0.27))
    tracker = PositionTracker(
        PaperExecutor(jup, curve=curve), jup, risk, now_fn=clock, curve=curve, dead_route_after=1
    )
    await _open(tracker, size_sol=0.2, tp=0.3, route="graduation", max_hold=0)
    clock.t += 1
    await tracker.check_once()
    # No curve pricing → treated as a no-route position, not a fabricated win.
    assert not any(t.pnl_sol > 0 for t in tracker._closed)


@pytest.mark.asyncio
async def test_sl_ratchet_locks_profit_after_first_rung():
    from zetryn_bot.execution.lifecycle import LifecycleEngine

    class _MutableJupiter:
        def __init__(self) -> None:
            self.current_sol_full = 0.27  # +35% at full size

        async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
            return Quote(
                in_amount=amount_atomic,
                out_amount=sol_to_lamports(self.current_sol_full * amount_atomic / 1_000_000),
                price_impact_pct=0.0,
            )

    clock = _Clock()
    jup = _MutableJupiter()
    risk = RiskManager(RiskConfig())
    lifecycle = LifecycleEngine(
        take_profit_pct=0.30,
        stop_loss_pct=0.15,
        max_hold_s=1800.0,
        tp_ladder=[(0.30, 0.5), (0.50, 0.5), (1.00, 1.0)],
    )
    tracker = PositionTracker(
        PaperExecutor(jup),
        jup,
        risk,
        now_fn=clock,
        lifecycle=lifecycle,
        sl_ratchet={0.30: 0.05, 0.50: 0.30},
    )
    await _open(tracker, size_sol=0.2)

    await tracker.check_once()  # TP1 fires at +35%
    pos = tracker._open["MintA"]
    assert pos.stop_loss_pct == pytest.approx(-0.05)  # stop ratcheted ABOVE entry
    assert pos.take_profit_pct == pytest.approx(0.50)  # bar targets TP2 next

    # Price falls back to +3% (below the +5% lock) -> remainder closes as a
    # ratchet_stop WINNER instead of riding to -15%.
    jup.current_sol_full = 0.206
    await tracker.check_once()
    assert tracker.open_count() == 0
    trade = tracker._closed[-1]
    assert trade.reason == "ratchet_stop"
    assert trade.pnl_sol > 0


# ── junk-quote guard (Jotchua incident 2026-07-13) ───────────────────────────


@pytest.mark.asyncio
async def test_extreme_quote_needs_two_sweeps_before_acting():
    class _JunkThenSaneJupiter:
        """First sweep quotes a phantom 2820x; later sweeps are sane."""

        def __init__(self) -> None:
            self.calls = 0

        async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
            self.calls += 1
            sol = 84.6 if self.calls == 1 else 0.21 * amount_atomic / 1_000_000
            return Quote(
                in_amount=amount_atomic, out_amount=sol_to_lamports(sol), price_impact_pct=0.0
            )

    clock = _Clock()
    jup = _JunkThenSaneJupiter()
    risk = RiskManager(RiskConfig())
    tracker = PositionTracker(PaperExecutor(jup), jup, risk, now_fn=clock)
    await _open(tracker, size_sol=0.2, tp=0.3)

    await tracker.check_once()  # phantom +42000% -> held for confirmation
    assert tracker.open_count() == 1
    assert tracker.stats()["closed"] == 0

    clock.t += 30
    await tracker.check_once()  # sane +5% -> pending cleared, still open
    assert tracker.open_count() == 1


@pytest.mark.asyncio
async def test_persistent_extreme_quote_is_accepted_second_sweep():
    class _MoonJupiter:
        async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
            return Quote(
                in_amount=amount_atomic,
                out_amount=sol_to_lamports(6.0 * amount_atomic / 1_000_000),  # 30x, persistent
                price_impact_pct=0.0,
            )

    clock = _Clock()
    jup = _MoonJupiter()
    risk = RiskManager(RiskConfig())
    tracker = PositionTracker(PaperExecutor(jup), jup, risk, now_fn=clock)
    await _open(tracker, size_sol=0.2, tp=0.3)

    await tracker.check_once()  # first extreme sweep -> pending
    assert tracker.open_count() == 1
    clock.t += 30
    await tracker.check_once()  # confirmed -> static TP closes at 30x
    assert tracker.open_count() == 0
    assert tracker._closed[-1].pnl_sol > 5.0

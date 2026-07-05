"""Unit tests for RiskManager — gates, sizing, circuit breaker."""

from __future__ import annotations

from datetime import date

from trading.schemas import Decision

from zetryn_bot.execution.risk import RiskConfig, RiskManager
from zetryn_bot.models.token import TokenCandidate


def _cand() -> TokenCandidate:
    return TokenCandidate(address="MintA", symbol="AAA")


def _rm(**over) -> RiskManager:
    cfg = RiskConfig(
        base_size_sol=0.2, min_confidence=0.6, max_positions=3, daily_loss_limit_sol=1.0
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    return RiskManager(cfg)


def _decision(action="alert", confidence=0.8) -> Decision:
    return Decision(action=action, confidence=confidence)


def test_non_alert_is_rejected():
    assert _rm().evaluate(_cand(), _decision(action="watch"), 0) is None


def test_watch_buys_when_in_buy_actions():
    rm = _rm(buy_actions=("alert", "watch"))
    assert rm.evaluate(_cand(), _decision(action="watch", confidence=0.8), 0) is not None
    # skip is still never a buy action here
    assert rm.evaluate(_cand(), _decision(action="skip", confidence=0.9), 0) is None


def test_low_confidence_is_rejected():
    assert _rm().evaluate(_cand(), _decision(confidence=0.5), 0) is None


def test_size_is_base_times_confidence():
    req = _rm().evaluate(_cand(), _decision(confidence=0.75), 0)
    assert req is not None
    assert req.size_sol == round(0.2 * 0.75, 4)


def test_max_positions_blocks():
    rm = _rm(max_positions=2)
    assert rm.evaluate(_cand(), _decision(), 2) is None
    assert rm.evaluate(_cand(), _decision(), 1) is not None


async def test_circuit_breaker_trips_on_daily_loss():
    rm = _rm(daily_loss_limit_sol=0.5)
    assert rm.evaluate(_cand(), _decision(), 0) is not None  # ok before losses
    await rm.record_close(-0.6)  # exceed daily loss limit
    assert rm.evaluate(_cand(), _decision(), 0) is None


def test_max_trade_sol_caps_live_sizing():
    rm = _rm(max_trade_sol=0.05)  # cap below what base*conf would produce
    req = rm.evaluate(_cand(), _decision(confidence=1.0), 0)
    assert req is not None
    assert req.size_sol == 0.05  # capped, not 0.2 * 1.0


def test_max_trade_sol_none_means_no_extra_cap():
    rm = _rm(max_trade_sol=None)
    req = rm.evaluate(_cand(), _decision(confidence=1.0), 0)
    assert req.size_sol == 0.2  # base_size_sol, uncapped (paper mode)


async def test_circuit_breaker_resets_next_day():
    days = [date(2026, 7, 4), date(2026, 7, 4), date(2026, 7, 5)]
    rm = RiskManager(RiskConfig(daily_loss_limit_sol=0.5), today_fn=lambda: days.pop(0))
    await rm.record_close(-0.6)  # day 1
    # day 2 lookup happens inside evaluate -> rolls over, breaker resets
    assert rm.evaluate(_cand(), _decision(), 0) is not None

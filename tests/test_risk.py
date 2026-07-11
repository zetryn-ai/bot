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


def test_require_sources_blocks_unverified_contract():
    rm = RiskManager(RiskConfig(require_sources=("rugcheck",)))
    cand = TokenCandidate(address="MintA", symbol="AAA", sources=["pumpfun_migration", "helius"])
    d = Decision(action="alert", confidence=0.9)
    assert rm.evaluate(cand, d, open_count=0) is None  # rugcheck missing → no buy


def test_require_sources_allows_verified_contract():
    rm = RiskManager(RiskConfig(require_sources=("rugcheck",)))
    cand = TokenCandidate(address="MintA", symbol="AAA", sources=["pumpfun_migration", "rugcheck"])
    d = Decision(action="alert", confidence=0.9)
    assert rm.evaluate(cand, d, open_count=0) is not None


def test_require_sources_empty_disables_check():
    rm = RiskManager(RiskConfig())  # default: no requirement at the dataclass level
    cand = TokenCandidate(address="MintA", symbol="AAA", sources=["pumpfun_migration"])
    d = Decision(action="alert", confidence=0.9)
    assert rm.evaluate(cand, d, open_count=0) is not None


def test_blocked_buy_source_never_buys():
    rm = RiskManager(RiskConfig(blocked_buy_sources=("dexscreener_boost",)))
    cand = TokenCandidate(address="MintA", symbol="AAA", sources=["dexscreener_boost", "rugcheck"])
    d = Decision(action="alert", confidence=0.9)
    assert rm.evaluate(cand, d, open_count=0) is None


def test_source_conf_floor_applies_to_that_source_only():
    rm = RiskManager(RiskConfig(source_conf_floors={"geckoterminal_trending": 0.68}))
    d = Decision(action="alert", confidence=0.65)
    gecko = TokenCandidate(address="MintA", symbol="AAA", sources=["geckoterminal_trending"])
    fresh = TokenCandidate(address="MintB", symbol="BBB", sources=["dexscreener"])
    assert rm.evaluate(gecko, d, open_count=0) is None  # 0.65 < 0.68 floor
    assert rm.evaluate(fresh, d, open_count=0) is not None  # other sources use global floor
    d_high = Decision(action="alert", confidence=0.70)
    assert rm.evaluate(gecko, d_high, open_count=0) is not None

"""M12 strategy-first routing: source membership + per-route entry gates."""

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.routing.gates import (
    LAUNCH_SOURCES,
    MOMENTUM_SOURCES,
    is_social_source,
    launch_gate,
    momentum_gate,
    social_gate,
)


def _c(**kw) -> TokenCandidate:
    return TokenCandidate(address="MintA", **kw)


def test_source_membership_covers_every_scanner_label():
    # Labels verified in scanner code 2026-07-13.
    assert "geckoterminal_trending" in MOMENTUM_SOURCES
    assert "birdeye_trending" in MOMENTUM_SOURCES
    for s in ("geckoterminal_new", "dexscreener", "raydium", "birdeye_new", "birdeye_new_pumpfun"):
        assert s in LAUNCH_SOURCES
    assert is_social_source("telegram_alpha")
    assert is_social_source("telegram_trending")
    assert not is_social_source("dexscreener_boost")  # falls to 'other'


def test_momentum_gate_blocks_laggards_and_dumps():
    ok, why = momentum_gate(
        _c(price_change_5m_pct=2, price_change_1h_pct=15, price_change_6h_pct=40),
        max_1h_pct=80,
        max_6h_pct=150,
    )
    assert ok, why  # young, rising: pass

    ok, why = momentum_gate(
        _c(price_change_5m_pct=-1, price_change_1h_pct=15, price_change_6h_pct=40),
        max_1h_pct=80,
        max_6h_pct=150,
    )
    assert not ok and "stalled" in why

    ok, why = momentum_gate(
        _c(price_change_5m_pct=2, price_change_1h_pct=15, price_change_6h_pct=400),
        max_1h_pct=80,
        max_6h_pct=150,
    )
    assert not ok and "laggard" in why

    ok, why = momentum_gate(
        _c(
            price_change_5m_pct=2,
            price_change_1h_pct=15,
            price_change_6h_pct=40,
            buyers_5m=3,
            sellers_5m=9,
        ),
        max_1h_pct=80,
        max_6h_pct=150,
    )
    assert not ok and "sellers" in why

    ok, why = momentum_gate(
        _c(
            price_change_5m_pct=2,
            price_change_1h_pct=15,
            price_change_6h_pct=40,
            buys_5m=4,
            sells_5m=10,
        ),
        max_1h_pct=80,
        max_6h_pct=150,
    )
    assert not ok and "buy ratio" in why


def test_momentum_gate_passes_through_when_no_data():
    ok, _ = momentum_gate(_c(), max_1h_pct=80, max_6h_pct=150)
    assert ok  # unknown is not proof of lagging — the analyst judges


def test_launch_and_social_gates_reject_old_tokens():
    assert launch_gate(_c(age_seconds=600), max_age_s=7200)[0]
    assert not launch_gate(_c(age_seconds=90000), max_age_s=7200)[0]
    assert social_gate(_c(age_seconds=3000), max_age_s=21600)[0]
    assert not social_gate(_c(age_seconds=90000), max_age_s=21600)[0]

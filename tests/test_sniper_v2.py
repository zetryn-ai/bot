"""Sniper v2 wiring tests (v0.13.0) — adapter mapping, GMGN taxes,
pumpfun_meta enricher, and the SNIPER_* settings block.

The scoring engine itself lives in zetryn-trading (tested there); these
tests pin the bot-side plumbing that feeds it.
"""

from __future__ import annotations

import pytest

from zetryn_bot.adapters.token_input import to_token_input
from zetryn_bot.config import Settings
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners.enrichers.gmgn_openapi import _apply_taxes
from zetryn_bot.scanners.enrichers.pumpfun_meta import PumpfunMetaEnricher

MINT = "So11111111111111111111111111111111111111112"


# ─── adapter: TokenCandidate → TokenInput ───────────────────────────────────


def test_adapter_maps_sniper_v2_fields():
    candidate = TokenCandidate(
        address=MINT,
        symbol="TEST",
        sources=["pumpfun.ws"],
        fdv_usd=250_000.0,
        buy_tax_pct=2.0,
        sell_tax_pct=7.5,
        bonding_curve_sol=12.5,
        curve_velocity_sol_per_min=3.2,
        has_website=True,
        has_twitter=True,
        has_telegram=False,
    )
    token = to_token_input(candidate)
    assert token.market.fdv == 250_000.0
    assert token.contract.buy_tax_pct == 2.0
    assert token.contract.sell_tax_pct == 7.5
    assert token.pumpfun is not None
    assert token.pumpfun.curve_sol == 12.5
    assert token.pumpfun.curve_velocity_sol_per_min == 3.2
    assert token.pumpfun.has_website is True
    assert token.pumpfun.has_twitter is True
    assert token.pumpfun.has_telegram is False


def test_adapter_no_pumpfun_block_for_other_sources():
    candidate = TokenCandidate(address=MINT, sources=["dexscreener.boost"])
    assert to_token_input(candidate).pumpfun is None


# ─── GMGN security → tax percent ────────────────────────────────────────────


def test_apply_taxes_fraction_and_percent_forms():
    candidate = TokenCandidate(address=MINT)
    # 0..1 fraction form → percent
    updated = _apply_taxes(candidate, {"buy_tax": 0.03, "sell_tax": 0.1})
    assert updated.buy_tax_pct == pytest.approx(3.0)
    assert updated.sell_tax_pct == pytest.approx(10.0)
    # already-percent form passes through
    updated = _apply_taxes(candidate, {"buyTax": 5, "sellTax": 12})
    assert updated.buy_tax_pct == 5.0
    assert updated.sell_tax_pct == 12.0


def test_apply_taxes_missing_or_junk_leaves_zero():
    candidate = TokenCandidate(address=MINT)
    assert _apply_taxes(candidate, None) is candidate
    updated = _apply_taxes(candidate, {"buy_tax": "n/a", "other": 1})
    assert updated.buy_tax_pct == 0.0
    assert updated.sell_tax_pct == 0.0


# ─── pumpfun_meta enricher ──────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, status: int, payload: dict):
        self._resp = _FakeResponse(status, payload)
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._resp


PUMP_PAYLOAD = {
    "twitter": "https://x.com/test",
    "telegram": None,
    "website": "https://test.example",
    "virtual_sol_reserves": int(42.5e9),  # 42.5 SOL virtual → 12.5 real
    "usd_market_cap": 55_000.0,
}


@pytest.mark.asyncio
async def test_pumpfun_meta_fills_socials_and_velocity():
    candidate = TokenCandidate(address=MINT, sources=["pumpfun.ws"], age_seconds=300)
    session = _FakeSession(200, PUMP_PAYLOAD)
    updated = await PumpfunMetaEnricher().enrich(MINT, candidate, session)

    assert updated.has_twitter is True
    assert updated.has_website is True
    assert updated.has_telegram is False
    assert updated.bonding_curve_sol == pytest.approx(12.5)
    # 12.5 SOL over 5 minutes = 2.5 SOL/min
    assert updated.curve_velocity_sol_per_min == pytest.approx(2.5)
    assert updated.market_cap_usd == pytest.approx(55_000.0)
    assert "pumpfun_meta" in updated.sources
    assert candidate.has_twitter is False  # input never mutated


@pytest.mark.asyncio
async def test_pumpfun_meta_skips_non_pumpfun_candidates():
    candidate = TokenCandidate(address=MINT, sources=["geckoterminal.trending"])
    session = _FakeSession(200, PUMP_PAYLOAD)
    result = await PumpfunMetaEnricher().enrich(MINT, candidate, session)
    assert result is candidate
    assert session.calls == []


@pytest.mark.asyncio
async def test_pumpfun_meta_non_200_passthrough():
    candidate = TokenCandidate(address=MINT, sources=["pumpfun.ws"])
    result = await PumpfunMetaEnricher().enrich(MINT, candidate, _FakeSession(530, {}))
    assert result is candidate


# ─── settings: SNIPER_* block ───────────────────────────────────────────────


def test_sniper_v2_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.sniper_use_scoring is True
    assert settings.sniper_min_liquidity_usd == 1500
    assert settings.sniper_max_liquidity_usd == 40000
    assert settings.sniper_max_fdv_usd == 1_000_000
    assert settings.sniper_max_tax_pct == 5.0
    assert settings.sniper_score_auto_buy == 90
    assert settings.sniper_stagnation_after_s == 300
    assert settings.parsed_route_max_hold_s()["sniper"] == 900

"""Unit tests for the pure adapter — no network, no framework agent."""

from __future__ import annotations

from zetryn_bot.adapters.token_input import to_token_input
from zetryn_bot.models.token import TokenCandidate


def _candidate(**overrides) -> TokenCandidate:
    defaults = {"address": "Mint111", "symbol": "FOO", "name": "Foo Coin"}
    defaults.update(overrides)
    return TokenCandidate(**defaults)


def test_maps_identity_and_market_fields():
    candidate = _candidate(
        sources=["dexscreener.new_pairs"],
        liquidity_usd=25_000.0,
        market_cap_usd=100_000.0,
        price_usd=0.002,
        age_seconds=120,
    )
    token_input = to_token_input(candidate)

    assert token_input.mint == "Mint111"
    assert token_input.symbol == "FOO"
    assert token_input.source == "dexscreener"
    assert token_input.market.liquidity_usd == 25_000.0
    assert token_input.market.mcap == 100_000.0
    assert token_input.market.price == 0.002
    assert token_input.market.age_seconds == 120.0


def test_unmapped_source_falls_back_to_manual():
    candidate = _candidate(sources=["telegram"])
    token_input = to_token_input(candidate)
    assert token_input.source == "manual"


def test_no_sources_falls_back_to_manual():
    candidate = _candidate(sources=[])
    assert to_token_input(candidate).source == "manual"


def test_holder_percentages_rescaled_from_0_100_to_0_1():
    candidate = _candidate(top10_holder_pct=45.0, dev_wallet_pct=12.5)
    token_input = to_token_input(candidate)
    assert token_input.holders.top10_pct == 0.45
    assert token_input.holders.dev_pct == 0.125


def test_pumpfun_data_only_populated_for_pumpfun_source():
    pumpfun_candidate = _candidate(
        sources=["pumpfun.ws"],
        creator_wallet="Creator1",
        creator_sol_buy=2.5,
        bonding_curve_pct=63.0,
        is_mayhem_mode=True,
    )
    token_input = to_token_input(pumpfun_candidate)
    assert token_input.pumpfun is not None
    assert token_input.pumpfun.creator_wallet == "Creator1"
    assert token_input.pumpfun.bonding_curve_pct == 63.0

    non_pumpfun = to_token_input(_candidate(sources=["birdeye.trending"]))
    assert non_pumpfun.pumpfun is None


def test_safety_flags_pass_through_to_contract():
    candidate = _candidate(
        is_honeypot=True,
        is_mintable=True,
        is_freezable=False,
        bundled_supply=True,
        dev_rug_history=True,
    )
    contract = to_token_input(candidate).contract
    assert contract.is_honeypot is True
    assert contract.mint_authority_active is True
    assert contract.freeze_authority_active is False
    assert contract.bundled_supply is True
    assert contract.dev_rug_history is True
    assert contract.is_dangerous is True


def test_wallet_intel_maps_gmgn_counts():
    candidate = _candidate(
        gmgn_safety_score=72.0,
        smart_wallet_buys=4,
        gmgn_smart_wallets=3,
        gmgn_kol_wallets=1,
        gmgn_sniper_wallets=9,
        gmgn_bundler_wallets=0,
        gmgn_whale_wallets=2,
    )
    wallets = to_token_input(candidate).wallets
    assert wallets.safety_score == 72.0
    assert wallets.smart_wallet_buys == 4
    assert wallets.smart_wallet_count == 3
    assert wallets.kol_wallet_count == 1
    assert wallets.sniper_wallet_count == 9
    assert wallets.whale_wallet_count == 2


def test_zero_gmgn_safety_score_becomes_none_not_available():
    candidate = _candidate(gmgn_safety_score=0.0)
    assert to_token_input(candidate).wallets.safety_score is None


def test_twitter_signals_map_to_top_influencer_as_handle():
    candidate = _candidate(
        twitter_top_influencer_handle="@whale",
        twitter_top_influencer_followers=50_000,
        twitter_mentions_1h=120,
        twitter_sentiment="bullish",
        twitter_engagement=340,
        twitter_velocity_tpm=4.2,
    )
    twitter = to_token_input(candidate).social.twitter
    assert twitter.handle == "@whale"
    assert twitter.followers == 50_000
    assert twitter.mentions_1h == 120
    assert twitter.sentiment == "bullish"
    assert twitter.velocity_tpm == 4.2

"""Bridge from the bot's :class:`TokenCandidate` to the framework's ``TokenInput``.

Pure mapping — no I/O, no enrichment. The bot's ``pipeline/enrich.py`` fills in
``TokenCandidate`` first; this module only reshapes what's already there into
the schema ``zetryn-trading`` agents expect. Framework never imports bot types,
so this is the one place the two schemas meet.
"""

from __future__ import annotations

from trading.schemas import (
    ActivityData,
    ContractData,
    HolderData,
    MarketData,
    PumpfunData,
    SocialData,
    TelegramData,
    TokenInput,
    TokenSource,
    TwitterData,
    WalletIntel,
)

from zetryn_bot.models.token import TokenCandidate

# Bot scanner ``name`` prefixes (e.g. "birdeye.trending") map to the framework's
# narrower TokenSource literal. Sources the framework doesn't model (telegram,
# geckoterminal) fall back to "manual" — see TokenSource in trading.schemas.
_SOURCE_PREFIX_MAP: dict[str, TokenSource] = {
    "pumpfun": "pumpfun_ws",
    "dexscreener": "dexscreener",
    "raydium": "raydium",
    "birdeye": "birdeye",
}


def _map_source(sources: list[str]) -> TokenSource:
    for source in sources:
        prefix = source.split(".", 1)[0]
        if prefix in _SOURCE_PREFIX_MAP:
            return _SOURCE_PREFIX_MAP[prefix]
    return "manual"


def to_token_input(candidate: TokenCandidate) -> TokenInput:
    """Map an enriched ``TokenCandidate`` to the framework's ``TokenInput``.

    ``candidate`` should already be fully enriched (see
    :func:`zetryn_bot.pipeline.enrich.enrich_candidate`) — this function does
    not call the network or mutate its argument.
    """
    source = _map_source(candidate.sources)

    return TokenInput(
        mint=candidate.address,
        symbol=candidate.symbol,
        name=candidate.name,
        source=source,
        market=MarketData(
            mcap=candidate.market_cap_usd,
            fdv=candidate.fdv_usd,
            liquidity_usd=candidate.liquidity_usd,
            volume_1h=candidate.volume_1h_usd,
            volume_24h=candidate.volume_24h_usd,
            price=candidate.price_usd or None,
            price_change_5m_pct=candidate.price_change_5m_pct,
            price_change_1h_pct=candidate.price_change_1h_pct,
            price_change_6h_pct=candidate.price_change_6h_pct,
            age_seconds=float(candidate.age_seconds),
            txns_1h=candidate.txns_1h,
        ),
        activity=ActivityData(
            volume_1m_usd=candidate.volume_1m_usd,
            volume_5m_usd=candidate.volume_5m_usd,
            volume_1h_usd=candidate.volume_1h_usd,
            txns_1m=candidate.txns_1m,
            txns_5m=candidate.txns_5m,
            buys_5m=candidate.buys_5m,
            sells_5m=candidate.sells_5m,
        ),
        holders=HolderData(
            count=candidate.holder_count,
            # Bot stores holder concentration as a 0..100 percentage
            # (see helius.py); the framework's HolderData expects 0..1.
            top10_pct=candidate.top10_holder_pct / 100.0,
            dev_pct=candidate.dev_wallet_pct / 100.0,
        ),
        contract=ContractData(
            buy_tax_pct=candidate.buy_tax_pct,
            sell_tax_pct=candidate.sell_tax_pct,
            mint_authority_active=candidate.is_mintable,
            freeze_authority_active=candidate.is_freezable,
            is_honeypot=candidate.is_honeypot,
            bundled_supply=candidate.bundled_supply,
            dev_rug_history=candidate.dev_rug_history,
        ),
        wallets=WalletIntel(
            safety_score=candidate.gmgn_safety_score or None,
            smart_wallet_buys=candidate.smart_wallet_buys,
            smart_wallet_count=candidate.gmgn_smart_wallets,
            kol_wallet_count=candidate.gmgn_kol_wallets,
            sniper_wallet_count=candidate.gmgn_sniper_wallets,
            bundler_wallet_count=candidate.gmgn_bundler_wallets,
            whale_wallet_count=candidate.gmgn_whale_wallets,
        ),
        pumpfun=(
            PumpfunData(
                creator_wallet=candidate.creator_wallet or None,
                creator_sol_buy=candidate.creator_sol_buy,
                bonding_curve_pct=candidate.bonding_curve_pct,
                is_mayhem_mode=candidate.is_mayhem_mode,
                curve_sol=candidate.bonding_curve_sol,
                curve_velocity_sol_per_min=candidate.curve_velocity_sol_per_min,
                has_website=candidate.has_website,
                has_twitter=candidate.has_twitter,
                has_telegram=candidate.has_telegram,
            )
            if source == "pumpfun_ws"
            else None
        ),
        social=SocialData(
            # TwitterData has no slot for "influencer count" or a generic
            # mention count separate from the top handle — the framework
            # tracks one representative handle, not the full pool. We use
            # the bot's top influencer as that representative and drop
            # ``twitter_influencer_count`` (lossy, but there's no target field).
            twitter=TwitterData(
                handle=candidate.twitter_top_influencer_handle or None,
                followers=candidate.twitter_top_influencer_followers,
                mentions_1h=candidate.twitter_mentions_1h,
                mention_growth_pct=candidate.twitter_mention_growth_pct,
                sentiment=candidate.twitter_sentiment or None,
                engagement=candidate.twitter_engagement,
                velocity_tpm=candidate.twitter_velocity_tpm,
            ),
            telegram=TelegramData(),
            boost_amount=candidate.boost_amount,
            boost_total_amount=candidate.boost_total_amount,
        ),
    )

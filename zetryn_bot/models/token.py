from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TokenCandidate(BaseModel):
    # Identity
    address: str
    symbol: str = ""
    name: str = ""
    created_at: datetime | None = None

    # Source tracking
    sources: list[str] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)

    # On-chain metrics
    age_seconds: int = 0
    liquidity_usd: float = 0.0
    market_cap_usd: float = 0.0
    price_usd: float = 0.0

    # Price momentum (percent change; set by dexscreener/geckoterminal). The
    # AI analyst uses these to tell "rising" from "already peaked" — a single
    # price snapshot cannot express direction. 0.0 = unknown/not provided
    # (e.g. pump.fun launches seconds old have no history yet).
    price_change_5m_pct: float = 0.0
    price_change_1h_pct: float = 0.0
    price_change_6h_pct: float = 0.0

    # Volume & activity
    volume_1m_usd: float = 0.0
    volume_5m_usd: float = 0.0
    volume_1h_usd: float = 0.0
    txns_1m: int = 0
    txns_5m: int = 0
    buys_5m: int = 0
    sells_5m: int = 0
    trades_total: int = 0

    # Holder data (via Helius)
    holder_count: int = 0
    top10_holder_pct: float = 0.0
    dev_wallet_pct: float = 0.0

    # Safety signals
    is_honeypot: bool = False
    is_mintable: bool = False
    is_freezable: bool = False
    bundled_supply: bool = False

    # GMGN signals
    gmgn_safety_score: float = 0.0
    smart_wallet_buys: int = 0
    dev_rug_history: bool = False

    # GMGN OpenAPI entity-labeled wallet counts (in-memory / Redis only — not DB columns)
    gmgn_smart_wallets: int = 0  # smart_degen — proven profitable wallets
    gmgn_kol_wallets: int = 0  # renowned — known KOLs
    gmgn_sniper_wallets: int = 0  # bought at token open
    gmgn_bundler_wallets: int = 0  # bot-bundled buys (manipulation risk)
    gmgn_whale_wallets: int = 0  # large holders

    # Pump.fun bonding curve signals (pumpfun_ws only)
    creator_sol_buy: float = 0.0  # SOL spent by creator at launch
    bonding_curve_sol: float = 0.0  # real SOL in bonding curve (vSol - 30 virtual)
    bonding_curve_pct: float = 0.0  # progress toward graduation (0-100%)
    is_mayhem_mode: bool = False  # pump.fun turbo/mayhem mode flag
    creator_wallet: str = ""  # traderPublicKey (creator address)

    # DexScreener boost signals — paid promotion = team commitment signal
    boost_amount: float = 0.0  # current boost (USD, decays over time)
    boost_total_amount: float = 0.0  # cumulative boost ever spent (USD)

    # Twitter social signals (set by _enrich_twitter after L2 filter)
    twitter_mentions_1h: int = 0
    twitter_mention_growth_pct: float = 0.0
    twitter_influencer_count: int = 0
    twitter_top_influencer_handle: str = ""
    twitter_top_influencer_followers: int = 0
    twitter_sentiment: str = ""  # "bullish" | "bearish" | "neutral" | ""
    twitter_engagement: int = 0
    twitter_velocity_tpm: float = 0.0  # tweets per minute (last 30m)

    # AI scoring (set by pipeline)
    ai_score: float = 0.0
    ai_decision: str = ""
    ai_skip_reason: str = ""

    @property
    def buy_ratio_5m(self) -> float:
        total = self.buys_5m + self.sells_5m
        return self.buys_5m / total if total > 0 else 0.0

    def merge_sources(self, other: TokenCandidate) -> None:
        """Merge data from another candidate of same token (multi-source dedup)."""
        for src in other.sources:
            if src not in self.sources:
                self.sources.append(src)
        # Take max values for numeric fields — more data is better
        self.liquidity_usd = max(self.liquidity_usd, other.liquidity_usd)
        self.market_cap_usd = max(self.market_cap_usd, other.market_cap_usd)
        self.volume_5m_usd = max(self.volume_5m_usd, other.volume_5m_usd)
        self.volume_1h_usd = max(self.volume_1h_usd, other.volume_1h_usd)
        self.txns_5m = max(self.txns_5m, other.txns_5m)
        self.buys_5m = max(self.buys_5m, other.buys_5m)
        self.sells_5m = max(self.sells_5m, other.sells_5m)
        self.holder_count = max(self.holder_count, other.holder_count)
        self.smart_wallet_buys = max(self.smart_wallet_buys, other.smart_wallet_buys)
        # GMGN score: take whichever is non-zero
        if other.gmgn_safety_score > 0:
            self.gmgn_safety_score = other.gmgn_safety_score
        # Safety: any True wins
        self.is_honeypot = self.is_honeypot or other.is_honeypot
        self.is_mintable = self.is_mintable or other.is_mintable
        self.is_freezable = self.is_freezable or other.is_freezable
        self.bundled_supply = self.bundled_supply or other.bundled_supply
        self.dev_rug_history = self.dev_rug_history or other.dev_rug_history

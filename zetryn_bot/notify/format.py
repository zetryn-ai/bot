"""Rich Telegram message formatting for trade notifications.

Token/decision detail is captured once at buy-time (``build_trade_meta``,
called from ``RiskManager.evaluate`` where the original ``TokenCandidate``
and ``Decision`` are still in scope) and carried as a plain string on
``SwapRequest``/``Position``. By the time a position closes, the pipeline
has moved on to other candidates — the original objects are gone — so
building the detail block once and reusing it at close time is cheaper than
threading the framework's decision objects through the whole position
lifecycle. Not persisted to the DB (M6): a restart loses this detail on any
position that was already open, which only affects notification richness,
never trading behavior.
"""

from __future__ import annotations

from trading.schemas import Decision

from zetryn_bot.execution.executor import ClosedTrade, Position
from zetryn_bot.models.token import TokenCandidate


def build_trade_meta(candidate: TokenCandidate, decision: Decision) -> str:
    """Render the token + decision detail block shown on open/close notifications."""
    lines = [
        f"Token: {candidate.symbol or '?'} ({candidate.name or 'unknown name'})",
        f"Mint: {candidate.address}",
        f"Sources: {', '.join(candidate.sources) or '?'} | age={candidate.age_seconds}s",
        f"Price: ${candidate.price_usd:.8f} | Liquidity: ${candidate.liquidity_usd:,.0f} "
        f"| MCap: ${candidate.market_cap_usd:,.0f} | Vol1h: ${candidate.volume_1h_usd:,.0f}",
        f"Holders: {candidate.holder_count} | Top10: {candidate.top10_holder_pct:.1f}% "
        f"| Dev wallet: {candidate.dev_wallet_pct:.1f}%",
        f"Safety: honeypot={candidate.is_honeypot} mintable={candidate.is_mintable} "
        f"freezable={candidate.is_freezable} bundled={candidate.bundled_supply}",
        f"GMGN: safety={candidate.gmgn_safety_score:.0f}/100 smart_wallets={candidate.smart_wallet_buys} "
        f"dev_rug_history={candidate.dev_rug_history}",
    ]
    if candidate.twitter_mentions_1h or candidate.twitter_sentiment:
        lines.append(
            f"Twitter: sentiment={candidate.twitter_sentiment or '?'} "
            f"mentions_1h={candidate.twitter_mentions_1h} "
            f"influencers={candidate.twitter_influencer_count} "
            f"(top: @{candidate.twitter_top_influencer_handle or '-'} "
            f"{candidate.twitter_top_influencer_followers} followers)"
        )
    lines.append(
        f"Decision: action={decision.action} confidence={decision.confidence:.2f} "
        f"scores={ {k: round(v, 2) for k, v in decision.scores.items()} }"
    )
    analysis = decision.analysis
    if analysis is not None:
        reason = (analysis.reasoning or "").strip().replace("\n", " ")[:500]
        lines.append(
            f"AI: final_score={analysis.final_score:.2f} recommendation={analysis.recommendation}"
        )
        if reason:
            lines.append(f"AI reasoning: {reason}")
    else:
        lines.append("AI: skipped (rule-only / hard-gate reject)")
    if decision.reasons:
        lines.append(f"Reasons: {'; '.join(decision.reasons)}")
    return "\n".join(lines)


def format_open(position: Position) -> str:
    header = (
        f"\U0001f7e2 OPENED — size={position.size_sol:.4f} SOL "
        f"(TP={position.take_profit_pct:.0%} SL={position.stop_loss_pct:.0%} "
        f"max_hold={position.max_hold_s / 60:.0f}min)\n\n"
    )
    return header + (position.meta or f"Token: {position.symbol or position.mint}")


def format_close(position: Position, trade: ClosedTrade, held_s: float) -> str:
    emoji = "\U0001f7e2" if trade.pnl_sol >= 0 else "\U0001f534"
    header = (
        f"{emoji} CLOSED — reason={trade.reason} pnl={trade.pnl_sol:+.4f} SOL "
        f"({trade.pnl_pct:+.1%}) exit={trade.exit_sol:.4f} SOL held={held_s / 60:.1f}min\n\n"
    )
    return header + (position.meta or f"Token: {position.symbol or position.mint}")

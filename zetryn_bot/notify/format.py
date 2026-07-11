"""Rich Telegram message formatting for trade notifications.

Token/decision detail is captured once at buy-time (``build_trade_meta``,
called from ``RiskManager.evaluate`` where the original ``TokenCandidate``
and ``Decision`` are still in scope) and carried as a plain string on
``SwapRequest``/``Position``. By the time a position closes, the pipeline
has moved on to other candidates — the original objects are gone — so
building the detail block once and reusing it at open time is cheaper than
threading the framework's decision objects through the whole position
lifecycle. Not persisted to the DB (M6): a restart loses this detail on any
position that was already open, which only affects notification richness,
never trading behavior.

``format_close`` deliberately does NOT repeat the full token/AI detail
block — that was already sent when the position opened. It only carries
what's new: exit reason, PnL, and how long the position was held.
"""

from __future__ import annotations

from trading.schemas import Decision

from zetryn_bot.execution.executor import ClosedTrade, Position
from zetryn_bot.models.token import TokenCandidate


def _ok(is_bad: bool) -> str:
    """❌ when the flag signals danger, ✅ when it's clear."""
    return "❌" if is_bad else "✅"


def build_trade_meta(candidate: TokenCandidate, decision: Decision) -> str:
    """Render the token + decision detail block shown on the open notification."""
    lines = [
        f"🪙 {candidate.symbol or '?'} ({candidate.name or 'unknown name'})",
        f"📍 {candidate.address}",
        f"🔎 Sources: {', '.join(candidate.sources) or '?'} · age {candidate.age_seconds}s",
        "",
        "📊 Market",
        f"  Price: ${candidate.price_usd:.8f}",
        f"  Liquidity: ${candidate.liquidity_usd:,.0f} · MCap: ${candidate.market_cap_usd:,.0f}",
        f"  Volume 1h: ${candidate.volume_1h_usd:,.0f}",
        "",
        "👥 Holders",
        f"  Count: {candidate.holder_count} · Top10: {candidate.top10_holder_pct:.1f}% "
        f"· Dev wallet: {candidate.dev_wallet_pct:.1f}%",
        "",
        "🛡️ Safety",
        f"  Not honeypot: {_ok(candidate.is_honeypot)}  Not mintable: {_ok(candidate.is_mintable)}",
        f"  Not freezable: {_ok(candidate.is_freezable)}  Not bundled: {_ok(candidate.bundled_supply)}",
        f"  No rug history: {_ok(candidate.dev_rug_history)}",
        f"  GMGN safety score: {candidate.gmgn_safety_score:.0f}/100 · "
        f"Smart wallets buying: {candidate.smart_wallet_buys}",
    ]
    if candidate.twitter_mentions_1h or candidate.twitter_sentiment:
        lines += [
            "",
            "🐦 Twitter",
            f"  Sentiment: {candidate.twitter_sentiment or '?'} · "
            f"Mentions/1h: {candidate.twitter_mentions_1h}",
            f"  Top influencer: @{candidate.twitter_top_influencer_handle or '-'} "
            f"({candidate.twitter_top_influencer_followers:,} followers)",
        ]
    lines += ["", "🧠 Decision"]
    lines.append(f"  Action: {decision.action} · Confidence: {decision.confidence:.2f}")
    scores = " · ".join(f"{k}={v:.2f}" for k, v in decision.scores.items())
    if scores:
        lines.append(f"  Scores: {scores}")
    analysis = decision.analysis
    if analysis is not None:
        reason = (analysis.reasoning or "").strip().replace("\n", " ")[:500]
        lines.append(
            f"  AI score: {analysis.final_score:.2f} · Recommendation: {analysis.recommendation}"
        )
        if reason:
            lines.append(f"  AI reasoning: {reason}")
    else:
        lines.append("  AI: skipped (rule-only / hard-gate reject)")
    if decision.reasons:
        lines.append(f"  Reasons: {'; '.join(decision.reasons)}")
    return "\n".join(lines)


def format_open(position: Position) -> str:
    header = (
        f"🔵 OPENED\n"
        f"Size: {position.size_sol:.4f} SOL · TP {position.take_profit_pct:.0%} · "
        f"SL {position.stop_loss_pct:.0%} · Max hold {position.max_hold_s / 60:.0f}min\n"
        "──────────────\n"
    )
    return header + (position.meta or f"🪙 {position.symbol or position.mint}")


def format_close(position: Position, trade: ClosedTrade, held_s: float) -> str:
    emoji = "🟢" if trade.pnl_sol >= 0 else "🔴"
    name = position.token_name or position.symbol or position.mint
    return (
        f"{emoji} CLOSED — {position.symbol or '?'} ({name})\n"
        f"📍 {position.mint}\n"
        f"Reason: {trade.reason}\n"
        f"PnL: {trade.pnl_sol:+.4f} SOL ({trade.pnl_pct:+.1%})\n"
        f"Exit: {trade.exit_sol:.4f} SOL · Held: {held_s / 60:.1f}min"
    )

"""M12 route membership + per-route entry gates (bot-side pre-filters).

The old "scanner" route was a catch-all: momentum tokens, brand-new pools,
and telegram calls all hit one generalist agent with one config. M12 maps
every source to the strategy its signal shape belongs to; the gates here run
BEFORE enrichment/LLM, so laggards stop costing API budget.

Source labels are the scanners' emitted ``sources[0]`` values (verified in
code 2026-07-13): dexscreener's new-pairs AND token-profile pollers both
emit ``"dexscreener"`` — profile-fresh tokens are launch-shaped, so that
label routes to launch.
"""

from __future__ import annotations

from zetryn_bot.models.token import TokenCandidate

MOMENTUM_SOURCES: frozenset[str] = frozenset({"geckoterminal_trending", "birdeye_trending"})
LAUNCH_SOURCES: frozenset[str] = frozenset(
    {"geckoterminal_new", "dexscreener", "raydium", "birdeye_new", "birdeye_new_pumpfun"}
)


def is_social_source(source: str) -> bool:
    return source.startswith("telegram_")


def momentum_gate(
    candidate: TokenCandidate, *, max_1h_pct: float, max_6h_pct: float
) -> tuple[bool, str]:
    """Anti-laggard gate: buy CONTINUATION, never the local top.

    Trending sources surface tokens AFTER they moved — the measured loss
    pattern was entering at the peak and stopping out in 0-6 minutes. A
    candidate passes only while its move is still young and demand-led.
    When the source ships no momentum data at all, pass through — unknown
    is not proof of lagging; the analyst judges.
    """
    pc5 = candidate.price_change_5m_pct
    pc1 = candidate.price_change_1h_pct
    pc6 = candidate.price_change_6h_pct
    if pc5 == 0 and pc1 == 0 and pc6 == 0:
        return True, ""
    if pc5 <= 0:
        return False, f"momentum stalled (Δ5m {pc5:+.1f}%)"
    if pc1 <= 0:
        return False, f"1h trend negative (Δ1h {pc1:+.1f}%)"
    if pc1 > max_1h_pct:
        return False, f"already ran Δ1h {pc1:+.1f}% > {max_1h_pct:.0f}% (laggard)"
    if pc6 > max_6h_pct:
        return False, f"already ran Δ6h {pc6:+.1f}% > {max_6h_pct:.0f}% (laggard)"
    if candidate.buyers_5m and candidate.sellers_5m >= candidate.buyers_5m:
        return False, (
            f"sellers outnumber buyers ({candidate.sellers_5m} vs {candidate.buyers_5m} 5m)"
        )
    if (candidate.buys_5m + candidate.sells_5m) > 0 and candidate.buy_ratio_5m <= 0.5:
        return False, f"buy ratio {candidate.buy_ratio_5m:.2f} <= 0.5"
    return True, ""


def launch_gate(candidate: TokenCandidate, *, max_age_s: float) -> tuple[bool, str]:
    """Launch route wants YOUNG pools only — old pools are not launches."""
    if candidate.age_seconds and candidate.age_seconds > max_age_s:
        return False, f"pool age {candidate.age_seconds}s > {max_age_s:.0f}s (not a launch)"
    return True, ""


def graduation_gate(candidate: TokenCandidate, *, min_liquidity_usd: float) -> tuple[bool, str]:
    """Don't buy INTO a post-migration dump ("Zetryn Focus" rework 2026-07-17).

    Right after a pump.fun token migrates to a DEX, snipers dump into the fresh
    liquidity — buying then caught a falling knife (07-16: graduation 0% WR,
    -1.03 SOL, fills at -95%). Evaluated AFTER a confirmation delay (see
    ``GraduationPipeline``) so the dump has revealed itself in the data. Enter
    only a graduated token that is NOT dropping and has real liquidity. When a
    signal is unknown (0), pass — the delay + tiny liquidity-capped size are the
    backstop, not a false-negative gate.
    """
    if candidate.liquidity_usd and candidate.liquidity_usd < min_liquidity_usd:
        return False, f"liquidity ${candidate.liquidity_usd:,.0f} < ${min_liquidity_usd:,.0f}"
    if candidate.price_change_5m_pct < 0:
        return False, f"post-migration dump (Δ5m {candidate.price_change_5m_pct:+.1f}%)"
    if candidate.buys_5m and candidate.sells_5m > candidate.buys_5m:
        return False, f"sell pressure ({candidate.sells_5m} sells vs {candidate.buys_5m} buys 5m)"
    return True, ""


def social_gate(candidate: TokenCandidate, *, max_age_s: float) -> tuple[bool, str]:
    """A call on a token that is already old is exit liquidity, not alpha."""
    if candidate.age_seconds and candidate.age_seconds > max_age_s:
        return False, f"called token is {candidate.age_seconds}s old > {max_age_s:.0f}s"
    return True, ""


__all__ = [
    "LAUNCH_SOURCES",
    "MOMENTUM_SOURCES",
    "graduation_gate",
    "is_social_source",
    "launch_gate",
    "momentum_gate",
    "social_gate",
]

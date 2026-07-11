"""RiskManager — the only thing that decides whether and how much to buy.

Bot-side risk/sizing: it does not re-make the alert/watch/skip decision (that is
the framework's, done in M3). It translates an ``alert`` into a sized
``SwapRequest`` behind three guardrails — confidence gate, daily-loss circuit
breaker, and max concurrent positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from loguru import logger
from trading.schemas import Decision

from zetryn_bot.execution.executor import SwapRequest
from zetryn_bot.models.token import TokenCandidate

log = logger.bind(component="execution.risk")


@dataclass
class RiskConfig:
    base_size_sol: float = 0.1
    min_confidence: float = 0.6
    max_positions: int = 5
    daily_loss_limit_sol: float = 1.0
    take_profit_pct: float = 0.30
    stop_loss_pct: float = 0.15
    max_hold_s: float = 1800.0
    # Which Decision actions trigger a buy. Default `alert` only (conservative,
    # for live). Add `watch` to paper-trade the analyst's watchlist and gather
    # outcome data — the AI-first scanner rarely emits `alert` on fresh memecoins
    # (market/social dimensions are almost always weak), so `alert`-only can go a
    # long time with no trades.
    buy_actions: tuple[str, ...] = ("alert",)
    # Absolute per-trade cap for live execution, independent of base_size_sol *
    # confidence — a last-resort guard against a misconfigured base size or
    # confidence when real funds are on the line. None = no extra cap (paper).
    max_trade_sol: float | None = None
    # Enricher names that MUST appear in candidate.sources before a buy.
    # Enrichers fail open (a rate-limited RugCheck leaves the candidate with
    # default "safe" contract flags), so this is the buy-side backstop: never
    # put money on a contract whose safety data never actually arrived.
    # Empty = no requirement (M4/M5 behaviour).
    require_sources: tuple[str, ...] = ()
    # Primary sources (candidate.sources[0], the discovering scanner) whose
    # candidates are never bought. 10h VPS data: dexscreener_boost went 0/3,
    # -0.077 SOL — a boost is PAID promotion by the dev, i.e. an
    # exit-liquidity signal, not a buy signal. Decisions are still logged.
    blocked_buy_sources: tuple[str, ...] = ()
    # Per-primary-source minimum confidence override (stricter than
    # min_confidence). Same 10h data: geckoterminal_trending is a lagging
    # source (tokens trend AFTER pumping) — its sub-0.65 buys were mostly
    # stop-losses while the 0.65+ band won 33% vs 23% below.
    source_conf_floors: dict[str, float] = field(default_factory=dict)


class RiskManager:
    """Gate + size an alert into a SwapRequest, and track daily realized PnL."""

    def __init__(
        self, config: RiskConfig, *, today_fn=date.today, repo=None, notifier=None
    ) -> None:
        from zetryn_bot.notify.telegram import NullNotifier

        self._cfg = config
        self._today_fn = today_fn
        self._day = today_fn()
        self._realized_pnl_today = 0.0
        self._repo = repo  # RiskStateRepo | None — None keeps M4/M5 in-memory behaviour
        self._notifier = notifier or NullNotifier()
        self._breaker_notified_today = False

    async def load(self) -> None:
        """Restore today's realized PnL from the DB so a restart doesn't reset
        the circuit breaker mid-day. No-op without a repo."""
        if self._repo is not None:
            self._realized_pnl_today = await self._repo.load_day(self._day)

    def _roll_day(self) -> None:
        today = self._today_fn()
        if today != self._day:
            self._day = today
            self._realized_pnl_today = 0.0
            self._breaker_notified_today = False

    def evaluate(
        self, candidate: TokenCandidate, decision: Decision, open_count: int
    ) -> SwapRequest | None:
        """Return a SwapRequest to execute, or ``None`` (reason logged)."""
        self._roll_day()

        # Gate 1 — only configured buy actions at/above the confidence floor.
        if (
            decision.action not in self._cfg.buy_actions
            or decision.confidence < self._cfg.min_confidence
        ):
            return None

        # Gate 1b — required enrichment actually happened (fail-closed buys).
        missing = [s for s in self._cfg.require_sources if s not in candidate.sources]
        if missing:
            log.info(
                "skipping {} — required enrichment missing: {} (fail-closed buy policy)",
                candidate.symbol or candidate.address[:8],
                ", ".join(missing),
            )
            return None

        # Gate 1c — per-source buy policy (primary source = discovering scanner).
        primary = candidate.sources[0] if candidate.sources else ""
        if primary in self._cfg.blocked_buy_sources:
            log.info(
                "skipping {} — source {} is buy-blocked (policy)",
                candidate.symbol or candidate.address[:8],
                primary,
            )
            return None
        floor = self._cfg.source_conf_floors.get(primary)
        if floor is not None and decision.confidence < floor:
            log.info(
                "skipping {} — source {} needs confidence >= {} (got {:.2f})",
                candidate.symbol or candidate.address[:8],
                primary,
                floor,
                decision.confidence,
            )
            return None

        # Gate 2 — daily-loss circuit breaker.
        if self._realized_pnl_today <= -self._cfg.daily_loss_limit_sol:
            log.warning(
                "circuit breaker — daily loss {:.4f} SOL <= -{} — skipping {}",
                self._realized_pnl_today,
                self._cfg.daily_loss_limit_sol,
                candidate.symbol or candidate.address[:8],
            )
            return None

        # Gate 3 — max concurrent positions.
        if open_count >= self._cfg.max_positions:
            log.info(
                "max positions ({}) reached — skipping {}",
                self._cfg.max_positions,
                candidate.symbol or candidate.address[:8],
            )
            return None

        size = self._cfg.base_size_sol * decision.confidence
        if self._cfg.max_trade_sol is not None:
            size = min(size, self._cfg.max_trade_sol)
        size = round(size, 4)
        from zetryn_bot.notify.format import build_trade_meta

        return SwapRequest(
            mint=candidate.address,
            symbol=candidate.symbol,
            size_sol=size,
            take_profit_pct=self._cfg.take_profit_pct,
            stop_loss_pct=self._cfg.stop_loss_pct,
            max_hold_s=self._cfg.max_hold_s,
            confidence=decision.confidence,
            meta=build_trade_meta(candidate, decision),
            token_name=candidate.name,
        )

    async def record_close(self, pnl_sol: float) -> None:
        """Feed realized PnL back for the daily circuit breaker, and persist it."""
        self._roll_day()
        self._realized_pnl_today += pnl_sol
        if self._repo is not None:
            await self._repo.save_day(self._day, self._realized_pnl_today)
        tripped = self._realized_pnl_today <= -self._cfg.daily_loss_limit_sol
        if tripped and not self._breaker_notified_today:
            self._breaker_notified_today = True
            await self._notifier.notify(
                f"⛔ circuit breaker TRIPPED — daily PnL {self._realized_pnl_today:+.4f} SOL "
                f"<= -{self._cfg.daily_loss_limit_sol} SOL — no more buys today"
            )

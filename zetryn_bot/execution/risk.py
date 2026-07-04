"""RiskManager — the only thing that decides whether and how much to buy.

Bot-side risk/sizing: it does not re-make the alert/watch/skip decision (that is
the framework's, done in M3). It translates an ``alert`` into a sized
``SwapRequest`` behind three guardrails — confidence gate, daily-loss circuit
breaker, and max concurrent positions.
"""

from __future__ import annotations

from dataclasses import dataclass
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


class RiskManager:
    """Gate + size an alert into a SwapRequest, and track daily realized PnL."""

    def __init__(self, config: RiskConfig, *, today_fn=date.today) -> None:
        self._cfg = config
        self._today_fn = today_fn
        self._day = today_fn()
        self._realized_pnl_today = 0.0

    def _roll_day(self) -> None:
        today = self._today_fn()
        if today != self._day:
            self._day = today
            self._realized_pnl_today = 0.0

    def evaluate(
        self, candidate: TokenCandidate, decision: Decision, open_count: int
    ) -> SwapRequest | None:
        """Return a SwapRequest to execute, or ``None`` (reason logged)."""
        self._roll_day()

        # Gate 1 — only high-conviction alerts.
        if decision.action != "alert" or decision.confidence < self._cfg.min_confidence:
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

        size = round(self._cfg.base_size_sol * decision.confidence, 4)
        return SwapRequest(
            mint=candidate.address,
            symbol=candidate.symbol,
            size_sol=size,
            take_profit_pct=self._cfg.take_profit_pct,
            stop_loss_pct=self._cfg.stop_loss_pct,
            max_hold_s=self._cfg.max_hold_s,
            confidence=decision.confidence,
        )

    def record_close(self, pnl_sol: float) -> None:
        """Feed realized PnL back for the daily circuit breaker."""
        self._roll_day()
        self._realized_pnl_today += pnl_sol

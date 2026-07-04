"""PositionTracker — in-memory open positions + the exit monitor loop.

Holds open positions (one per mint), polls Jupiter for each position's current
SOL value, and auto-closes on take-profit / stop-loss / max-hold. Closed trades
accumulate in memory (durable persistence is M6) and feed realized PnL back to
the RiskManager's circuit breaker.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from loguru import logger

from zetryn_bot.execution.executor import ClosedTrade, Executor, Position
from zetryn_bot.execution.jupiter import SOL_MINT, JupiterQuote, lamports_to_sol
from zetryn_bot.execution.risk import RiskManager

log = logger.bind(component="execution.positions")


class PositionTracker:
    """Owns open/closed paper positions and the exit-monitoring loop."""

    def __init__(
        self,
        executor: Executor,
        jupiter: JupiterQuote,
        risk: RiskManager,
        *,
        poll_interval_s: float = 5.0,
        stats_interval_s: float = 60.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executor = executor
        self._jup = jupiter
        self._risk = risk
        self._poll_interval_s = poll_interval_s
        self._stats_interval_s = stats_interval_s
        self._now = now_fn
        self._open: dict[str, Position] = {}
        self._closed: list[ClosedTrade] = []

    def open_count(self) -> int:
        return len(self._open)

    def holds(self, mint: str) -> bool:
        return mint in self._open

    async def add(self, position: Position) -> None:
        """Register a freshly-opened position, stamping its open time."""
        position.opened_at = self._now()
        self._open[position.mint] = position

    def _exit_reason(self, position: Position, current_sol: float) -> str | None:
        pnl_pct = (
            (current_sol - position.size_sol) / position.size_sol if position.size_sol else 0.0
        )
        if pnl_pct >= position.take_profit_pct:
            return "take_profit"
        if pnl_pct <= -position.stop_loss_pct:
            return "stop_loss"
        if (self._now() - position.opened_at) >= position.max_hold_s:
            return "max_hold"
        return None

    async def check_once(self) -> None:
        """One sweep over open positions: quote, evaluate exits, close if triggered."""
        for mint, position in list(self._open.items()):
            q = await self._jup.quote(mint, SOL_MINT, position.tokens_atomic)
            if q is None:
                continue
            current_sol = lamports_to_sol(q.out_amount)
            reason = self._exit_reason(position, current_sol)
            if reason is None:
                continue
            trade = await self._executor.sell(position, reason)
            if trade is None:
                continue  # sell failed (no quote) — keep the position open, retry next sweep
            del self._open[mint]
            self._closed.append(trade)
            self._risk.record_close(trade.pnl_sol)

    async def monitor_loop(self) -> None:
        """Supervised task: sweep exits every ``poll_interval_s``; log stats periodically."""
        last_stats = self._now()
        while True:
            await self.check_once()
            if (self._now() - last_stats) >= self._stats_interval_s:
                s = self.stats()
                log.info(
                    "positions — open={} closed={} win_rate={:.0%} pnl={:+.4f} SOL",
                    s["open"],
                    s["closed"],
                    s["win_rate"],
                    s["total_pnl_sol"],
                )
                last_stats = self._now()
            await asyncio.sleep(self._poll_interval_s)

    def stats(self) -> dict:
        wins = [t for t in self._closed if t.pnl_sol > 0]
        return {
            "open": len(self._open),
            "closed": len(self._closed),
            "win_rate": len(wins) / len(self._closed) if self._closed else 0.0,
            "total_pnl_sol": sum(t.pnl_sol for t in self._closed),
        }

"""PositionTracker — open positions + the exit monitor loop, with persistence.

Open positions and closed trades persist to Postgres (M6) when a repo is
supplied; without one it behaves exactly as M4/M5 (in-memory only). On startup
`load_and_reconcile` restores open positions from the DB, and in live mode
verifies each against the actual on-chain token balance before resuming — a
mismatch is flagged `needs_review` and excluded from the monitor loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from loguru import logger
from solders.pubkey import Pubkey

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
        repo=None,  # PositionRepo | None — None keeps M4/M5 in-memory behaviour
        execution_mode: str = "paper",
    ) -> None:
        self._executor = executor
        self._jup = jupiter
        self._risk = risk
        self._poll_interval_s = poll_interval_s
        self._stats_interval_s = stats_interval_s
        self._now = now_fn
        self._repo = repo
        self._execution_mode = execution_mode
        self._open: dict[str, Position] = {}
        self._closed: list[ClosedTrade] = []

    def open_count(self) -> int:
        return len(self._open)

    def holds(self, mint: str) -> bool:
        return mint in self._open

    async def add(self, position: Position) -> None:
        """Register a freshly-opened position, stamping its open time + persisting."""
        position.opened_at = self._now()
        self._open[position.mint] = position
        if self._repo is not None:
            await self._repo.save_open(position, self._execution_mode)

    async def load_and_reconcile(self, wallet_pubkey: str | None, rpc) -> None:
        """Restore open positions from the DB. In live mode, verify each against
        the on-chain token balance; a mismatch is marked ``needs_review`` and
        excluded from the monitor loop (never auto-traded on stale data)."""
        if self._repo is None:
            return
        loaded = await self._repo.load_open(now_fn=self._now)
        reviewed = 0
        for pos in loaded:
            if self._execution_mode == "live" and rpc is not None and wallet_pubkey:
                onchain = await rpc.get_token_balance_for_mint(
                    Pubkey.from_string(wallet_pubkey), Pubkey.from_string(pos.mint)
                )
                if onchain != pos.tokens_atomic:
                    log.warning(
                        "RECONCILE MISMATCH {} — db={} on-chain={} — marking needs_review, "
                        "NOT auto-trading",
                        pos.symbol or pos.mint[:8],
                        pos.tokens_atomic,
                        onchain,
                    )
                    await self._repo.mark_needs_review(pos.mint)
                    reviewed += 1
                    continue
            self._open[pos.mint] = pos
        log.info(
            "restored {} open position(s) from DB ({} flagged needs_review)",
            len(self._open),
            reviewed,
        )

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
                continue  # sell failed — keep the position open, retry next sweep
            del self._open[mint]
            self._closed.append(trade)
            if self._repo is not None:
                await self._repo.delete_open(mint)
                await self._repo.save_closed_trade(trade, self._execution_mode)
            await self._risk.record_close(trade.pnl_sol)

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

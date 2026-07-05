"""Executor Protocol + PaperExecutor, plus the shared trade dataclasses.

``PaperExecutor`` fills at real Jupiter quote prices but performs no
transaction and holds no keypair. A future ``LiveExecutor`` (M5) implements the
same Protocol with real swaps, so the risk/position layers never change.

Everything is denominated in SOL (lamports), so token decimals never need
resolving: a buy quotes SOL→mint (tokens received), a sell quotes mint→SOL
(lamports back), and PnL is just ``exit_sol - size_sol``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from loguru import logger

from zetryn_bot.execution.jupiter import (
    SOL_MINT,
    JupiterQuote,
    lamports_to_sol,
    sol_to_lamports,
)

log = logger.bind(component="execution.paper")


@dataclass(frozen=True)
class SwapRequest:
    """A risk-approved intent to buy, with exit params snapshot at entry."""

    mint: str
    symbol: str
    size_sol: float
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_s: float
    confidence: float
    # Human-readable token/decision detail snapshot (RiskManager.evaluate),
    # carried through to Position/ClosedTrade for rich Telegram notifications
    # (M7) — never persisted to the DB, in-memory only.
    meta: str = ""
    token_name: str = ""


@dataclass
class Position:
    """An open paper position. ``opened_at`` is stamped by the PositionTracker."""

    mint: str
    symbol: str
    size_sol: float  # SOL spent at entry
    tokens_atomic: int  # tokens received (atomic units)
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_s: float
    confidence: float
    opened_at: float = 0.0
    meta: str = ""
    token_name: str = ""


@dataclass
class ClosedTrade:
    """A closed paper position with realized PnL."""

    position: Position
    exit_sol: float
    pnl_sol: float
    reason: str
    closed_at: float = field(default_factory=time.monotonic)

    @property
    def pnl_pct(self) -> float:
        return self.pnl_sol / self.position.size_sol if self.position.size_sol else 0.0


@runtime_checkable
class Executor(Protocol):
    """Turns a SwapRequest into a Position (buy) and a Position into a ClosedTrade (sell)."""

    async def buy(self, req: SwapRequest) -> Position | None: ...

    async def sell(self, position: Position, reason: str) -> ClosedTrade | None: ...


class PaperExecutor:
    """Simulated fills at real Jupiter prices — no keypair, no transaction."""

    def __init__(self, jupiter: JupiterQuote, *, slippage_bps: int = 100) -> None:
        self._jup = jupiter
        self._slippage_bps = slippage_bps

    async def buy(self, req: SwapRequest) -> Position | None:
        q = await self._jup.quote(
            SOL_MINT, req.mint, sol_to_lamports(req.size_sol), self._slippage_bps
        )
        if q is None or q.out_amount <= 0:
            log.warning("PAPER BUY {} aborted — no quote", req.symbol or req.mint[:8])
            return None
        log.info(
            "PAPER BUY {} size={:.4f} SOL -> {} tokens (impact {:.2%}, conf {:.2f})",
            req.symbol or req.mint[:8],
            req.size_sol,
            q.out_amount,
            q.price_impact_pct,
            req.confidence,
        )
        return Position(
            mint=req.mint,
            symbol=req.symbol,
            size_sol=req.size_sol,
            tokens_atomic=q.out_amount,
            take_profit_pct=req.take_profit_pct,
            stop_loss_pct=req.stop_loss_pct,
            max_hold_s=req.max_hold_s,
            confidence=req.confidence,
            meta=req.meta,
            token_name=req.token_name,
        )

    async def sell(self, position: Position, reason: str) -> ClosedTrade | None:
        q = await self._jup.quote(
            position.mint, SOL_MINT, position.tokens_atomic, self._slippage_bps
        )
        if q is None:
            log.warning("PAPER SELL {} aborted — no quote (still open)", position.symbol)
            return None
        exit_sol = lamports_to_sol(q.out_amount)
        pnl = exit_sol - position.size_sol
        log.info(
            "PAPER SELL {} -> {:.4f} SOL | pnl={:+.4f} SOL ({:+.1%}) reason={}",
            position.symbol or position.mint[:8],
            exit_sol,
            pnl,
            pnl / position.size_sol if position.size_sol else 0.0,
            reason,
        )
        return ClosedTrade(position=position, exit_sol=exit_sol, pnl_sol=pnl, reason=reason)

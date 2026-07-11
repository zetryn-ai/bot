"""PositionRepo — persist open positions + closed-trade history.

Bridges the in-memory ``Position`` / ``ClosedTrade`` (whose ``opened_at`` /
``closed_at`` are ``time.monotonic()`` values, meaningful only within one
process) to wall-clock ``timestamptz`` columns, and back on load. Without this
bridge, ``max_hold_s`` comparisons would break after every restart.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from zetryn_bot.db.models import ClosedTradeModel, PositionModel
from zetryn_bot.execution.executor import ClosedTrade, Position

log = logger.bind(component="db.positions")


class PositionRepo:
    """Async CRUD for open positions and closed trades."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def save_open(self, position: Position, execution_mode: str) -> None:
        """Upsert an open position by mint. ``opened_at`` stored as wall-clock."""
        opened_wall = _monotonic_to_wall(position.opened_at)
        values = {
            "mint": position.mint,
            "symbol": position.symbol,
            "size_sol": Decimal(str(position.size_sol)),
            "tokens_atomic": position.tokens_atomic,
            "take_profit_pct": Decimal(str(position.take_profit_pct)),
            "stop_loss_pct": Decimal(str(position.stop_loss_pct)),
            "max_hold_s": Decimal(str(position.max_hold_s)),
            "confidence": Decimal(str(position.confidence)),
            "opened_at": opened_wall,
            "execution_mode": execution_mode,
            "status": "open",
        }
        stmt = pg_insert(PositionModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PositionModel.mint],
            set_={k: v for k, v in values.items() if k != "mint"},
        )
        async with self._sf() as session, session.begin():
            await session.execute(stmt)

    async def delete_open(self, mint: str) -> None:
        async with self._sf() as session, session.begin():
            await session.execute(delete(PositionModel).where(PositionModel.mint == mint))

    async def mark_needs_review(self, mint: str) -> None:
        async with self._sf() as session, session.begin():
            await session.execute(
                update(PositionModel)
                .where(PositionModel.mint == mint)
                .values(status="needs_review")
            )

    async def save_closed_trade(self, trade: ClosedTrade, execution_mode: str) -> None:
        pos = trade.position
        async with self._sf() as session, session.begin():
            await session.execute(
                insert(ClosedTradeModel).values(
                    mint=pos.mint,
                    symbol=pos.symbol,
                    size_sol=Decimal(str(pos.size_sol)),
                    tokens_atomic=pos.tokens_atomic,
                    exit_sol=Decimal(str(trade.exit_sol)),
                    pnl_sol=Decimal(str(trade.pnl_sol)),
                    reason=trade.reason,
                    confidence=Decimal(str(pos.confidence)),
                    opened_at=_monotonic_to_wall(pos.opened_at),
                    closed_at=_monotonic_to_wall(trade.closed_at),
                    execution_mode=execution_mode,
                )
            )

    async def load_recent_close_ages(self, within_s: float) -> list[tuple[str, float]]:
        """Return ``(mint, seconds_since_close)`` for trades closed in the last
        ``within_s`` seconds — used to rebuild re-entry cooldowns after a
        restart so churn can't resume by bouncing the container."""
        cutoff = datetime.now(UTC) - timedelta(seconds=within_s)
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(ClosedTradeModel.mint, ClosedTradeModel.closed_at).where(
                        ClosedTradeModel.closed_at >= cutoff
                    )
                )
            ).all()
        wall_now = datetime.now(UTC)
        return [(mint, (wall_now - closed_at).total_seconds()) for mint, closed_at in rows]

    async def load_open(self, *, now_fn=time.monotonic) -> list[Position]:
        """Load status='open' positions, rebuilding ``opened_at`` as a monotonic
        value consistent with the running clock (so ``max_hold_s`` still works)."""
        async with self._sf() as session:
            rows = (
                (await session.execute(select(PositionModel).where(PositionModel.status == "open")))
                .scalars()
                .all()
            )

        wall_now = datetime.now(UTC)
        mono_now = now_fn()
        positions: list[Position] = []
        for row in rows:
            elapsed = (wall_now - row.opened_at).total_seconds()
            positions.append(
                Position(
                    mint=row.mint,
                    symbol=row.symbol,
                    size_sol=float(row.size_sol),
                    tokens_atomic=int(row.tokens_atomic),
                    take_profit_pct=float(row.take_profit_pct),
                    stop_loss_pct=float(row.stop_loss_pct),
                    max_hold_s=float(row.max_hold_s),
                    confidence=float(row.confidence),
                    opened_at=mono_now - elapsed,
                )
            )
        return positions


def _monotonic_to_wall(mono_value: float) -> datetime:
    """Convert a ``time.monotonic()`` reading to a wall-clock UTC datetime.

    ``mono_value`` is seconds on the monotonic clock; ``time.monotonic() -
    mono_value`` is how long ago that was, subtracted from wall-clock now.
    """
    seconds_ago = time.monotonic() - mono_value
    return datetime.now(UTC) - timedelta(seconds=seconds_ago)

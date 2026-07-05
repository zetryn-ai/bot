"""RiskStateRepo — persist the daily realized PnL for the circuit breaker.

One row per calendar day. `RiskManager` loads today's value at startup and
saves on every close, so a mid-day restart doesn't reset the daily-loss
circuit breaker back to zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from zetryn_bot.db.models import RiskStateModel


class RiskStateRepo:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def load_day(self, day: date) -> float:
        """Return the persisted realized PnL for ``day`` (0.0 if no row yet)."""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(RiskStateModel.realized_pnl_sol).where(RiskStateModel.day == day)
                )
            ).scalar_one_or_none()
        return float(row) if row is not None else 0.0

    async def save_day(self, day: date, realized_pnl_sol: float) -> None:
        """Upsert ``day``'s realized PnL."""
        stmt = pg_insert(RiskStateModel).values(
            day=day,
            realized_pnl_sol=Decimal(str(realized_pnl_sol)),
            updated_at=datetime.now(UTC),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RiskStateModel.day],
            set_={
                "realized_pnl_sol": Decimal(str(realized_pnl_sol)),
                "updated_at": datetime.now(UTC),
            },
        )
        async with self._sf() as session, session.begin():
            await session.execute(stmt)

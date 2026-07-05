"""SQLAlchemy 2.0 ORM models — the M6 persistence schema.

Four tables:
- ``positions``       — currently-open positions (one row per mint).
- ``closed_trades``   — realized-PnL history (fully denormalized; no FK to
                        ``positions``, whose rows are deleted on close).
- ``risk_state``      — one row per day holding realized PnL, for the daily
                        circuit breaker (so a restart doesn't reset it).
- ``decision_log_kv`` — a generic namespaced KV table backing ``PostgresStore``
                        (which satisfies the framework's ``MemoryStore``
                        Protocol, used by ``DecisionLog`` / ``ReflectiveNode``).

Timestamps are ``timestamptz`` (wall-clock). The in-memory ``Position`` /
``ClosedTrade`` use ``time.monotonic()`` values that are only meaningful within
one process; the repositories bridge between the two (see ``position_repo``).

This module is the single source of truth for the schema — Alembic autogenerates
migrations from ``Base.metadata``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PositionModel(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    mint: Mapped[str] = mapped_column(String, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String, default="")
    size_sol: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    tokens_atomic: Mapped[int] = mapped_column(BigInteger)
    take_profit_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    stop_loss_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    max_hold_s: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    execution_mode: Mapped[str] = mapped_column(String(8))  # "paper" | "live"
    status: Mapped[str] = mapped_column(
        String(16), default="open", index=True
    )  # open | needs_review


class ClosedTradeModel(Base):
    __tablename__ = "closed_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    mint: Mapped[str] = mapped_column(String, index=True)
    symbol: Mapped[str] = mapped_column(String, default="")
    size_sol: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    tokens_atomic: Mapped[int] = mapped_column(BigInteger)
    exit_sol: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    pnl_sol: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    reason: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    execution_mode: Mapped[str] = mapped_column(String(8))


class RiskStateModel(Base):
    __tablename__ = "risk_state"

    day: Mapped[date] = mapped_column("date", Date, primary_key=True)  # one row per day
    realized_pnl_sol: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal(0))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionLogEntry(Base):
    __tablename__ = "decision_log_kv"

    ns: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    exp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

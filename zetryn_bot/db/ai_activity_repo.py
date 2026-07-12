"""``AiActivityRepo`` — persistence for the M9 live AI-activity table.

Writes happen on the bot's decision path (via ``AiActivitySink``), reads on
the dashboard API. Both are best-effort consumers: a failed write is logged
and dropped (never disturbs trading), a failed read surfaces as an API error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, insert, select, update

from zetryn_bot.db.models import AiDecisionModel


class AiActivityRepo:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def insert(
        self,
        *,
        mint: str,
        symbol: str,
        primary_source: str,
        route: str,
        action: str,
        confidence: float,
        final_score: float,
        scores: dict,
        reasoning: str,
        reasons: list,
        outcome: str = "",
        outcome_detail: str = "",
    ) -> int:
        async with self._sf() as session, session.begin():
            result = await session.execute(
                insert(AiDecisionModel)
                .values(
                    ts=datetime.now(UTC),
                    mint=mint,
                    symbol=symbol,
                    primary_source=primary_source,
                    route=route,
                    action=action,
                    confidence=Decimal(str(round(confidence, 4))),
                    final_score=Decimal(str(round(final_score, 4))),
                    scores=scores,
                    reasoning=reasoning,
                    reasons=reasons,
                    outcome=outcome,
                    outcome_detail=outcome_detail[:160],
                )
                .returning(AiDecisionModel.id)
            )
            return int(result.scalar_one())

    async def set_outcome(self, row_id: int, outcome: str, detail: str = "") -> None:
        async with self._sf() as session, session.begin():
            await session.execute(
                update(AiDecisionModel)
                .where(AiDecisionModel.id == row_id)
                .values(outcome=outcome, outcome_detail=detail[:160])
            )

    async def load_recent(self, limit: int = 100) -> list[AiDecisionModel]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AiDecisionModel).order_by(AiDecisionModel.ts.desc()).limit(limit)
                )
            ).scalars()
            return list(rows)

    async def prune(self, retention_days: float) -> int:
        """Delete rows older than the retention window; returns rows removed."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                delete(AiDecisionModel).where(AiDecisionModel.ts < cutoff)
            )
            return int(result.rowcount or 0)

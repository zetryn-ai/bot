"""PostgresStore — a Postgres backend for the framework's ``MemoryStore``.

Satisfies ``zetryn.memory.store.MemoryStore`` (get/put/delete/query) over the
``decision_log_kv`` table, so the framework's ``DecisionLog`` / ``ReflectiveNode``
can persist across restarts. TTL (``exp``) is honored exactly like the
framework's ``InMemoryStore`` / ``JSONFileStore``: expired entries read as absent.

The bot only provides storage here — the decision-tier logic (what to log, how
to reflect) stays entirely in the framework.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from zetryn_bot.db.models import DecisionLogEntry


class PostgresStore:
    """Namespaced key-value store on Postgres. Implements MemoryStore Protocol."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def get(self, ns: str, key: str) -> Any | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(DecisionLogEntry).where(
                        DecisionLogEntry.ns == ns, DecisionLogEntry.key == key
                    )
                )
            ).scalar_one_or_none()
        if row is None or _expired(row.exp):
            return None
        return row.value

    async def put(self, ns: str, key: str, value: Any, *, ttl: float | None = None) -> None:
        exp = datetime.now(UTC) + timedelta(seconds=ttl) if ttl is not None else None
        stmt = pg_insert(DecisionLogEntry).values(ns=ns, key=key, value=value, exp=exp)
        stmt = stmt.on_conflict_do_update(
            index_elements=[DecisionLogEntry.ns, DecisionLogEntry.key],
            set_={"value": value, "exp": exp},
        )
        async with self._sf() as session, session.begin():
            await session.execute(stmt)

    async def delete(self, ns: str, key: str) -> None:
        async with self._sf() as session, session.begin():
            await session.execute(
                delete(DecisionLogEntry).where(
                    DecisionLogEntry.ns == ns, DecisionLogEntry.key == key
                )
            )

    async def query(self, ns: str) -> list[Any]:
        async with self._sf() as session:
            rows = (
                (await session.execute(select(DecisionLogEntry).where(DecisionLogEntry.ns == ns)))
                .scalars()
                .all()
            )
        return [r.value for r in rows if not _expired(r.exp)]


def _expired(exp: datetime | None) -> bool:
    return exp is not None and exp <= datetime.now(UTC)

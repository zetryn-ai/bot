"""Async engine + session factory, and a connectivity probe.

`build_session_factory` creates the engine from a ``DATABASE_URL`` and returns
an ``async_sessionmaker``. `check_connection` is used at startup to decide
whether persistence is available — a failure there means the runtime falls back
to in-memory state (repos = None) rather than crashing.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

log = logger.bind(component="db.engine")


def build_engine(database_url: str) -> AsyncEngine:
    """Create an async engine. ``pool_pre_ping`` guards against stale connections."""
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_connection(engine: AsyncEngine) -> bool:
    """Return True if the database answers a trivial query; log + return False otherwise."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("database unreachable ({}) — persistence disabled, using in-memory state", exc)
        return False

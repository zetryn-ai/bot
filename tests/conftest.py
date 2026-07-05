"""Shared fixtures — the DB-backed ones gate on ``DATABASE_URL_TEST``.

Integration tests run against a real Postgres (no mock DB). They are skipped
unless ``DATABASE_URL_TEST`` is set, so they never truncate a developer's real
``zetryn_bot`` database by accident. CI sets it to a disposable service DB.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from zetryn_bot.db.engine import build_engine, build_session_factory
from zetryn_bot.db.models import Base

_TEST_DB_URL = os.environ.get("DATABASE_URL_TEST")
_M6_TABLES = "positions, closed_trades, risk_state, decision_log_kv"


@pytest_asyncio.fixture
async def session_factory():
    """A session factory against the test DB, schema created + tables truncated.

    Skips the test when ``DATABASE_URL_TEST`` is unset or the DB is unreachable.
    """
    if not _TEST_DB_URL:
        pytest.skip("DATABASE_URL_TEST not set — DB integration tests skipped")

    engine = build_engine(_TEST_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text(f"TRUNCATE {_M6_TABLES}"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"test DB unreachable: {exc}")

    yield build_session_factory(engine)
    await engine.dispose()

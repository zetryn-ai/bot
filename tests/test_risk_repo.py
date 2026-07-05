"""Integration tests for RiskStateRepo (real Postgres via DATABASE_URL_TEST)."""

from __future__ import annotations

from datetime import date

import pytest

from zetryn_bot.db.risk_repo import RiskStateRepo


@pytest.mark.asyncio
async def test_missing_day_returns_zero(session_factory):
    repo = RiskStateRepo(session_factory)
    assert await repo.load_day(date(2099, 1, 1)) == 0.0


@pytest.mark.asyncio
async def test_save_and_load(session_factory):
    repo = RiskStateRepo(session_factory)
    await repo.save_day(date(2099, 1, 1), -0.5)
    assert await repo.load_day(date(2099, 1, 1)) == pytest.approx(-0.5)


@pytest.mark.asyncio
async def test_upsert_same_day(session_factory):
    repo = RiskStateRepo(session_factory)
    await repo.save_day(date(2099, 1, 1), -0.5)
    await repo.save_day(date(2099, 1, 1), -0.9)  # accumulated further loss
    assert await repo.load_day(date(2099, 1, 1)) == pytest.approx(-0.9)


@pytest.mark.asyncio
async def test_days_are_independent(session_factory):
    repo = RiskStateRepo(session_factory)
    await repo.save_day(date(2099, 1, 1), -0.5)
    await repo.save_day(date(2099, 1, 2), 0.3)
    assert await repo.load_day(date(2099, 1, 1)) == pytest.approx(-0.5)
    assert await repo.load_day(date(2099, 1, 2)) == pytest.approx(0.3)

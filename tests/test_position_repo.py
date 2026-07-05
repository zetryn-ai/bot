"""Integration tests for PositionRepo (real Postgres via DATABASE_URL_TEST)."""

from __future__ import annotations

import time

import pytest

from zetryn_bot.db.position_repo import PositionRepo
from zetryn_bot.execution.executor import ClosedTrade, Position


def _pos(mint="MintA", opened_at=None, max_hold=1800.0) -> Position:
    return Position(
        mint=mint,
        symbol="AAA",
        size_sol=0.08,
        tokens_atomic=134_923_453_525,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=max_hold,
        confidence=0.8,
        opened_at=opened_at if opened_at is not None else time.monotonic(),
    )


@pytest.mark.asyncio
async def test_save_and_load_open(session_factory):
    repo = PositionRepo(session_factory)
    await repo.save_open(_pos("MintA"), "paper")
    loaded = await repo.load_open()
    assert [p.mint for p in loaded] == ["MintA"]
    assert loaded[0].tokens_atomic == 134_923_453_525


@pytest.mark.asyncio
async def test_load_preserves_age_across_restart(session_factory):
    # opened 100s ago; after reload, age should still be ~100s (monotonic bridge)
    repo = PositionRepo(session_factory)
    await repo.save_open(_pos("MintAge", opened_at=time.monotonic() - 100.0), "paper")
    loaded = await repo.load_open()
    age = time.monotonic() - loaded[0].opened_at
    assert 98.0 < age < 105.0


@pytest.mark.asyncio
async def test_upsert_by_mint_no_duplicate(session_factory):
    repo = PositionRepo(session_factory)
    await repo.save_open(_pos("MintA"), "paper")
    await repo.save_open(_pos("MintA"), "paper")  # same mint again
    assert len(await repo.load_open()) == 1


@pytest.mark.asyncio
async def test_delete_open(session_factory):
    repo = PositionRepo(session_factory)
    await repo.save_open(_pos("MintA"), "paper")
    await repo.delete_open("MintA")
    assert await repo.load_open() == []


@pytest.mark.asyncio
async def test_needs_review_excluded_from_load_open(session_factory):
    repo = PositionRepo(session_factory)
    await repo.save_open(_pos("MintA"), "live")
    await repo.mark_needs_review("MintA")
    assert await repo.load_open() == []  # load_open only returns status='open'


@pytest.mark.asyncio
async def test_save_closed_trade_removes_from_open_view(session_factory):
    repo = PositionRepo(session_factory)
    pos = _pos("MintClose")
    await repo.save_open(pos, "paper")
    await repo.delete_open("MintClose")
    await repo.save_closed_trade(
        ClosedTrade(position=pos, exit_sol=0.092, pnl_sol=0.012, reason="take_profit"), "paper"
    )
    assert await repo.load_open() == []

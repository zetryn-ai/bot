"""Integration tests for PositionTracker.load_and_reconcile (real Postgres)."""

from __future__ import annotations

import time

import pytest

from zetryn_bot.db.position_repo import PositionRepo
from zetryn_bot.execution.executor import PaperExecutor, Position
from zetryn_bot.execution.jupiter import Quote, sol_to_lamports
from zetryn_bot.execution.position import PositionTracker
from zetryn_bot.execution.risk import RiskConfig, RiskManager

# A syntactically-valid base58 Solana pubkey/mint for the RPC path.
_WALLET = "4MSkSdJp5NdsHrAkyE1mKkPLdGKpCX8YiQ4ifArB5FZj"
_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


class _FakeJupiter:
    async def quote(self, *a, **k):
        return Quote(in_amount=1, out_amount=sol_to_lamports(0.2), price_impact_pct=0.0)


class _FakeRpc:
    """Returns a fixed on-chain balance for get_token_balance_for_mint."""

    def __init__(self, onchain: int) -> None:
        self.onchain = onchain

    async def get_token_balance_for_mint(self, owner, mint) -> int:
        return self.onchain


def _tracker(session_factory, execution_mode):
    jup = _FakeJupiter()
    risk = RiskManager(RiskConfig())
    return PositionTracker(
        PaperExecutor(jup),
        jup,
        risk,
        repo=PositionRepo(session_factory),
        execution_mode=execution_mode,
    )


async def _seed(session_factory, tokens_atomic=1_000_000):
    pos = Position(
        mint=_MINT,
        symbol="BONK",
        size_sol=0.2,
        tokens_atomic=tokens_atomic,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
        opened_at=time.monotonic() - 10.0,
    )
    await PositionRepo(session_factory).save_open(pos, "live")


@pytest.mark.asyncio
async def test_paper_mode_loads_without_reconcile(session_factory):
    await _seed(session_factory)
    tracker = _tracker(session_factory, "paper")
    await tracker.load_and_reconcile(wallet_pubkey=None, rpc=None)
    assert tracker.open_count() == 1  # loaded, no on-chain check


@pytest.mark.asyncio
async def test_live_match_loads_position(session_factory):
    await _seed(session_factory, tokens_atomic=1_000_000)
    tracker = _tracker(session_factory, "live")
    await tracker.load_and_reconcile(_WALLET, _FakeRpc(onchain=1_000_000))  # matches
    assert tracker.open_count() == 1


@pytest.mark.asyncio
async def test_live_mismatch_flags_needs_review(session_factory):
    await _seed(session_factory, tokens_atomic=1_000_000)
    tracker = _tracker(session_factory, "live")
    await tracker.load_and_reconcile(_WALLET, _FakeRpc(onchain=500_000))  # mismatch
    assert tracker.open_count() == 0  # excluded from monitor loop
    # and it's persisted as needs_review (not returned by load_open anymore)
    assert await PositionRepo(session_factory).load_open() == []

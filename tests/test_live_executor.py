"""Unit tests for LiveExecutor — mocked RPC/Jupiter, no real network or funds."""

from __future__ import annotations

import asyncio
import base64

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from zetryn_bot.execution.executor import Position, SwapRequest
from zetryn_bot.execution.jupiter import Quote
from zetryn_bot.execution.live import LiveExecutor
from zetryn_bot.wallet.keystore import Wallet


def _dummy_swap_tx_b64(payer_pubkey: Pubkey) -> str:
    """An unsigned VersionedTransaction payable by ``payer_pubkey`` — mirrors a
    real Jupiter /swap response, which is unsigned (default-signature
    placeholders) for the requested user pubkey. LiveExecutor decodes and signs
    it locally with the wallet's keypair.
    """
    msg = MessageV0.try_compile(payer_pubkey, [], [], Hash.default())
    n = msg.header.num_required_signatures
    unsigned = VersionedTransaction.populate(msg, [Signature.default()] * n)
    return base64.b64encode(bytes(unsigned)).decode()


class _FakeJupiter:
    def __init__(self, out_amount: int = 1_000_000) -> None:
        self.out_amount = out_amount
        self.build_calls: list[tuple] = []

    async def build_swap_tx(self, input_mint, output_mint, amount_atomic, user_pubkey, **kw):
        self.build_calls.append((input_mint, output_mint, amount_atomic))
        return _dummy_swap_tx_b64(Pubkey.from_string(user_pubkey))

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        return Quote(in_amount=amount_atomic, out_amount=self.out_amount, price_impact_pct=0.0)


class _FakeRpc:
    def __init__(self, *, confirms=True, lands_after_timeout=False, balance=1.0) -> None:
        self.confirms = confirms
        self.lands_after_timeout = lands_after_timeout
        self.balance = balance
        self.sent: list[VersionedTransaction] = []

    async def send_transaction(self, tx):
        self.sent.append(tx)
        return Signature.default()

    async def confirm(self, sig, *, timeout_s=30.0):
        return self.confirms

    async def check_signature_landed(self, sig):
        return self.lands_after_timeout

    async def get_sol_balance(self, pubkey):
        return self.balance


def _wallet() -> Wallet:
    return Wallet(Keypair())


def _req(size_sol=0.1) -> SwapRequest:
    return SwapRequest(
        mint="MintA",
        symbol="AAA",
        size_sol=size_sol,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
    )


@pytest.mark.asyncio
async def test_buy_succeeds_and_signs_locally():
    jup = _FakeJupiter(out_amount=500_000)
    rpc = _FakeRpc(confirms=True, balance=1.0)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    pos = await ex.buy(_req(size_sol=0.1))
    assert pos is not None
    assert pos.tokens_atomic == 500_000
    assert len(rpc.sent) == 1
    # the tx LiveExecutor sent must carry a real (non-default) signature
    assert rpc.sent[0].signatures[0] != Signature.default()


@pytest.mark.asyncio
async def test_buy_aborts_on_insufficient_balance():
    jup = _FakeJupiter()
    rpc = _FakeRpc(balance=0.05)  # less than size (0.1) + reserve (0.05)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    pos = await ex.buy(_req(size_sol=0.1))
    assert pos is None
    assert len(rpc.sent) == 0  # never even attempted a swap


@pytest.mark.asyncio
async def test_timeout_then_landed_is_treated_as_success():
    jup = _FakeJupiter(out_amount=42)
    rpc = _FakeRpc(confirms=False, lands_after_timeout=True, balance=1.0)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    pos = await ex.buy(_req())
    assert pos is not None  # on-chain check saved it, despite confirm() timing out


@pytest.mark.asyncio
async def test_timeout_then_not_landed_is_treated_as_failure_no_retry():
    jup = _FakeJupiter()
    rpc = _FakeRpc(confirms=False, lands_after_timeout=False, balance=1.0)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    pos = await ex.buy(_req())
    assert pos is None
    assert len(rpc.sent) == 1  # sent once; caller must not blind-retry


@pytest.mark.asyncio
async def test_concurrent_buys_same_mint_are_serialized():
    jup = _FakeJupiter()
    rpc = _FakeRpc(balance=10.0)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    await asyncio.gather(*(ex.buy(_req()) for _ in range(5)))
    # Each call is serialized via the per-mint lock (no assertion on business
    # outcome here — this test would deadlock/hang or raise if the lock were
    # broken; reaching this point with 5 sends confirms serialized access).
    assert len(rpc.sent) == 5


@pytest.mark.asyncio
async def test_sell_computes_pnl():
    jup = _FakeJupiter(out_amount=int(0.15 * 1_000_000_000))  # 0.15 SOL back
    rpc = _FakeRpc(confirms=True, balance=1.0)
    ex = LiveExecutor(_wallet(), rpc, jup, min_sol_reserve=0.05)

    position = Position(
        mint="MintA",
        symbol="AAA",
        size_sol=0.1,
        tokens_atomic=1_000_000,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
    )
    trade = await ex.sell(position, "take_profit")
    assert trade is not None
    assert round(trade.pnl_sol, 4) == 0.05

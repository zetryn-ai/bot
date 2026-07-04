"""Unit tests for BalanceCache — TTL refresh, no inline query when fresh."""

from __future__ import annotations

import pytest
from solders.keypair import Keypair

from zetryn_bot.execution.live import BalanceCache


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _FakeRpc:
    def __init__(self, balances: list[float]) -> None:
        self._balances = list(balances)
        self.calls = 0

    async def get_sol_balance(self, pubkey) -> float:
        self.calls += 1
        return self._balances.pop(0)


@pytest.mark.asyncio
async def test_first_get_fetches():
    rpc = _FakeRpc([1.0])
    cache = BalanceCache(rpc, Keypair().pubkey(), ttl_s=10.0, now_fn=_Clock())
    assert await cache.get() == 1.0
    assert rpc.calls == 1


@pytest.mark.asyncio
async def test_within_ttl_does_not_refetch():
    clock = _Clock()
    rpc = _FakeRpc([1.0, 2.0])
    cache = BalanceCache(rpc, Keypair().pubkey(), ttl_s=10.0, now_fn=clock)
    await cache.get()
    clock.t = 5.0  # within TTL
    assert await cache.get() == 1.0
    assert rpc.calls == 1


@pytest.mark.asyncio
async def test_past_ttl_refetches():
    clock = _Clock()
    rpc = _FakeRpc([1.0, 2.0])
    cache = BalanceCache(rpc, Keypair().pubkey(), ttl_s=10.0, now_fn=clock)
    await cache.get()
    clock.t = 11.0  # past TTL
    assert await cache.get() == 2.0
    assert rpc.calls == 2


@pytest.mark.asyncio
async def test_invalidate_forces_refetch():
    rpc = _FakeRpc([1.0, 2.0])
    cache = BalanceCache(rpc, Keypair().pubkey(), ttl_s=100.0, now_fn=_Clock())
    await cache.get()
    cache.invalidate()
    assert await cache.get() == 2.0
    assert rpc.calls == 2

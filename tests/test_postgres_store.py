"""Integration tests for PostgresStore — the MemoryStore Protocol contract."""

from __future__ import annotations

import pytest
from zetryn.memory.store import MemoryStore

from zetryn_bot.db.memory_store import PostgresStore


@pytest.mark.asyncio
async def test_satisfies_memorystore_protocol(session_factory):
    assert isinstance(PostgresStore(session_factory), MemoryStore)


@pytest.mark.asyncio
async def test_put_get_roundtrip(session_factory):
    store = PostgresStore(session_factory)
    await store.put("ns", "k1", {"run_id": "k1", "action": "alert"})
    assert await store.get("ns", "k1") == {"run_id": "k1", "action": "alert"}


@pytest.mark.asyncio
async def test_get_missing_is_none(session_factory):
    assert await PostgresStore(session_factory).get("ns", "nope") is None


@pytest.mark.asyncio
async def test_put_overwrites(session_factory):
    store = PostgresStore(session_factory)
    await store.put("ns", "k1", {"v": 1})
    await store.put("ns", "k1", {"v": 2})
    assert await store.get("ns", "k1") == {"v": 2}


@pytest.mark.asyncio
async def test_delete(session_factory):
    store = PostgresStore(session_factory)
    await store.put("ns", "k1", {"v": 1})
    await store.delete("ns", "k1")
    assert await store.get("ns", "k1") is None


@pytest.mark.asyncio
async def test_query_returns_namespace_values(session_factory):
    store = PostgresStore(session_factory)
    await store.put("ns_a", "k1", {"v": 1})
    await store.put("ns_a", "k2", {"v": 2})
    await store.put("ns_b", "k3", {"v": 3})
    vals = await store.query("ns_a")
    assert {v["v"] for v in vals} == {1, 2}


@pytest.mark.asyncio
async def test_expired_entry_reads_as_absent(session_factory):
    store = PostgresStore(session_factory)
    await store.put("ns", "k1", {"v": 1}, ttl=-1)  # already expired
    assert await store.get("ns", "k1") is None
    assert await store.query("ns") == []

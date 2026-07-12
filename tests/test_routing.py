"""Unit tests for M10b entry routing — rules, LaunchMemory, graduation event."""

from __future__ import annotations

import pytest
from trading.schemas import Decision

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.routing.graduation import build_graduation_event
from zetryn_bot.routing.launch_memory import LaunchMemory
from zetryn_bot.routing.router import Route, RoutedPipeline, primary_source


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _StubPipeline:
    """Records what it processed; returns a decision tagged with its name."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[str] = []

    async def process(self, candidate, session):
        self.seen.append(candidate.address)
        d = Decision(action="skip", confidence=0.0)
        d.meta["route"] = self.name
        return d


def _cand(address: str, source: str, age: int = 0) -> TokenCandidate:
    return TokenCandidate(address=address, symbol="T", sources=[source], age_seconds=age)


def _router(max_age: float = 120.0, memory: LaunchMemory | None = None):
    sniper, grad, scanner = (
        _StubPipeline("sniper"),
        _StubPipeline("graduation"),
        _StubPipeline("scanner"),
    )
    router = RoutedPipeline(
        routes=[
            Route(
                "sniper",
                lambda c: primary_source(c) == "pumpfun_ws" and c.age_seconds <= max_age,
                sniper,
            ),
            Route("graduation", lambda c: primary_source(c) == "pumpfun_migration", grad),
        ],
        fallback=Route("scanner", lambda c: True, scanner),
        launch_memory=memory,
    )
    return router, sniper, grad, scanner


# ── routing rules ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fresh_pumpfun_launch_routes_to_sniper():
    router, sniper, _, scanner = _router()
    d = await router.process(_cand("MintA", "pumpfun_ws", age=30), session=None)
    assert sniper.seen == ["MintA"] and scanner.seen == []
    assert d.meta["route"] == "sniper"


@pytest.mark.asyncio
async def test_stale_pumpfun_launch_falls_to_scanner():
    router, sniper, _, scanner = _router(max_age=120)
    await router.process(_cand("MintA", "pumpfun_ws", age=121), session=None)
    assert sniper.seen == [] and scanner.seen == ["MintA"]


@pytest.mark.asyncio
async def test_migration_routes_to_graduation():
    router, _, grad, _ = _router()
    await router.process(_cand("MintB", "pumpfun_migration"), session=None)
    assert grad.seen == ["MintB"]


@pytest.mark.asyncio
async def test_other_sources_fall_back_to_scanner():
    router, sniper, grad, scanner = _router()
    for src in ("dexscreener", "geckoterminal_trending", "birdeye_trending", "telegram_alpha"):
        await router.process(_cand(f"M_{src}", src), session=None)
    assert sniper.seen == [] and grad.seen == []
    assert len(scanner.seen) == 4


# ── launch memory ────────────────────────────────────────────────────────────


def test_launch_memory_fill_seconds_and_ttl():
    clock = _Clock()
    mem = LaunchMemory(ttl_s=3600, now_fn=clock)
    mem.record("MintA")
    clock.t += 240
    assert mem.fill_seconds("MintA") == pytest.approx(240)
    assert mem.fill_seconds("Unknown") == 0.0
    clock.t += 3601
    assert mem.fill_seconds("MintA") == 0.0  # expired


def test_launch_memory_first_sighting_wins():
    clock = _Clock()
    mem = LaunchMemory(now_fn=clock)
    mem.record("MintA")
    clock.t += 100
    mem.record("MintA")  # duplicate launch event must not reset the clock
    clock.t += 100
    assert mem.fill_seconds("MintA") == pytest.approx(200)


@pytest.mark.asyncio
async def test_router_records_launches_for_later_migration():
    clock = _Clock()
    mem = LaunchMemory(now_fn=clock)
    router, *_ = _router(memory=mem)
    await router.process(_cand("MintA", "pumpfun_ws", age=10), session=None)
    clock.t += 300
    assert mem.fill_seconds("MintA") == pytest.approx(300)


# ── graduation event mapping ─────────────────────────────────────────────────


def test_graduation_event_uses_launch_memory():
    clock = _Clock()
    mem = LaunchMemory(now_fn=clock)
    mem.record("MintG")
    clock.t += 420
    cand = TokenCandidate(
        address="MintG",
        symbol="G",
        sources=["pumpfun_migration"],
        age_seconds=15,
        bonding_curve_sol=84.5,
    )
    event = build_graduation_event(cand, mem)
    assert event.mint == "MintG"
    assert event.bonding_curve_fill_seconds == pytest.approx(420)
    assert event.bonding_curve_sol_raised == pytest.approx(84.5)
    # pair age is 0 at detection: the migration event fires when the DEX pool
    # is created; candidate.age_seconds is TOKEN age and must NOT leak in.
    assert event.pair_age_seconds == 0.0


def test_graduation_event_unknown_launch_is_zero():
    event = build_graduation_event(
        TokenCandidate(address="MintX", sources=["pumpfun_migration"]), LaunchMemory()
    )
    assert event.bonding_curve_fill_seconds == 0.0

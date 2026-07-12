#!/usr/bin/env python3
"""M10b smoke test — entry routing with real framework agents, offline.

Run from the repo root::

    python scripts/m10b_smoke.py

Builds the REAL sniper (rule mode) and graduation agents plus a rule-only
scanner, wires them into a ``RoutedPipeline`` exactly as ``__main__`` does,
and pushes three synthetic candidates through: a fresh pump.fun launch, its
migration 5 minutes later, and a trending token. Asserts each lands on the
right route, the launch memory dates the migration, and the shared sink saw
every decision. No network, no keys, no funds.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.agents.graduation import build_graduation
from strategies.agents.scanner import build_scanner
from strategies.agents.sniper import build_sniper
from trading.schemas import GraduationConfig, ScannerConfig, SniperConfig

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink
from zetryn_bot.routing.graduation import GraduationPipeline
from zetryn_bot.routing.launch_memory import LaunchMemory
from zetryn_bot.routing.router import Route, RoutedPipeline, primary_source


def _candidate(address: str, source: str, **kw) -> TokenCandidate:
    return TokenCandidate(address=address, symbol=address[:4].upper(), sources=[source], **kw)


async def check() -> int:
    failures: list[str] = []
    sink = ListSink()
    memory = LaunchMemory()

    sniper_pipe = BotPipeline(
        build_sniper(llm_client=None), sink=sink, config=SniperConfig(), route_label="sniper"
    )
    graduation_pipe = GraduationPipeline(
        build_graduation(llm_client=None),
        sink=sink,
        config=GraduationConfig(
            min_unique_buyers=0, require_lp_burned=False, min_initial_liquidity_sol=0.0
        ),
        launch_memory=memory,
    )
    scanner_pipe = BotPipeline(
        build_scanner(llm_client=None), sink=sink, config=ScannerConfig(), route_label="scanner"
    )
    router = RoutedPipeline(
        routes=[
            Route(
                "sniper",
                lambda c: primary_source(c) == "pumpfun_ws" and c.age_seconds <= 120,
                sniper_pipe,
            ),
            Route(
                "graduation", lambda c: primary_source(c) == "pumpfun_migration", graduation_pipe
            ),
        ],
        fallback=Route("scanner", lambda c: True, scanner_pipe),
        launch_memory=memory,
    )

    launch = _candidate("LaunchMint111", "pumpfun_ws", age_seconds=20, bonding_curve_sol=4.0)
    migration = _candidate(
        "LaunchMint111", "pumpfun_migration", age_seconds=8, bonding_curve_sol=85.0
    )
    trending = _candidate("TrendMint2222", "geckoterminal_trending", age_seconds=7200)

    t0 = time.perf_counter()
    d_launch = await router.process(launch, session=None)
    sniper_ms = (time.perf_counter() - t0) * 1000
    d_migration = await router.process(migration, session=None)
    d_trend = await router.process(trending, session=None)

    routes = [d.meta.get("route") for d in (d_launch, d_migration, d_trend)]
    print(f"routes: launch={routes[0]} migration={routes[1]} trending={routes[2]}")
    print(f"sniper decision: action={d_launch.action} ({sniper_ms:.1f} ms, no LLM)")
    print(f"graduation fill_seconds recorded: {memory.fill_seconds('LaunchMint111'):.1f}s pending")
    if routes != ["sniper", "graduation", "scanner"]:
        failures.append(f"wrong routes: {routes}")
    if len(sink.decisions) != 3:
        failures.append(f"shared sink saw {len(sink.decisions)} decisions, expected 3")
    if sniper_ms > 1000:
        failures.append(f"sniper (rule mode) took {sniper_ms:.0f} ms — LLM in the hot loop?")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M10b smoke test passed (routing + shared sink).")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

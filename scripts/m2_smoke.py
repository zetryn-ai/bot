#!/usr/bin/env python3
"""M2 smoke test — exercise the adapter + pipeline against a real build_scanner().

Run from the repo root::

    python scripts/m2_smoke.py

Fully offline: uses ``build_scanner(llm_client=None)`` (rule-only gates, no
LLM call) and a synthetic TokenCandidate — no API keys, no network. This is
the same schema-contract check the pytest integration test runs; kept as a
standalone script per the M2 design doc so it's runnable without pytest
installed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from strategies.agents.scanner import build_scanner

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink


async def check() -> int:
    agent = build_scanner(llm_client=None)
    sink = ListSink()
    pipeline = BotPipeline(agent, sink=sink)

    healthy = TokenCandidate(
        address="Mint_healthy",
        symbol="GOOD",
        sources=["dexscreener.new_pairs"],
        liquidity_usd=50_000,
        volume_1h_usd=40_000,
        holder_count=200,
        top10_holder_pct=15.0,
    )
    dangerous = TokenCandidate(
        address="Mint_rug",
        symbol="RUG",
        is_honeypot=True,
        liquidity_usd=50_000,
        volume_1h_usd=40_000,
    )

    failures: list[str] = []
    async with aiohttp.ClientSession() as session:
        healthy_decision = await pipeline.process(healthy, session)
        print(
            f"healthy candidate -> action={healthy_decision.action!r} flags={healthy_decision.flags}"
        )
        if "hard_gate" in healthy_decision.flags:
            failures.append("healthy candidate was rejected by a hard gate")

        dangerous_decision = await pipeline.process(dangerous, session)
        print(
            f"dangerous candidate -> action={dangerous_decision.action!r} flags={dangerous_decision.flags}"
        )
        if dangerous_decision.action != "skip" or not dangerous_decision.flags.get("hard_gate"):
            failures.append("dangerous candidate was NOT rejected by the safety gate")

    if len(sink.decisions) != 2:
        failures.append(f"expected 2 emitted decisions, got {len(sink.decisions)}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M2 smoke test passed.")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

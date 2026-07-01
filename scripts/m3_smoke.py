#!/usr/bin/env python3
"""M3 smoke test — drive the Orchestrator end-to-end, offline.

Run from the repo root::

    python scripts/m3_smoke.py

Fully offline: a single in-process fake scanner feeds synthetic candidates
through a real ``build_scanner(llm_client=None)`` pipeline into a ListSink.
No network, no API keys, no LLM. Verifies the orchestrator wiring (queue +
worker pool + dedup + graceful shutdown) holds together.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.agents.scanner import build_scanner

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ListSink
from zetryn_bot.runtime.orchestrator import Orchestrator


class _FakeScanner:
    name = "smoke.fake"

    async def stream(self, session) -> AsyncIterator[TokenCandidate]:
        for i in range(5):
            yield TokenCandidate(
                address=f"Mint{i}",
                symbol=f"TOK{i}",
                sources=["dexscreener.new_pairs"],
                liquidity_usd=50_000,
                volume_1h_usd=40_000,
                holder_count=200,
                top10_holder_pct=15.0,
            )
        await asyncio.sleep(3600)  # stay alive so supervise doesn't restart


async def check() -> int:
    sink = ListSink()
    pipeline = BotPipeline(build_scanner(llm_client=None), sink=sink)
    orch = Orchestrator(pipeline, [_FakeScanner()], workers=2)

    await orch.start()
    try:
        # Wait until all 5 candidates have been processed (or time out).
        async def _poll():
            while len(sink.decisions) < 5:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=5.0)
    finally:
        await orch.shutdown()

    failures: list[str] = []
    if len(sink.decisions) != 5:
        failures.append(f"expected 5 decisions, got {len(sink.decisions)}")
    for candidate, decision in sink.decisions:
        print(f"{candidate.address} -> action={decision.action!r}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M3 smoke test passed.")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

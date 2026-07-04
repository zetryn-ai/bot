#!/usr/bin/env python3
"""M4 smoke test — paper execution loop end to end, offline.

Run from the repo root::

    python scripts/m4_smoke.py

Fully offline (Jupiter mocked): an ``alert`` decision → RiskManager →
PaperExecutor → PositionTracker → forced take-profit exit → assert a closed
trade with positive PnL and correct stats. No network, no keypair, no tx.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.schemas import Decision

from zetryn_bot.execution.executor import PaperExecutor
from zetryn_bot.execution.jupiter import SOL_MINT, Quote, sol_to_lamports
from zetryn_bot.execution.position import PositionTracker
from zetryn_bot.execution.risk import RiskConfig, RiskManager
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.sinks import ExecutionSink


class _MockJupiter:
    """Buy → 1e6 tokens; sell/value → a configurable SOL amount (drives exit)."""

    def __init__(self) -> None:
        self.sell_sol = 0.20  # flat initially

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        if output_mint == SOL_MINT:  # valuation / sell leg
            return Quote(
                in_amount=amount_atomic,
                out_amount=sol_to_lamports(self.sell_sol),
                price_impact_pct=0.0,
            )
        return Quote(in_amount=amount_atomic, out_amount=1_000_000, price_impact_pct=0.01)


async def check() -> int:
    jup = _MockJupiter()
    risk = RiskManager(RiskConfig(base_size_sol=0.2, min_confidence=0.6, take_profit_pct=0.3))
    executor = PaperExecutor(jup)
    tracker = PositionTracker(executor, jup, risk, poll_interval_s=0.01)
    sink = ExecutionSink(risk, executor, tracker)

    failures: list[str] = []

    # 1) An alert opens a paper position.
    cand = TokenCandidate(address="MintSmoke", symbol="SMOKE", sources=["dexscreener.new_pairs"])
    await sink.emit(cand, Decision(action="alert", confidence=0.8))
    if tracker.open_count() != 1:
        failures.append(f"expected 1 open position, got {tracker.open_count()}")

    # 2) Price jumps +50% → take-profit closes it on the next sweep.
    jup.sell_sol = 0.30
    await tracker.check_once()
    if tracker.open_count() != 0:
        failures.append(f"expected position closed, still {tracker.open_count()} open")

    stats = tracker.stats()
    print(
        f"open={stats['open']} closed={stats['closed']} "
        f"win_rate={stats['win_rate']:.0%} pnl={stats['total_pnl_sol']:+.4f} SOL"
    )
    if stats["closed"] != 1:
        failures.append(f"expected 1 closed trade, got {stats['closed']}")
    if stats["total_pnl_sol"] <= 0:
        failures.append(f"expected positive PnL, got {stats['total_pnl_sol']}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M4 smoke test passed.")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

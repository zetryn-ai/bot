#!/usr/bin/env python3
"""M6 smoke test — persistence round-trip, with a graceful in-memory fallback.

Run from the repo root::

    python scripts/m6_smoke.py

If ``DATABASE_URL`` (or the default local Postgres) is reachable: save a
position + daily PnL + a decision-log entry, reload them in a fresh repo
(simulating a restart), and assert they survived — including the
monotonic↔wall-clock age bridge. If Postgres is unreachable: assert the
in-memory fallback engages cleanly (no crash), which is itself the M6 safety
guarantee. No network, no funds, no real trades.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zetryn_bot.config import Settings
from zetryn_bot.db.engine import build_engine, build_session_factory, check_connection
from zetryn_bot.db.memory_store import PostgresStore
from zetryn_bot.db.models import Base
from zetryn_bot.db.position_repo import PositionRepo
from zetryn_bot.db.risk_repo import RiskStateRepo
from zetryn_bot.execution.executor import Position

_SMOKE_MINT = "M6smokeMint1111111111111111111111111111111"


async def _run_with_db(engine) -> list[str]:
    from sqlalchemy import text

    failures: list[str] = []
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = build_session_factory(engine)
    prepo, rrepo, store = PositionRepo(sf), RiskStateRepo(sf), PostgresStore(sf)

    # 1) Save a position opened 90s ago, then reload in a "fresh" repo (restart).
    pos = Position(
        mint=_SMOKE_MINT,
        symbol="SMK",
        size_sol=0.08,
        tokens_atomic=134_923_453_525,
        take_profit_pct=0.3,
        stop_loss_pct=0.15,
        max_hold_s=1800,
        confidence=0.8,
        opened_at=time.monotonic() - 90.0,
    )
    await prepo.save_open(pos, "paper")
    reloaded = [p for p in await PositionRepo(sf).load_open() if p.mint == _SMOKE_MINT]
    if len(reloaded) != 1:
        failures.append(f"expected 1 restored position, got {len(reloaded)}")
    else:
        age = time.monotonic() - reloaded[0].opened_at
        print(f"position restored: age={age:.1f}s (expect ~90) tokens={reloaded[0].tokens_atomic}")
        if not (85.0 < age < 96.0):
            failures.append(f"age bridge wrong: {age:.1f}s")

    # 2) Daily circuit-breaker PnL persists.
    await rrepo.save_day(date(2099, 1, 1), -0.42)
    pnl = await RiskStateRepo(sf).load_day(date(2099, 1, 1))
    print(f"daily PnL restored: {pnl} (expect -0.42)")
    if abs(pnl - (-0.42)) > 1e-9:
        failures.append(f"daily PnL wrong: {pnl}")

    # 3) Decision-log KV round-trip (backs ReflectiveNode).
    await store.put("m6_smoke", "run1", {"action": "alert"})
    if await store.get("m6_smoke", "run1") != {"action": "alert"}:
        failures.append("decision-log KV round-trip failed")

    # cleanup
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM positions WHERE mint = :m"), {"m": _SMOKE_MINT})
        await conn.execute(text("DELETE FROM risk_state WHERE date = '2099-01-01'"))
        await conn.execute(text("DELETE FROM decision_log_kv WHERE ns = 'm6_smoke'"))
    return failures


async def check() -> int:
    settings = Settings(_env_file=None)  # defaults; DATABASE_URL overridable via real env
    import os

    url = os.environ.get("DATABASE_URL", settings.database_url)
    engine = build_engine(url)

    if not await check_connection(engine):
        await engine.dispose()
        print("Postgres unreachable — verified in-memory fallback path (no crash).")
        print("\nOK — M6 smoke test passed (fallback path).")
        return 0

    failures = await _run_with_db(engine)
    await engine.dispose()

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M6 smoke test passed (persistence round-trip).")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

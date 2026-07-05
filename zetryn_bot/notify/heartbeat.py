"""Periodic liveness summary — confirms the process is alive, not silent.

Registered as an ``Orchestrator`` background task, same shape as
``PositionTracker.monitor_loop`` — crash-supervised, restarted if it dies.
"""

from __future__ import annotations

import asyncio
import time


async def heartbeat_loop(notifier, tracker, interval_s: float) -> None:
    """Sleep ``interval_s``, then push an uptime + position/PnL summary. Loops forever.

    ``tracker`` is ``None`` when execution is disabled — the heartbeat still
    confirms liveness, just without trade stats.
    """
    started_at = time.monotonic()
    while True:
        await asyncio.sleep(interval_s)
        uptime_h = (time.monotonic() - started_at) / 3600.0
        if tracker is not None:
            stats = tracker.stats()
            text = (
                f"\U0001f49a heartbeat — uptime={uptime_h:.1f}h "
                f"open={stats['open']} closed={stats['closed']} "
                f"win_rate={stats['win_rate']:.0%} pnl={stats['total_pnl_sol']:+.4f} SOL"
            )
        else:
            text = f"\U0001f49a heartbeat — uptime={uptime_h:.1f}h (execution disabled)"
        await notifier.notify(text)

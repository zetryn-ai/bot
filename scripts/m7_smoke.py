#!/usr/bin/env python3
"""M7 smoke test — notifier dedup/no-op safety, with an optional live Telegram send.

Run from the repo root::

    python scripts/m7_smoke.py

Always exercises the ``NullNotifier`` no-op path and ``TelegramNotifier``
dedup logic offline. If ``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID`` are set
in the environment, also sends one real Telegram message — network-safe,
skips cleanly without credentials (mirrors ``gmgn_check.py``).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zetryn_bot.notify.telegram import NullNotifier, TelegramNotifier


async def check() -> int:
    failures: list[str] = []

    # 1) NullNotifier is a true no-op.
    null = NullNotifier()
    await null.notify("should be dropped")
    print("NullNotifier: no-op confirmed")

    # 2) Dedup logic — same key within the window collapses, distinct keys don't.
    clock = {"t": 0.0}
    notifier = TelegramNotifier(
        "fake-token", "fake-chat", dedup_window_s=10.0, now_fn=lambda: clock["t"]
    )
    if not notifier._should_send("evt"):
        failures.append("first send of a fresh key should be allowed")
    if notifier._should_send("evt"):
        failures.append("repeat within window should be deduped")
    clock["t"] += 11.0
    if not notifier._should_send("evt"):
        failures.append("send after window elapsed should be allowed")
    print("TelegramNotifier dedup: OK")

    # 3) Optional live send.
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        live = TelegramNotifier(token, chat_id)
        await live.notify(f"\U0001f9ea zetryn-bot M7 smoke test — {int(time.time())}")
        print("Live Telegram send attempted (check your chat).")
    else:
        print("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping live send.")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M7 smoke test passed.")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

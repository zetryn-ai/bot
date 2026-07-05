"""Push notifications — Telegram alerts for trades, errors, and heartbeats.

Bot-owned I/O infrastructure (M7), same tier as the M6 persistence layer.
Off by default (``NOTIFY_ENABLED=false``); a misconfigured or unreachable
Telegram never crashes the pipeline (see ``NullNotifier`` / ``TelegramNotifier``).
"""

from __future__ import annotations

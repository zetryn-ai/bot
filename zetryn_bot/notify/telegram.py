"""``TelegramNotifier`` — pushes messages via the Telegram Bot API, plus ``NullNotifier``.

Uses a bot token (from @BotFather) + chat ID — distinct from the telethon user
session (``TELEGRAM_API_ID``/``TELEGRAM_API_HASH``) the Telegram *scanner*
uses to read channels. Sending a message never raises: a network/API failure
logs a warning once and drops that message, matching the M5/M6 fallback-safe
pattern (a broken notifier must never take down the trading pipeline).
"""

from __future__ import annotations

import time

import aiohttp
from loguru import logger

log = logger.bind(component="notify.telegram")

_API_BASE = "https://api.telegram.org"
_SEND_TIMEOUT_S = 10.0


class NullNotifier:
    """No-op — used when ``NOTIFY_ENABLED=false`` or credentials are missing."""

    async def notify(self, text: str, *, dedup_key: str | None = None) -> None:
        return None


class TelegramNotifier:
    """Sends ``text`` to one chat, deduplicating repeats of the same ``dedup_key``.

    ``dedup_key=None`` always sends (used for one-off, high-value events like
    trades). A set ``dedup_key`` is dropped if the same key was sent within
    ``dedup_window_s`` — collapses a storm of identical rate-limit/error
    warnings into a single message.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        dedup_window_s: float = 900.0,
        now_fn=time.monotonic,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._dedup_window_s = dedup_window_s
        self._now = now_fn
        self._last_sent: dict[str, float] = {}

    def _should_send(self, dedup_key: str | None) -> bool:
        if dedup_key is None:
            return True
        last = self._last_sent.get(dedup_key)
        now = self._now()
        if last is not None and (now - last) < self._dedup_window_s:
            return False
        self._last_sent[dedup_key] = now
        return True

    async def notify(self, text: str, *, dedup_key: str | None = None) -> None:
        if not self._should_send(dedup_key):
            return
        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text}
        try:
            timeout = aiohttp.ClientTimeout(total=_SEND_TIMEOUT_S)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=payload) as resp,
            ):
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("Telegram send failed status={} body={}", resp.status, body[:200])
        except Exception as exc:
            log.warning("Telegram send failed ({}) — message dropped", exc)

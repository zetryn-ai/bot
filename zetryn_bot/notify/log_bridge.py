"""Forward selected log records to a ``Notifier`` — no call-site rewiring needed.

Rate-limit and key/model-rotation warnings already exist as ``log.warning(...)``
calls scattered across scanner modules and the framework's key pool. Rather
than threading a ``Notifier`` into every one of those call sites, this module
installs loguru sinks that watch the log stream itself: ERROR+ always
forwards (critical failures), WARNING forwards only when it matches a
rate-limit/rotation keyword (routine warnings like GeckoTerminal's known
30s back-off stay log-only... unless they *are* the rate-limit event, which
is exactly what we want surfaced).

Dedup happens inside the ``Notifier`` (via ``dedup_key``), not here — this
module only decides *whether* a record is notify-worthy and builds the key.
"""

from __future__ import annotations

from loguru import logger

from zetryn_bot.notify.protocol import Notifier

_RATE_LIMIT_KEYWORDS = ("rate limit", "rate-limited", "flood", "back off", "backing off")
_ROTATION_KEYWORDS = ("rotat", "key pool", "keypool")


def _is_rate_limit_or_rotation(record) -> bool:
    msg = record["message"].lower()
    return any(k in msg for k in _RATE_LIMIT_KEYWORDS) or any(k in msg for k in _ROTATION_KEYWORDS)


def install_log_bridge(notifier: Notifier) -> None:
    """Register the ERROR+ and filtered-WARNING sinks against ``notifier``.

    Idempotent-ish in intent but not enforced — call once at startup. Async
    loguru sinks require a running event loop, true for the runtime's entire
    lifetime (installed after ``asyncio.run`` has started).
    """

    async def _forward(message) -> None:
        record = message.record
        component = record["extra"].get("component", "?")
        text = f"[{record['level'].name}] {component}: {record['message']}"
        dedup_key = f"{record['level'].name}:{record['message'][:80]}"
        await notifier.notify(text, dedup_key=dedup_key)

    # Critical errors — DB/wallet/RPC failures, scanner crash-loops, etc.
    logger.add(_forward, level="ERROR")

    # Rate-limit / key-rotation warnings only — not every WARNING is notify-worthy.
    logger.add(
        _forward,
        level="WARNING",
        filter=lambda r: r["level"].name == "WARNING" and _is_rate_limit_or_rotation(r),
    )

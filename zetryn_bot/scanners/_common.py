"""Shared helpers used across scanner implementations.

Kept intentionally small. The aim is to remove boilerplate, not to
abstract over things scanners genuinely do differently.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate

T = TypeVar("T")


async def poll_loop(
    name: str,
    interval_s: float,
    fetch_fn: Callable[[], Awaitable[list[TokenCandidate]]],
) -> AsyncIterator[TokenCandidate]:
    """Standard polling loop for :class:`Scanner` implementations.

    Calls ``fetch_fn`` every ``interval_s`` seconds, yields each candidate
    the fetch returned, and absorbs transient errors with a logged
    warning before continuing.

    Args:
        name: Scanner name, used as the log component.
        interval_s: Sleep between polls. Must be > 0.
        fetch_fn: Async callable returning a fresh batch of
            :class:`TokenCandidate` per poll. Should not raise on
            transient HTTP errors — handle those inside and return an
            empty list. Raises will be caught here for survival but
            logged at warning.

    Yields:
        One :class:`TokenCandidate` per discovered token. The loop runs
        until cancelled.
    """
    log = logger.bind(component=name)
    while True:
        try:
            candidates = await fetch_fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"poll error: {exc}")
            candidates = []
        for candidate in candidates:
            yield candidate
        await asyncio.sleep(interval_s)


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout_s: float = 10.0,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
    name: str = "scanner",
) -> dict | list | None:
    """Best-effort JSON GET with consistent error handling.

    Returns ``None`` on non-200 status or any network error. The caller
    treats ``None`` as "skip this poll round" and continues.
    """
    log = logger.bind(component=name)
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            headers=headers,
            params=params,
        ) as resp:
            if resp.status != 200:
                log.warning(f"GET {url} returned status {resp.status}")
                return None
            return await resp.json()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning(f"GET {url} failed: {exc}")
        return None


__all__ = ["fetch_json", "poll_loop"]

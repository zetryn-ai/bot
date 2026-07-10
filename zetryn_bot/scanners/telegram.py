"""Telegram — channel-monitor social scanner.

Source: Telegram MTProto via :mod:`telethon`
Auth: TELEGRAM_API_ID, TELEGRAM_API_HASH (https://my.telegram.org/apps).
    A session file is written on first run after interactive login; this
    file is gitignored — never commit it.
Mechanism: Long-running Telegram client subscribed to a fixed set of
    public channels. New messages are scanned for Solana mint addresses
    (base58 32-44 chars) and yielded as :class:`TokenCandidate`s.
Rate limits: Telegram FloodWait errors are honoured (sleep up to 300s).
Emits: TokenCandidate via Scanner.stream(). Each candidate's
    ``sources`` field carries a label of form ``"telegram_<category>"``
    (e.g. ``"telegram_alpha"``, ``"telegram_smart_money"``); use that
    to score signal strength on the consumer side. Telegram-specific
    metadata (channel name, trust weight, snippet) is **not** part of
    :class:`TokenCandidate` — callers that need it should also subscribe
    to a side-channel of their own (out of scope for the Scanner
    Protocol).

The cdexio shape stored an ``_telegram_channel`` / ``_telegram_trust``
key on the payload dict. The Protocol-clean version drops those keys
because they would not survive a real :class:`TokenCandidate` validation.
If the caller needs that context, they should consume from this scanner
in their own runtime layer (which knows the channel-trust mapping) and
attach metadata at sink time, not on the candidate itself.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
from loguru import logger
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.tl.types import Message

from zetryn_bot.models.token import TokenCandidate

# Solana base58 address regex — 32-44 chars, base58 alphabet (no 0OIl).
_SOLANA_CA_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")

# Known false positives: Solana program IDs and common non-token addresses.
_KNOWN_NON_TOKENS: set[str] = {
    "11111111111111111111111111111111",  # System program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv",  # ATA program
    "So11111111111111111111111111111111111111112",  # Wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


@dataclass
class ChannelConfig:
    """One Telegram channel to monitor.

    Attributes:
        username: ``@channel`` or invite hash. Must be reachable by the
            authenticated session (user is already a member).
        category: Free-form label used by the consumer to score signal
            strength (e.g. ``"alpha"``, ``"smart_money"``, ``"calls"``).
            Surfaced via the ``sources`` field on each candidate.
        display_name: Human-readable label for log messages.
    """

    username: str
    category: str
    display_name: str


class TelegramScanner:
    """Telethon-backed Telegram channel monitor.

    Yields one :class:`TokenCandidate` per detected mint address per
    incoming message. The candidate's ``sources`` field is set to
    ``["telegram_<category>"]`` so consumers can route by signal type.

    Construction is parameter-heavy because Telegram authentication is
    parameter-heavy. See the :class:`ChannelConfig` dataclass and the
    :func:`build_channels_from_config` helper for parsing channel lists
    from a Settings object.

    The :meth:`stream` method requires an :class:`aiohttp.ClientSession`
    only to satisfy the :class:`Scanner` Protocol — Telegram itself uses
    its own MTProto transport, not HTTP. The session arg is unused.
    """

    name = "telegram"

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: str,
        channels: list[ChannelConfig],
        *,
        phone: str = "",
        reconnect_delay_s: float = 30.0,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._channels = channels
        self._phone = phone
        self._reconnect_delay_s = reconnect_delay_s
        self._log = logger.bind(component=self.name)

    async def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        if not self._channels:
            self._log.info("no channels configured — scanner idle")
            await self._idle_forever()
            return

        client = TelegramClient(self._session_path, self._api_id, self._api_hash)
        # Connect + verify an EXISTING authorized session. Never call
        # client.start(): without a session it prompts for phone/OTP on stdin,
        # which blocks the whole runtime (a background task stuck in input()
        # stalls the event loop and even SIGTERM can't drain). Interactive login
        # is a separate one-time step — see scripts/telegram_login.py.
        try:
            await client.connect()
        except Exception as exc:
            self._log.error(f"connect failed: {exc} — scanner disabled")
            await self._idle_forever()
            return
        if not await client.is_user_authorized():
            self._log.error(
                "no authorized session at {}.session — run "
                "`python scripts/telegram_login.py` once; scanner disabled",
                self._session_path,
            )
            await client.disconnect()
            await self._idle_forever()
            return

        channel_map = await self._resolve_channels(client)
        if not channel_map:
            self._log.warning("no channels resolved — scanner idle")
            await client.disconnect()
            await self._idle_forever()
            return

        queue: asyncio.Queue[TokenCandidate] = asyncio.Queue()

        @client.on(events.NewMessage(chats=list(channel_map.keys())))
        async def _on_message(event: events.NewMessage.Event) -> None:
            msg: Message = event.message
            text = msg.message or ""
            if not text:
                return
            ch_config = channel_map.get(event.chat_id)
            if not ch_config:
                return
            for address in _extract_solana_addresses(text):
                candidate = TokenCandidate(
                    address=address,
                    sources=[f"telegram_{ch_config.category}"],
                )
                await queue.put(candidate)
                self._log.info(
                    f"CA detected: {address[:8]}... "
                    f"| channel={ch_config.display_name} "
                    f"[{ch_config.category}]"
                )

        self._log.info("active — listening for messages")

        # Drive the Telethon event loop in the background; pull from the
        # queue in this coroutine and yield to the caller.
        watcher = asyncio.create_task(self._watch_connection(client))
        try:
            while True:
                candidate = await queue.get()
                yield candidate
        finally:
            watcher.cancel()
            await client.disconnect()

    @staticmethod
    async def _idle_forever() -> None:
        """Park the producer without returning.

        The orchestrator supervises producers with restart-on-exit, so a clean
        ``return`` here means an immediate respawn — a hot loop re-logging the
        same 'scanner disabled' error every second (seen on the first VPS
        deploy, M8). The disabled states above are permanent for this process
        lifetime (fixing them requires a login/config change + restart), so
        sleeping forever is the honest behaviour.
        """
        while True:
            await asyncio.sleep(3600)

    async def _resolve_channels(self, client: TelegramClient) -> dict[int, ChannelConfig]:
        """Resolve configured channel usernames to entity IDs."""
        channel_map: dict[int, ChannelConfig] = {}
        for ch in self._channels:
            try:
                entity = await client.get_entity(ch.username)
                channel_map[entity.id] = ch
                self._log.info(f"  joined: {ch.display_name} ({ch.username}) [{ch.category}]")
            except UserNotParticipantError:
                self._log.warning(
                    f"  not a member of {ch.username} — skipping (join manually first)"
                )
            except FloodWaitError as exc:
                self._log.warning(f"  FloodWait {exc.seconds}s joining {ch.username} — skipping")
            except Exception as exc:
                self._log.warning(f"  failed to resolve {ch.username}: {exc}")
        return channel_map

    async def _watch_connection(self, client: TelegramClient) -> None:
        """Background task: keep the Telethon client connected and healthy."""
        while True:
            try:
                await asyncio.wait_for(client.run_until_disconnected(), timeout=30.0)
            except TimeoutError:
                if not client.is_connected():
                    self._log.warning("disconnected — reconnecting...")
                    try:
                        await client.connect()
                    except Exception as exc:
                        self._log.error(f"reconnect failed: {exc}")
                        await asyncio.sleep(self._reconnect_delay_s)
            except FloodWaitError as exc:
                self._log.warning(f"FloodWait {exc.seconds}s")
                await asyncio.sleep(min(exc.seconds, 300))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.error(f"watch error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(15)


def _extract_solana_addresses(text: str) -> list[str]:
    """Extract valid Solana mint addresses from a message body."""
    candidates = _SOLANA_CA_RE.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for addr in candidates:
        if addr in _KNOWN_NON_TOKENS or addr in seen:
            continue
        if not (32 <= len(addr) <= 44):
            continue
        seen.add(addr)
        out.append(addr)
    return out


def build_channels_from_config(settings) -> list[ChannelConfig]:
    """Parse a JSON-encoded ``TELEGRAM_CHANNELS`` env var into a config list.

    Expected format::

        TELEGRAM_CHANNELS=[
            {"username": "@alphagroup1", "category": "alpha", "display_name": "Alpha 1"},
            {"username": "@smartmoney",  "category": "smart_money", "display_name": "Smart"},
            {"username": "t.me/+hash",   "category": "calls",  "display_name": "Calls"}
        ]
    """
    log = logger.bind(component="telegram.config")
    raw = getattr(settings, "telegram_channels", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(f"TELEGRAM_CHANNELS parse error: {exc} — scanner disabled")
        return []
    out: list[ChannelConfig] = []
    for item in items:
        username = item.get("username", "").strip()
        if not username:
            continue
        out.append(
            ChannelConfig(
                username=username,
                category=item.get("category", "default"),
                display_name=item.get("display_name", username),
            )
        )
    return out


__all__ = ["ChannelConfig", "TelegramScanner", "build_channels_from_config"]

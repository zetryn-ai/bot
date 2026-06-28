from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import redis.asyncio as aioredis
from loguru import logger
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.tl.types import Message

from zetryn_bot.storage.redis_client import CHANNEL_SNIPER

log = logger.bind(component="scanner.telegram")

# Solana base58 address regex — 32-44 chars, starts with common prefixes
_SOLANA_CA_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")

# Known false positives (Solana program IDs, common non-token addresses)
_KNOWN_NON_TOKENS: set[str] = {
    "11111111111111111111111111111111",          # System program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv",  # ATA program
    "So11111111111111111111111111111111111111112",    # Wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# Trust weight per channel category — multiplied into signal strength
_TRUST_WEIGHTS: dict[str, float] = {
    "alpha":       0.9,   # verified alpha callers
    "smart_money": 0.95,  # whale/smart money trackers
    "calls":       0.7,   # general call groups (noisier)
    "launch":      0.8,   # launch announcement feeds
    "default":     0.5,
}


@dataclass
class ChannelConfig:
    username: str        # @channel_username or invite hash
    category: str        # one of _TRUST_WEIGHTS keys
    display_name: str    # human-readable label for logs


async def run_telegram_scanner(
    redis: aioredis.Redis,
    stop_event: asyncio.Event,
    api_id: int,
    api_hash: str,
    session_name: str,
    channels: list[ChannelConfig],
    phone: str = "",
    pause_event: asyncio.Event | None = None,
) -> None:
    """
    Long-running Telegram scanner task.
    Connects to Telegram MTProto, monitors configured channels in real-time,
    extracts Solana CAs from messages, and publishes to scanner.sniper.
    """
    if not channels:
        log.info("No Telegram channels configured — scanner disabled")
        return

    client = TelegramClient(session_name, api_id, api_hash)

    try:
        await client.start(phone=phone if phone else None)
    except Exception as e:
        log.error(f"Telegram login failed: {e} — scanner disabled")
        return

    log.info(f"Telegram connected — monitoring {len(channels)} channel(s)")

    # Build lookup: entity_id → ChannelConfig
    channel_map: dict[int, ChannelConfig] = {}
    for ch in channels:
        try:
            entity = await client.get_entity(ch.username)
            channel_map[entity.id] = ch
            log.info(f"  Joined: {ch.display_name} ({ch.username}) [{ch.category}]")
        except UserNotParticipantError:
            log.warning(f"  Not a member of {ch.username} — skipping (join manually first)")
        except FloodWaitError as e:
            log.warning(f"  FloodWait {e.seconds}s joining {ch.username} — skipping")
        except Exception as e:
            log.warning(f"  Failed to resolve {ch.username}: {e}")

    if not channel_map:
        log.warning("No Telegram channels joined — scanner idle")
        await client.disconnect()
        return

    @client.on(events.NewMessage(chats=list(channel_map.keys())))
    async def _on_message(event: events.NewMessage.Event) -> None:
        # Discard messages when engine is paused — connection stays alive
        if pause_event and pause_event.is_set():
            return

        msg: Message = event.message
        text = msg.message or ""
        if not text:
            return

        channel_id = event.chat_id
        ch_config = channel_map.get(channel_id)
        if not ch_config:
            return

        addresses = _extract_solana_addresses(text)
        if not addresses:
            return

        trust_weight = _TRUST_WEIGHTS.get(ch_config.category, _TRUST_WEIGHTS["default"])

        for address in addresses:
            await _publish_alpha(redis, address, text, ch_config, trust_weight)

    log.info("Telegram scanner active — listening for messages")

    # Run until stop_event is set
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                client.run_until_disconnected(),
                timeout=30,
            )
        except asyncio.TimeoutError:
            if not client.is_connected():
                log.warning("Telegram disconnected — reconnecting...")
                try:
                    await client.connect()
                except Exception as e:
                    log.error(f"Telegram reconnect failed: {e}")
                    await asyncio.sleep(30)
        except FloodWaitError as e:
            log.warning(f"Telegram FloodWait {e.seconds}s")
            await asyncio.sleep(min(e.seconds, 300))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Telegram error: {type(e).__name__}: {e}")
            await asyncio.sleep(15)

    log.info("Telegram scanner stopping")
    await client.disconnect()


def _extract_solana_addresses(text: str) -> list[str]:
    """Extract valid Solana CA addresses from message text."""
    candidates = _SOLANA_CA_RE.findall(text)
    result = []
    for addr in candidates:
        if addr in _KNOWN_NON_TOKENS:
            continue
        if len(addr) < 32 or len(addr) > 44:
            continue
        if addr not in result:
            result.append(addr)
    return result


async def _publish_alpha(
    redis: aioredis.Redis,
    address: str,
    message_text: str,
    ch_config: ChannelConfig,
    trust_weight: float,
) -> None:
    """Publish detected CA to scanner.sniper with telegram_alpha source metadata."""
    source_tag = f"telegram_{ch_config.category}"
    payload = {
        "address": address,
        "symbol": "",
        "name": "",
        "sources": [source_tag],
        "liquidity_usd": 0.0,
        "market_cap_usd": 0.0,
        "price_usd": 0.0,
        "volume_5m_usd": 0.0,
        "txns_5m": 0,
        # Telegram-specific metadata (not in TokenCandidate — used for debug logging only)
        "_telegram_channel": ch_config.display_name,
        "_telegram_trust": trust_weight,
        "_telegram_snippet": message_text[:200].replace("\n", " "),
    }

    try:
        await redis.publish(CHANNEL_SNIPER, json.dumps(payload))
        log.info(
            f"[TELEGRAM] CA detected: {address[:8]}... "
            f"| channel={ch_config.display_name} [{ch_config.category}] "
            f"| trust={trust_weight:.2f}"
        )
    except Exception as e:
        log.warning(f"Telegram publish error: {e}")


def build_channels_from_config(settings) -> list[ChannelConfig]:
    """
    Parse TELEGRAM_CHANNELS env var into ChannelConfig list.

    Format (JSON array in env var):
      TELEGRAM_CHANNELS=[
        {"username": "@alphagroup1", "category": "alpha", "display_name": "Alpha Group 1"},
        {"username": "@smartmoney", "category": "smart_money", "display_name": "Smart Money"},
        {"username": "t.me/+invitehash", "category": "calls", "display_name": "Call Group"}
      ]
    """
    raw = getattr(settings, "telegram_channels", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
        channels = []
        for item in items:
            username = item.get("username", "").strip()
            if not username:
                continue
            channels.append(ChannelConfig(
                username=username,
                category=item.get("category", "default"),
                display_name=item.get("display_name", username),
            ))
        return channels
    except json.JSONDecodeError as e:
        log.warning(f"TELEGRAM_CHANNELS parse error: {e} — telegram scanner disabled")
        return []

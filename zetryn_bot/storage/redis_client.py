from __future__ import annotations

import orjson
import redis.asyncio as aioredis
from loguru import logger

CHANNEL_SNIPER    = "scanner.sniper"     # pumpfun_ws, gecko_new, birdeye_new
CHANNEL_MIGRATION = "scanner.migration"  # pumpfun_migration — dedicated fast track
CHANNEL_MOMENTUM  = "scanner.momentum"   # dexscreener, gecko_trending, birdeye_trending, raydium

log = logger.bind(component="storage.redis")


async def connect(redis_url: str) -> aioredis.Redis:
    client = await aioredis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    await client.ping()
    log.info("Redis connected", url=redis_url)
    return client


async def publish_sniper(redis: aioredis.Redis, data: dict) -> None:
    await redis.publish(CHANNEL_SNIPER, orjson.dumps(data).decode())


async def publish_migration(redis: aioredis.Redis, data: dict) -> None:
    await redis.publish(CHANNEL_MIGRATION, orjson.dumps(data).decode())


async def publish_momentum(redis: aioredis.Redis, data: dict) -> None:
    await redis.publish(CHANNEL_MOMENTUM, orjson.dumps(data).decode())


# `ensure_consumer_groups` (cdexio's `stream.commands` / `stream.decisions`
# consumer-group bootstrap) intentionally removed — those streams belong
# to the decision tier (now handled by `zetryn-trading`), not the scanner
# transport here. Define your own consumer groups in your bot wiring code.

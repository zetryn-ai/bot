"""Storage adapters.

Phase 1: Redis only — pub/sub transport between scanners and downstream
consumers. PostgreSQL was in cdexio for decision logs and position state;
both belong to ``zetryn-trading`` (DecisionLog) or to the execution layer,
not the scanner layer.
"""

from .redis_client import (
    CHANNEL_MIGRATION,
    CHANNEL_MOMENTUM,
    CHANNEL_SNIPER,
    connect,
    publish_migration,
    publish_momentum,
    publish_sniper,
)

__all__ = [
    "CHANNEL_MIGRATION",
    "CHANNEL_MOMENTUM",
    "CHANNEL_SNIPER",
    "connect",
    "publish_migration",
    "publish_momentum",
    "publish_sniper",
]

"""Pydantic Settings for the scanner layer.

Scoped intentionally narrow: only the env vars that scanner sources, Redis
transport, and logging actually read. Wallet, execution, position
management, decision thresholds, and notification configs lived in the
cdexio ``Settings`` blob — they will be added back per concern as the bot
template grows, in their own modules (not as one monolithic Settings).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_csv(raw: str | list[str] | None) -> list[str]:
    """Split a comma-separated env string into a clean list of values."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [k.strip() for k in raw if k and k.strip()]
    return [k.strip() for k in raw.split(",") if k.strip()]


class Settings(BaseSettings):
    """Env-driven config for the scanner + transport layer.

    Loaded from ``.env`` at the repo root (see ``.env.example``). All keys
    optional except ``redis_url``; scanners are skipped at runtime when the
    keys they need are missing — see each scanner module for the contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Transport (REQUIRED) ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Solana RPC (used by helius / pumpfun / raydium scanners) ────────────
    solana_rpc_url: str = ""
    solana_rpc_fallback_url: str = ""

    # ── Scanner API keys (CSV-separated for key-pool rotation) ──────────────
    # NoDecode: skip pydantic-settings' default JSON decoding of list-typed env
    # vars so the CSV validator below handles them — otherwise an empty
    # ``HELIUS_API_KEYS=`` triggers a JSON parse error before the validator runs.
    helius_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    birdeye_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gmgn_api_key: str = ""
    pumpportal_api_key: str = ""

    # ── Telegram scanner (telethon) ─────────────────────────────────────────
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session_path: str = "telegram_session"  # do NOT commit the .session file
    telegram_channels: str = ""  # JSON list, parsed by build_channels_from_config

    # ── Twitter scanner (twitter_login) ─────────────────────────────────────
    twitter_cookies_path: str = "twitter_cookies"  # do NOT commit cookies dir

    # ── Runtime orchestration (M3) ──────────────────────────────────────────
    # Empty scanners_enabled = auto-enable all sources whose keys are present.
    # Non-empty = keep only scanners whose ``.name`` is in the list.
    scanners_enabled: Annotated[list[str], NoDecode] = Field(default_factory=list)
    workers: int = 4  # pipeline worker pool size (caps LLM concurrency)
    queue_size: int = 1000  # candidate queue maxsize (backpressure bound)
    dedup_ttl_s: float = 60.0  # window for collapsing duplicate mints

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: str = ""

    # CSV → list normalisation for env vars passed as comma-separated strings
    @field_validator("helius_api_keys", "birdeye_api_keys", "scanners_enabled", mode="before")
    @classmethod
    def _csv_to_list(cls, v):
        return _parse_csv(v)

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


def _parse_kv_floats(raw: str) -> dict[str, float]:
    """Parse a "key:1.0,key2:0.5" CSV into a dict (bad entries skipped)."""
    out: dict[str, float] = {}
    for part in _parse_csv(raw):
        if ":" not in part:
            continue
        key, _, val = part.partition(":")
        try:
            out[key.strip()] = float(val)
        except ValueError:
            continue
    return out


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
    # Pre-filter for pump.fun create events: creates whose bonding curve holds
    # less real SOL than this never enter the pipeline (dust launches are 80%
    # of the firehose and burn enricher budget just to be rejected for
    # "liquidity too low" anyway). 0 disables.
    pumpfun_min_curve_sol: float = 2.0

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

    # GeckoTerminal poll cadence. The free tier is a universal 30 calls/min
    # per IP (verified 2026-07-11) with no auth — occasional 429s are handled
    # with a 30s back-off, but on a shared/busy IP slowing these down cuts the
    # 429 (and its Telegram warning) rate to ~zero.
    gecko_new_pools_poll_s: float = 15.0
    gecko_trending_poll_s: float = 45.0

    # Birdeye poll cadence. Standard (free) keys carry 30,000 CU/MONTH each
    # (verified 2026-07-11: trending=40 CU/call, new_listing=30 CU/call) —
    # the old 60s/45s defaults burned ~115k CU/day and exhausted a 5-key pool
    # in ~31 hours. At 1800s both, a 5-key pool spends ~3.4k CU/day and
    # survives 7x24 indefinitely. Lower these only on a paid plan.
    birdeye_trending_poll_s: float = 1800.0
    birdeye_new_listing_poll_s: float = 1800.0

    # ── Decision gate thresholds ────────────────────────────────────────────
    # These feed the framework's ScannerConfig, which the three hard gates
    # (safety / intel / market) check BEFORE the LLM analyst runs. Defaults
    # match the framework. Loosen them (e.g. max_top10_pct=1.0,
    # max_bundler_wallets=100000, min_liquidity_usd=0) to let more candidates
    # survive the gates and reach the LLM — useful for exercising the AI path.
    gate_min_liquidity_usd: float = 5_000
    gate_min_volume_1h: float = 10_000
    gate_max_top10_pct: float = 0.5  # 0..1 top-10 holder concentration ceiling
    gate_min_holders: int = 50
    gate_max_bundler_wallets: int = 3
    gate_min_gmgn_safety_score: float = 40.0  # 0..100

    # ── Execution layer (M4 — paper trading) ────────────────────────────────
    # Off by default: with execution_enabled=False the runtime is identical to
    # M3 (LogSink only, no positions). Turn on to paper-trade alerts.
    execution_enabled: bool = False
    risk_base_size_sol: float = 0.1  # base position size; actual = base x confidence
    risk_min_confidence: float = 0.6  # only alerts at/above this confidence buy
    risk_max_positions: int = 5  # max concurrent open positions
    # Decision actions that trigger a buy (CSV). Default alert-only (live-safe);
    # set to "alert,watch" to paper-trade the watchlist (the analyst rarely
    # emits alert on fresh memecoins, so alert-only can sit idle for a long time).
    risk_buy_actions: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["alert"])
    # Enricher names that must be present in candidate.sources before a buy
    # (CSV; empty = disabled). Default rugcheck: enrichers fail open, so a
    # rate-limited RugCheck would otherwise let an unverified contract be
    # bought with default "safe" flags.
    risk_require_sources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["rugcheck"]
    )
    # Routes exempt from RISK_REQUIRE_SOURCES (CSV). Sniper candidates are
    # seconds old — RugCheck has not indexed them yet (29% coverage observed
    # on pumpfun_ws), so fail-closed would block every sniper buy; the sniper
    # agent's fast_safety gate is the safety authority on that route.
    risk_require_sources_exempt_routes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["sniper"]
    )
    # Churn guard: block re-buying a mint for this long after ANY close.
    # 10h dry-run data: 6 mints = 32/48 trades at net -0.076 SOL, re-buy
    # cycles 5-136 min after close; 4h covers all observed churn with margin.
    # 0 disables.
    risk_reentry_cooldown_s: float = 14400.0
    # Primary sources never bought (CSV). dexscreener_boost = paid promotion
    # (0/3, -0.077 SOL in the same data) — exit-liquidity signal.
    risk_blocked_buy_sources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["dexscreener_boost"]
    )
    # Per-source confidence floor, "source:floor" CSV. geckoterminal_trending
    # lags (tokens trend AFTER pumping): its 0.65+ buys won 33% vs 23% below.
    # If it still loses money with the floor, move it to the block list above.
    risk_source_conf_floors: str = "geckoterminal_trending:0.68"

    # ── Entry routing (M10b) ─────────────────────────────────────────────────
    # Off by default: one generalist scanner pipeline, exactly as v0.9.x. On,
    # candidates route first-match: fresh pumpfun_ws launches → sniper (rule
    # mode, no LLM), pumpfun_migration → graduation agent, everything else →
    # scanner. NOTE: sniper rule-mode buys carry action="buy" — include "buy"
    # in RISK_BUY_ACTIONS for the sniper route to trade.
    routing_enabled: bool = False
    sniper_max_age_s: float = 120.0  # pumpfun_ws launches older than this fall to scanner
    route_size_multipliers: str = "sniper:0.5,graduation:1.0,scanner:1.0"
    route_conf_floors: str = "sniper:0.6,graduation:0.6,scanner:0.6"
    risk_daily_loss_limit_sol: float = 1.0  # circuit breaker: stop buying past this daily loss
    exit_tp_pct: float = 0.30  # take profit at +30%
    exit_sl_pct: float = 0.15  # stop loss at -15%
    exit_max_hold_s: float = 1800.0  # force-close after 30 min
    exec_poll_interval_s: float = 5.0  # position monitor poll cadence

    # ── Exit intelligence (M10 — framework lifecycle agent) ─────────────────
    # Off by default: exits stay static TP/SL/max-hold. On, the framework's
    # PL1 lifecycle agent (rule mode, deterministic) evaluates each open
    # position per sweep: emergency → hard SL → time stop → trailing stop →
    # TP. Adds the trailing stop: after a run-up past the arm threshold, exit
    # when the position gives back the configured fraction of its peak —
    # "momentum died" exits before the hard SL burns the gain.
    lifecycle_enabled: bool = False
    exit_trailing_arm_pnl_pct: float = 0.20  # trailing arms after +20% peak
    exit_trailing_drawdown_pct: float = 0.50  # exit at 50% given back from peak

    # ── Wallet + live execution (M5) ─────────────────────────────────────────
    # execution_mode selects the Executor when execution_enabled=True.
    # "live" additionally requires the wallet keyfile to decrypt successfully —
    # any failure falls back to paper (logged loudly), never crashes.
    execution_mode: str = "paper"  # "paper" | "live"
    wallet_keyfile_path: str = "wallet.enc"
    wallet_passphrase: str = ""  # env only — never defaulted, never logged
    wallet_min_sol_reserve: float = 0.05  # gas reserve, never spent on trades
    wallet_max_trade_sol: float = 0.5  # absolute per-trade cap for live, independent of risk sizing
    live_slippage_bps: int = 200
    live_priority_fee_lamports: int | None = None  # None = Jupiter auto

    # ── Persistence (M6) ─────────────────────────────────────────────────────
    # Open positions, closed trades, and the daily circuit breaker persist here
    # so a restart doesn't lose them. If the DB is unreachable at startup the
    # runtime falls back to in-memory state (logged), never crashes.
    database_url: str = "postgresql+asyncpg://zetryn:zetryn@localhost:5432/zetryn_bot"
    # Opt-in: wire the framework's DecisionLog (Postgres-backed) into the agent,
    # activating ReflectiveNode (analyst learns from recent losses). Adds a
    # historical read per decision.
    enable_decision_log: bool = False

    # ── Dashboard API (M9) ───────────────────────────────────────────────────
    # Bearer token required by every /api/* endpoint. The API refuses to start
    # without it (a read-only dashboard is still trade data). Bot side only
    # uses ai_activity_retention_days (prune window for the live AI table).
    dashboard_token: str = ""
    ai_activity_retention_days: float = 14.0

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: str = ""

    # ── Notifications (M7 — Telegram) ───────────────────────────────────────
    # Off by default: NullNotifier no-ops until enabled AND both credentials
    # are present. Bot Api token (@BotFather) + chat ID — distinct from
    # TELEGRAM_API_ID/HASH above, which is the telethon *scanner*'s user session.
    notify_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_dedup_window_s: float = 900.0  # rolling window collapsing repeat warnings
    heartbeat_interval_s: float = 3600.0

    # CSV → list normalisation for env vars passed as comma-separated strings
    def parsed_source_conf_floors(self) -> dict[str, float]:
        """Parse ``risk_source_conf_floors`` ("src:0.68,src2:0.7") into a dict."""
        return _parse_kv_floats(self.risk_source_conf_floors)

    def parsed_route_size_multipliers(self) -> dict[str, float]:
        return _parse_kv_floats(self.route_size_multipliers)

    def parsed_route_conf_floors(self) -> dict[str, float]:
        return _parse_kv_floats(self.route_conf_floors)

    @field_validator(
        "helius_api_keys",
        "birdeye_api_keys",
        "scanners_enabled",
        "risk_buy_actions",
        "risk_require_sources",
        "risk_require_sources_exempt_routes",
        "risk_blocked_buy_sources",
        mode="before",
    )
    @classmethod
    def _csv_to_list(cls, v):
        return _parse_csv(v)

    # Empty-string env value for an optional int → None (operators leave
    # LIVE_PRIORITY_FEE_LAMPORTS= blank to mean "Jupiter auto"; a blank env var
    # arrives as "" which int-parsing would otherwise reject).
    @field_validator("live_priority_fee_lamports", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

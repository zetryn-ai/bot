"""Runtime entry point — ``python -m zetryn_bot`` (or the ``zetryn-bot`` script).

Boots the orchestration runtime: build the enabled scanners + enricher chain
from config, wire them to a single ``BotPipeline`` (rule-only unless an LLM
provider key is present), and run until SIGINT/SIGTERM, then drain cleanly.

Runs with no ``.env`` — the zero-arg scanners always run, so a bare checkout
still does something. Missing keys just disable the sources that need them.
"""

from __future__ import annotations

import asyncio
import signal
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from zetryn_bot.config import Settings
from zetryn_bot.logger_setup import setup_logger
from zetryn_bot.notify.log_bridge import install_log_bridge
from zetryn_bot.notify.protocol import Notifier
from zetryn_bot.notify.telegram import NullNotifier, TelegramNotifier
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import ExecutionSink, LogSink, TeeSink
from zetryn_bot.runtime.llm import try_build_llm_client
from zetryn_bot.runtime.orchestrator import Orchestrator
from zetryn_bot.runtime.registry import (
    build_enabled_scanners,
    build_enrichers,
    build_twitter_enricher,
)

log = logger.bind(component="runtime.main")


def _build_notifier(settings: Settings) -> Notifier:
    """Return a ``TelegramNotifier``, or ``NullNotifier`` if disabled/unconfigured.

    Never raises: a missing token/chat ID just means no notifications, not a
    startup failure.
    """
    if not settings.notify_enabled:
        return NullNotifier()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning(
            "NOTIFY_ENABLED=true but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing — disabled"
        )
        return NullNotifier()
    log.info("notifications ENABLED — Telegram")
    return TelegramNotifier(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        dedup_window_s=settings.notify_dedup_window_s,
    )


def _build_executor(settings: Settings, jupiter, curve=None):
    """Return ``(executor, rpc, wallet_pubkey)``. Live only when every guard passes.

    Live requires: EXECUTION_MODE=="live" AND the wallet keyfile decrypts
    successfully. Any failure logs an error and falls back to paper — this
    function never raises and never leaves execution silently un-armed. ``rpc``
    and ``wallet_pubkey`` are non-None only for a genuinely-live executor (used
    by M6 startup reconciliation); paper/fallback returns ``(executor, None, None)``.
    """
    from zetryn_bot.execution.executor import PaperExecutor

    if settings.execution_mode != "live":
        log.info("execution ENABLED (paper) — base_size={} SOL", settings.risk_base_size_sol)
        return PaperExecutor(jupiter, curve=curve), None, None

    from zetryn_bot.execution.live import LiveExecutor
    from zetryn_bot.execution.rpc import SolanaRpc
    from zetryn_bot.wallet.keystore import Wallet, WalletError

    try:
        wallet = Wallet.load(settings.wallet_keyfile_path, settings.wallet_passphrase)
    except WalletError as exc:
        log.error(
            "LIVE execution requested but wallet failed to load ({}) — falling back to PAPER", exc
        )
        return PaperExecutor(jupiter, curve=curve), None, None

    rpc = SolanaRpc(settings.solana_rpc_url)
    executor = LiveExecutor(
        wallet,
        rpc,
        jupiter,
        slippage_bps=settings.live_slippage_bps,
        priority_fee_lamports=settings.live_priority_fee_lamports,
        min_sol_reserve=settings.wallet_min_sol_reserve,
    )
    log.warning(
        "LIVE EXECUTION ACTIVE — wallet={} max_trade={} SOL — real funds will be spent",
        wallet.pubkey,
        settings.wallet_max_trade_sol,
    )
    return executor, rpc, wallet.pubkey


async def _build_session_factory(settings: Settings):
    """Build a DB session factory, or None if Postgres is unreachable (fallback).

    A connection failure logs once and returns None — the runtime then uses
    in-memory state (M4/M5 behaviour) rather than crashing.
    """
    from zetryn_bot.db.engine import build_engine, build_session_factory, check_connection

    engine = build_engine(settings.database_url)
    if not await check_connection(engine):
        await engine.dispose()
        return None
    log.info("persistence ENABLED — Postgres reachable")
    return build_session_factory(engine)


async def build_orchestrator(settings: Settings) -> Orchestrator:
    """Assemble the full runtime graph from ``settings``.

    Async because the Twitter enricher needs an ``await pool.initialize()`` to
    load its cookie store; everything else is constructed synchronously.
    """
    # build_scanner is imported lazily so importing this module (e.g. for the
    # console-script entry point) doesn't pull the framework graph eagerly.
    from strategies.agents.scanner import build_scanner
    from trading.schemas import ScannerConfig

    notifier = _build_notifier(settings)
    install_log_bridge(notifier)

    llm = try_build_llm_client()

    # Persistence (M6): a shared session factory (or None on DB failure → in-memory).
    # Set up when execution or the decision log needs it.
    session_factory = None
    if settings.execution_enabled or settings.enable_decision_log:
        session_factory = await _build_session_factory(settings)

    # Decision log (M6): Postgres-backed → activates the framework's ReflectiveNode.
    decision_log = None
    if settings.enable_decision_log and session_factory is not None:
        from zetryn.memory import DecisionLog

        from zetryn_bot.db.memory_store import PostgresStore

        decision_log = DecisionLog(PostgresStore(session_factory))
        log.info("decision log ENABLED — ReflectiveNode active (analyst learns from past losses)")

    agent = build_scanner(llm_client=llm, decision_log=decision_log)

    enrichers = build_enrichers(settings)
    twitter = await build_twitter_enricher(settings)
    if twitter is not None:
        enrichers.append(twitter)  # last: runs once symbol is known

    # Gate thresholds from Settings feed the hard gates that run before the LLM.
    config = ScannerConfig(
        # Since zetryn-trading 1.2.0, 0 means "no floor" (the old
        # division-by-zero in market_gate is fixed) — pass values through.
        min_liquidity_usd=settings.gate_min_liquidity_usd,
        min_volume_1h=settings.gate_min_volume_1h,
        max_top10_pct=settings.gate_max_top10_pct,
        min_holders=settings.gate_min_holders,
        max_bundler_wallets=settings.gate_max_bundler_wallets,
        min_gmgn_safety_score=settings.gate_min_gmgn_safety_score,
    )
    # Sink: LogSink always; add the ExecutionSink (behind a TeeSink) + its
    # position monitor loop only when execution is enabled. The executor is
    # PaperExecutor unless every live guard passes (see _build_executor).
    sink = LogSink()
    background_tasks: list = []
    if settings.execution_enabled:
        from zetryn_bot.execution.jupiter import JupiterQuote
        from zetryn_bot.execution.position import PositionTracker
        from zetryn_bot.execution.risk import RiskConfig, RiskManager

        from zetryn_bot.execution.pumpcurve import PumpCurveQuote

        jupiter = JupiterQuote()
        # Curve-phase pricing for fresh pump.fun tokens Jupiter can't route
        # yet — shared by the executor (fills) and the tracker (sweeps).
        curve = PumpCurveQuote()
        is_live = settings.execution_mode == "live"
        executor, rpc, wallet_pubkey = _build_executor(settings, jupiter, curve)

        # M6 repos (None when Postgres is unreachable → in-memory, M4/M5 behaviour).
        risk_repo = position_repo = None
        if session_factory is not None:
            from zetryn_bot.db.position_repo import PositionRepo
            from zetryn_bot.db.risk_repo import RiskStateRepo

            risk_repo = RiskStateRepo(session_factory)
            position_repo = PositionRepo(session_factory)

        risk = RiskManager(
            RiskConfig(
                base_size_sol=settings.risk_base_size_sol,
                min_confidence=settings.risk_min_confidence,
                max_positions=settings.risk_max_positions,
                daily_loss_limit_sol=settings.risk_daily_loss_limit_sol,
                buy_actions=tuple(settings.risk_buy_actions),
                require_sources=tuple(settings.risk_require_sources),
                require_sources_exempt_routes=tuple(settings.risk_require_sources_exempt_routes),
                blocked_buy_sources=tuple(settings.risk_blocked_buy_sources),
                source_conf_floors=settings.parsed_source_conf_floors(),
                route_size_multipliers=settings.parsed_route_size_multipliers(),
                route_conf_floors=settings.parsed_route_conf_floors(),
                take_profit_pct=settings.exit_tp_pct,
                stop_loss_pct=settings.exit_sl_pct,
                max_hold_s=settings.exit_max_hold_s,
                # Only cap live trades — paper mode has no real funds to protect.
                max_trade_sol=settings.wallet_max_trade_sol if is_live else None,
            ),
            repo=risk_repo,
            notifier=notifier,
        )
        await risk.load()  # restore today's circuit-breaker PnL

        # M10: framework lifecycle agent replaces the static exit triple.
        lifecycle = None
        if settings.lifecycle_enabled:
            from zetryn_bot.execution.lifecycle import LifecycleEngine

            lifecycle = LifecycleEngine(
                take_profit_pct=settings.exit_tp_pct,
                stop_loss_pct=settings.exit_sl_pct,
                max_hold_s=settings.exit_max_hold_s,
                trailing_arm_pnl_pct=settings.exit_trailing_arm_pnl_pct,
                trailing_drawdown_pct=settings.exit_trailing_drawdown_pct,
                tp_ladder=settings.parsed_tp_ladder() or None,
            )
            log.info(
                "lifecycle agent ENABLED — framework rule exits "
                "(TP +{:.0%}, SL -{:.0%}, max-hold {:.0f}s, trailing arm "
                "+{:.0%} / drawdown {:.0%})",
                settings.exit_tp_pct,
                settings.exit_sl_pct,
                settings.exit_max_hold_s,
                settings.exit_trailing_arm_pnl_pct,
                settings.exit_trailing_drawdown_pct,
            )

        tracker = PositionTracker(
            executor,
            jupiter,
            risk,
            poll_interval_s=settings.exec_poll_interval_s,
            repo=position_repo,
            execution_mode=settings.execution_mode,
            notifier=notifier,
            lifecycle=lifecycle,
            reentry_cooldown_s=settings.risk_reentry_cooldown_s,
            curve=curve,
        )
        await tracker.load_and_reconcile(wallet_pubkey, rpc)  # restore + (live) verify on-chain

        # M9: live AI-activity feed (only when persistence is up). Order
        # matters — AiActivitySink must run BEFORE ExecutionSink so the row
        # exists when the outcome is reported.
        activity = None
        if session_factory is not None:
            from zetryn_bot.db.ai_activity_repo import AiActivityRepo
            from zetryn_bot.pipeline.sinks import AiActivitySink

            activity_repo = AiActivityRepo(session_factory)
            pruned = await activity_repo.prune(settings.ai_activity_retention_days)
            if pruned:
                log.info("ai-activity: pruned {} rows past retention", pruned)
            activity = AiActivitySink(activity_repo)

        sinks: list = [LogSink()]
        if activity is not None:
            sinks.append(activity)
        sinks.append(ExecutionSink(risk, executor, tracker, notifier=notifier, activity=activity))
        sink = TeeSink(sinks)
        background_tasks.append(("execution.monitor", tracker.monitor_loop))

    if settings.notify_enabled:
        from zetryn_bot.notify.heartbeat import heartbeat_loop

        tracker_for_heartbeat = tracker if settings.execution_enabled else None
        background_tasks.append(
            (
                "notify.heartbeat",
                lambda: heartbeat_loop(
                    notifier, tracker_for_heartbeat, settings.heartbeat_interval_s
                ),
            )
        )

    if settings.routing_enabled:
        # M10b: first-match routing — fresh pumpfun launches → sniper (rule
        # mode, no LLM), migrations → graduation agent, the rest → scanner.
        # All routes share the enrichers and the sink, so global risk policy
        # (breaker, max positions, cooldown, blocked sources) is unchanged.
        from strategies.agents.graduation import build_graduation
        from strategies.agents.sniper import build_sniper
        from trading.schemas import GraduationConfig, SniperConfig

        from zetryn_bot.routing.graduation import GraduationPipeline
        from zetryn_bot.routing.launch_memory import LaunchMemory
        from zetryn_bot.routing.router import (
            Route,
            RoutedPipeline,
            live_age_seconds,
            primary_source,
        )

        launch_memory = LaunchMemory()
        max_age = settings.sniper_max_age_s
        sniper_pipe = BotPipeline(
            build_sniper(llm_client=None),  # rule mode: no LLM in the hot loop
            enrichers=enrichers,
            sink=sink,
            config=SniperConfig(),
            route_label="sniper",
        )
        graduation_pipe = GraduationPipeline(
            build_graduation(llm_client=llm, decision_log=decision_log),
            enrichers=enrichers,
            sink=sink,
            # Relaxed gates for fields our feeds don't carry yet (unique
            # buyers, LP-burn state, initial SOL liquidity) — see M10b design
            # doc §3.3. Tighten via config once those feeds exist (M10c).
            config=GraduationConfig(
                min_unique_buyers=0,
                require_lp_burned=False,
                min_initial_liquidity_sol=0.0,
                max_pair_age_seconds=3600.0,
            ),
            launch_memory=launch_memory,
        )
        scanner_pipe = BotPipeline(
            agent, enrichers=enrichers, sink=sink, config=config, route_label="scanner"
        )
        pipeline = RoutedPipeline(
            routes=[
                Route(
                    "sniper",
                    # live age, not the parse-time snapshot — queue latency
                    # must not smuggle a stale launch into the sniper.
                    lambda c: primary_source(c) == "pumpfun_ws" and live_age_seconds(c) <= max_age,
                    sniper_pipe,
                ),
                Route(
                    "graduation",
                    lambda c: primary_source(c) == "pumpfun_migration",
                    graduation_pipe,
                ),
            ],
            fallback=Route("scanner", lambda c: True, scanner_pipe),
            launch_memory=launch_memory,
        )
        log.info(
            "entry routing ENABLED — sniper (pumpfun_ws age<={:.0f}s, rule mode) / "
            "graduation (pumpfun_migration) / scanner fallback",
            max_age,
        )
    else:
        pipeline = BotPipeline(agent, enrichers=enrichers, sink=sink, config=config)
    return Orchestrator(
        pipeline,
        build_enabled_scanners(settings),
        workers=settings.workers,
        queue_size=settings.queue_size,
        dedup_ttl_s=settings.dedup_ttl_s,
        background_tasks=background_tasks,
    )


async def _run(settings: Settings) -> None:
    orch = await build_orchestrator(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await orch.start()
    log.info(
        "runtime up — {} scanner(s), {} worker(s); Ctrl-C to stop",
        len(orch.scanners),
        settings.workers,
    )
    try:
        await stop.wait()
    finally:
        log.info("shutdown signal received — draining")
        await orch.shutdown()


def main() -> int:
    # Load .env into the process environment BEFORE anything else. Pydantic
    # reads .env into Settings on its own, but the framework's LLM provider
    # resolver reads os.environ directly — without this, LLM keys placed in
    # .env are invisible to zetryn-trading and the runtime silently stays
    # rule-only. Loading here keeps the boundary intact: the bot only makes
    # the shared file visible; the framework still owns which vars it reads.
    load_dotenv()

    settings = Settings()
    setup_logger(settings.log_level, settings.log_file)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:  # pragma: no cover - defensive; signal handler covers this
        pass
    except Exception:
        # Crash dump (M7): full traceback to a local file (offline debugging),
        # short excerpt pushed to Telegram (a fresh loop — the crashed one is
        # gone). Best-effort: a broken notifier here must not mask the crash.
        tb = traceback.format_exc()
        dump_path = Path(f"crash-{int(time.time())}.log")
        dump_path.write_text(tb)
        log.critical("unhandled crash — traceback dumped to {}", dump_path)
        try:
            notifier = _build_notifier(settings)
            asyncio.run(notifier.notify(f"\U0001f534 CRASHED — see {dump_path}\n\n{tb[-800:]}"))
        except Exception:
            log.exception("crash notification itself failed")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

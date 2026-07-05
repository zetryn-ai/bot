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

from dotenv import load_dotenv
from loguru import logger

from zetryn_bot.config import Settings
from zetryn_bot.logger_setup import setup_logger
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


def _build_executor(settings: Settings, jupiter):
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
        return PaperExecutor(jupiter), None, None

    from zetryn_bot.execution.live import LiveExecutor
    from zetryn_bot.execution.rpc import SolanaRpc
    from zetryn_bot.wallet.keystore import Wallet, WalletError

    try:
        wallet = Wallet.load(settings.wallet_keyfile_path, settings.wallet_passphrase)
    except WalletError as exc:
        log.error(
            "LIVE execution requested but wallet failed to load ({}) — falling back to PAPER", exc
        )
        return PaperExecutor(jupiter), None, None

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
        # Floor at 1: the framework's market_gate divides by these * 5, so a
        # literal 0 would crash it. 1 is effectively "no minimum" and safe.
        min_liquidity_usd=max(1.0, settings.gate_min_liquidity_usd),
        min_volume_1h=max(1.0, settings.gate_min_volume_1h),
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

        jupiter = JupiterQuote()
        is_live = settings.execution_mode == "live"
        executor, rpc, wallet_pubkey = _build_executor(settings, jupiter)

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
                take_profit_pct=settings.exit_tp_pct,
                stop_loss_pct=settings.exit_sl_pct,
                max_hold_s=settings.exit_max_hold_s,
                # Only cap live trades — paper mode has no real funds to protect.
                max_trade_sol=settings.wallet_max_trade_sol if is_live else None,
            ),
            repo=risk_repo,
        )
        await risk.load()  # restore today's circuit-breaker PnL

        tracker = PositionTracker(
            executor,
            jupiter,
            risk,
            poll_interval_s=settings.exec_poll_interval_s,
            repo=position_repo,
            execution_mode=settings.execution_mode,
        )
        await tracker.load_and_reconcile(wallet_pubkey, rpc)  # restore + (live) verify on-chain

        sink = TeeSink([LogSink(), ExecutionSink(risk, executor, tracker)])
        background_tasks.append(("execution.monitor", tracker.monitor_loop))

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

from loguru import logger

from zetryn_bot.config import Settings
from zetryn_bot.logger_setup import setup_logger
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.pipeline.sinks import LogSink
from zetryn_bot.runtime.llm import try_build_llm_client
from zetryn_bot.runtime.orchestrator import Orchestrator
from zetryn_bot.runtime.registry import build_enabled_scanners, build_enrichers

log = logger.bind(component="runtime.main")


def build_orchestrator(settings: Settings) -> Orchestrator:
    """Assemble the full runtime graph from ``settings`` (no I/O yet)."""
    # build_scanner is imported lazily so importing this module (e.g. for the
    # console-script entry point) doesn't pull the framework graph eagerly.
    from strategies.agents.scanner import build_scanner

    llm = try_build_llm_client()
    agent = build_scanner(llm_client=llm)
    pipeline = BotPipeline(
        agent,
        enrichers=build_enrichers(settings),
        sink=LogSink(),
    )
    return Orchestrator(
        pipeline,
        build_enabled_scanners(settings),
        workers=settings.workers,
        queue_size=settings.queue_size,
        dedup_ttl_s=settings.dedup_ttl_s,
    )


async def _run(settings: Settings) -> None:
    orch = build_orchestrator(settings)

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
    settings = Settings()
    setup_logger(settings.log_level, settings.log_file)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:  # pragma: no cover - defensive; signal handler covers this
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

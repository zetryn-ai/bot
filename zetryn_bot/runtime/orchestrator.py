"""``Orchestrator`` — runs scanners concurrently into a shared pipeline.

Producers (each scanner, crash-supervised) enqueue deduped candidates onto a
bounded ``asyncio.Queue``; a fixed pool of worker tasks dequeues and drives the
M2 :class:`~zetryn_bot.pipeline.runner.BotPipeline`. This decouples scan rate
from decision rate (backpressure via the queue's ``maxsize``) and caps
concurrency (and thus LLM calls) at the worker count.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.runner import BotPipeline
from zetryn_bot.runtime.dedup import DedupCache
from zetryn_bot.scanners.protocol import Scanner
from zetryn_bot.utils.supervisor import supervise

log = logger.bind(component="runtime.orchestrator")


class Orchestrator:
    """Owns the queue, scanner producer tasks, worker pool, and lifecycle."""

    def __init__(
        self,
        pipeline: BotPipeline,
        scanners: list[Scanner],
        *,
        workers: int = 4,
        queue_size: int = 1000,
        dedup_ttl_s: float = 60.0,
        background_tasks: list[tuple[str, Callable[[], Awaitable[None]]]] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.scanners = scanners
        self._workers = workers
        # (name, coro-fn) pairs run as supervised tasks alongside producers/workers
        # — e.g. the M4 position monitor loop. Each is crash-restarted like a scanner.
        self._background_tasks = background_tasks or []
        self.queue: asyncio.Queue[TokenCandidate] = asyncio.Queue(maxsize=queue_size)
        self.dedup = DedupCache(ttl_s=dedup_ttl_s)
        self.session: aiohttp.ClientSession | None = None
        self._tasks: list[asyncio.Task] = []

    async def _produce(self, scanner: Scanner, session: aiohttp.ClientSession) -> None:
        """Stream one scanner's candidates onto the queue, skipping dups."""
        async for candidate in scanner.stream(session):
            if self.dedup.seen(candidate.address):
                continue
            await self.queue.put(candidate)  # blocks (backpressure) when full

    async def _consume(self, session: aiohttp.ClientSession) -> None:
        """Dequeue candidates and run each through the pipeline. Never dies on error."""
        while True:
            candidate = await self.queue.get()
            try:
                await self.pipeline.process(candidate, session)
            except Exception:
                logger.exception("pipeline error for {}", candidate.address)
            finally:
                self.queue.task_done()

    async def start(self) -> None:
        """Open the shared session and launch producer + worker tasks."""
        self.session = aiohttp.ClientSession()
        producers = [
            asyncio.create_task(
                supervise(scanner.name, self._produce, scanner, self.session),
                name=f"producer:{scanner.name}",
            )
            for scanner in self.scanners
        ]
        consumers = [
            asyncio.create_task(self._consume(self.session), name=f"worker:{i}")
            for i in range(self._workers)
        ]
        background = [
            asyncio.create_task(supervise(name, coro_fn), name=f"bg:{name}")
            for name, coro_fn in self._background_tasks
        ]
        self._tasks = producers + consumers + background
        log.info(
            "orchestrator started — {} producer(s), {} worker(s), {} background",
            len(producers),
            len(consumers),
            len(background),
        )

    async def drain(self) -> None:
        """Block until every currently-enqueued candidate has been processed.

        Convenience for finite runs (smoke script). Note: it waits on the queue,
        not on producers — a live scanner keeps streaming, so this returns once
        the in-flight backlog clears, not when scanning "ends". The long-running
        entry point never calls this; it waits on a shutdown signal and calls
        :meth:`shutdown` directly.
        """
        await self.queue.join()

    async def shutdown(self) -> None:
        """Cancel all tasks, wait for them to unwind, and close the session."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self.session is not None:
            await self.session.close()
            self.session = None
        log.info("orchestrator shut down")

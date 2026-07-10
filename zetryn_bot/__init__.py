"""Zetryn Bot — Solana memecoin scanner sources and bot scaffolding.

Companion to `zetryn-trading` (the decision framework). This package owns
the I/O side: fetching token signals from public DEX sources, normalising
them to a shared schema, and publishing onto a Redis bus for downstream
consumers (the decision graph from `zetryn-trading`, your execution layer,
your dashboard, etc.).

Boundary mirror of `zetryn-trading`:

    zetryn-trading  : decides   (graph + LLM + rules)
    zetryn_bot      : executes  (scanners → normalise → publish; later: swap, wallet)

M1 shipped the scanner sources (Phase 1 scaffolding). M2 added the wire-up
to `zetryn-trading`: an adapter bridging `TokenCandidate` -> `TokenInput`,
an enrichment pipeline, and a `BotPipeline` runner. M3 adds the runtime:
`python -m zetryn_bot` boots the enabled scanners concurrently and fans
their candidates through a shared queue + worker pool into the pipeline,
with crash-safe supervision and graceful shutdown. M4 adds a paper-trading
execution layer: `alert`s are risk-sized into paper positions at real
Jupiter quote prices, tracked and auto-exited on TP/SL/max-hold (off by
default via `EXECUTION_ENABLED`). M5 adds a real Solana wallet — an
encrypted keypair and a `LiveExecutor` that signs and submits real Jupiter
swaps, selected via `EXECUTION_MODE=live` behind layered safety guards
(falls back to paper on any guard failure). M6 adds PostgreSQL persistence:
open positions, closed trades, and the daily circuit breaker survive
restarts; live positions are reconciled against on-chain balances on
startup; and the framework's `DecisionLog`/`ReflectiveNode` can be
activated via a Postgres-backed store. M7 adds observability: a Telegram
notifier for trade open/close, circuit-breaker trips, critical errors,
and scanner rate-limit/key-rotation warnings (deduplicated), a periodic
heartbeat, and crash-dump capture. M8 adds deployment: a Docker image
(two-stage, non-root), a VPS compose file supervised by the Docker daemon
(`restart: unless-stopped`), and a one-command deploy script. A dashboard
lands in a later phase (see ROADMAP.md).
"""

__version__ = "0.8.0"

__all__ = ["__version__"]

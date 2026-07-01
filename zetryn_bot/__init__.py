"""Zetryn Bot — Solana memecoin scanner sources and bot scaffolding.

Companion to `zetryn-trading` (the decision framework). This package owns
the I/O side: fetching token signals from public DEX sources, normalising
them to a shared schema, and publishing onto a Redis bus for downstream
consumers (the decision graph from `zetryn-trading`, your execution layer,
your dashboard, etc.).

Boundary mirror of `zetryn-trading`:

    zetryn-trading  : decides   (graph + LLM + rules)
    zetryn_bot      : executes  (scanners → normalise → publish; later: swap, wallet)

M1 shipped the scanner sources (Phase 1 scaffolding). M2 adds the wire-up
to `zetryn-trading`: an adapter bridging `TokenCandidate` -> `TokenInput`,
an enrichment pipeline, and a `BotPipeline` runner that invokes a compiled
agent graph and emits the resulting `Decision` to a swappable sink.
Orchestration, swap execution, and wallet land in subsequent phases (see
ROADMAP.md).
"""

__version__ = "0.2.0"

__all__ = ["__version__"]

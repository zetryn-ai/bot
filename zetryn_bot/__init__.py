"""Zetryn Bot — Solana memecoin scanner sources and bot scaffolding.

Companion to `zetryn-trading` (the decision framework). This package owns
the I/O side: fetching token signals from public DEX sources, normalising
them to a shared schema, and publishing onto a Redis bus for downstream
consumers (the decision graph from `zetryn-trading`, your execution layer,
your dashboard, etc.).

Boundary mirror of `zetryn-trading`:

    zetryn-trading  : decides   (graph + LLM + rules)
    zetryn_bot      : executes  (scanners → normalise → publish; later: swap, wallet)

This is **Phase 1 scaffolding** — scanner sources only. Orchestration, swap
execution, wallet, and the wire-up to `zetryn-trading` agents will be added
in subsequent phases (see ROADMAP discussion).
"""

__version__ = "0.0.0"

__all__ = ["__version__"]

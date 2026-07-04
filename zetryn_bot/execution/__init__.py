"""Execution layer (M4) — paper-trading engine.

Turns a `Decision(action=alert)` into a risk-sized paper trade at real Jupiter
quote prices, tracks the open position, and auto-exits on TP / SL / max-hold.
No transactions, no keypair, no funds — everything sits behind a swappable
`Executor` Protocol so a real `LiveExecutor` slots in at M5 without touching the
risk or position layers.
"""

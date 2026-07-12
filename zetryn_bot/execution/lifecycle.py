"""LifecycleEngine — framework-driven exit decisions (M10).

Wraps the framework's PL1 lifecycle agent (``strategies.agents.lifecycle``,
rule mode — no LLM call per tick) around one ``PositionContext`` per monitor
sweep. The engine owns nothing but peak-PnL bookkeeping; quoting, selling, and
persistence stay in PositionTracker/Executor, honouring the framework boundary:
the framework decides, the bot executes.

Gates evaluated per tick (framework order): emergency_exit → hard_stop_loss →
time_stop → trailing_stop → tp_ladder → rule_hold. Versus the old static
TP/SL/max-hold triple this adds the trailing stop — "momentum died after a
run-up" exits before the hard SL gives the gain back.

Peak PnL is tracked in-memory only: after a restart the trailing stop re-arms
from the first post-restart quote (a position that peaked +50% before the
restart starts over) — accepted for M10, persisting peaks is a follow-up.

Multi-rung partial exits (M10.1, 2026-07-12): pass ``tp_ladder`` and the
engine feeds executed rungs back as ``PositionState.partial_exits`` so the
framework never recommends the same rung twice. The tracker performs the
partial sell and calls :meth:`mark_rung`.
"""

from __future__ import annotations

import time

from loguru import logger
from strategies.agents.lifecycle import build_lifecycle
from trading.schemas import (
    Decision,
    LifecycleConfig,
    PartialExit,
    PositionContext,
    PositionState,
    TokenInput,
)
from zetryn.core import State

from zetryn_bot.execution.executor import Position

log = logger.bind(component="execution.lifecycle")


class LifecycleEngine:
    """One compiled lifecycle graph + per-position peak tracking."""

    def __init__(
        self,
        *,
        take_profit_pct: float,
        stop_loss_pct: float,
        max_hold_s: float,
        trailing_arm_pnl_pct: float = 0.20,
        trailing_drawdown_pct: float = 0.50,
        tp_ladder: list[tuple[float, float]] | None = None,
    ) -> None:
        self._graph = build_lifecycle()  # rule mode: deterministic, sub-ms
        self._ladder = sorted(tp_ladder or [(take_profit_pct, 1.0)], key=lambda r: r[0])
        self._config = LifecycleConfig(
            decision_mode="rule",
            stop_loss_pct=-abs(stop_loss_pct),
            max_hold_seconds=max_hold_s,
            trailing_arms_at_pnl_pct=trailing_arm_pnl_pct,
            trailing_drawdown_pct=trailing_drawdown_pct,
            tp_ladder=self._ladder,
        )
        self._peaks: dict[str, float] = {}
        self._partials: dict[str, list[PartialExit]] = {}

    async def evaluate(self, position: Position, current_sol: float, holding_s: float) -> Decision:
        """Run one lifecycle tick. ``action == "hold"`` keeps the position open."""
        pnl_pct = (
            (current_sol - position.size_sol) / position.size_sol if position.size_sol else 0.0
        )
        peak = max(self._peaks.get(position.mint, pnl_pct), pnl_pct)
        self._peaks[position.mint] = peak
        # Fraction of the peak *value* given back: 1 - (1+pnl)/(1+peak).
        peak_mult = 1.0 + peak
        drawdown = max(0.0, (peak - pnl_pct) / peak_mult) if peak_mult > 0 else 0.0

        ctx = PositionContext(
            # Minimal snapshot: the tracker holds no fresh enrichment, so the
            # emergency (rug) gate is inert until entry snapshots are stored.
            token=TokenInput(mint=position.mint, symbol=position.symbol),
            position=PositionState(
                entry_price=1.0,
                entry_size=position.size_sol,
                entry_ts=0.0,
                current_price=1.0 + pnl_pct,
                current_size=position.size_sol,
                pnl_pct=pnl_pct,
                holding_seconds=holding_s,
                peak_pnl_pct=peak,
                drawdown_from_peak_pct=drawdown,
                partial_exits=self._partials.get(position.mint, []),
            ),
            config=self._config,
        )
        state = await self._graph.run(State(context=ctx))
        return state.output

    def forget(self, mint: str) -> None:
        """Drop peak + rung bookkeeping once a position closes."""
        self._peaks.pop(mint, None)
        self._partials.pop(mint, None)

    def mark_rung(self, mint: str, pnl_pct: float, sold_size: float) -> float:
        """Record an executed partial exit and return the RUNG threshold hit.

        The framework skips rungs by exact threshold match against
        ``partial_exits[].sold_at_pnl_pct``, so the recorded value must be the
        configured threshold — recording the actual pnl (e.g. 0.312 for the
        0.30 rung) would refire the rung every sweep and drain the position.
        """
        hit = {round(pe.sold_at_pnl_pct, 6) for pe in self._partials.get(mint, [])}
        rung = next(
            (t for t, _ in self._ladder if round(t, 6) not in hit and pnl_pct >= t),
            pnl_pct,
        )
        self._partials.setdefault(mint, []).append(
            PartialExit(sold_at_pnl_pct=rung, sold_size=sold_size, sold_at_ts=time.time())
        )
        return rung

    def next_rung(self, mint: str) -> float | None:
        """The next un-hit ladder threshold — the remainder's display target."""
        hit = {round(pe.sold_at_pnl_pct, 6) for pe in self._partials.get(mint, [])}
        for threshold, _ in self._ladder:
            if round(threshold, 6) not in hit:
                return threshold
        return None

    def restore_partials(self, mint: str, entries: list[dict]) -> None:
        """Rebuild executed rungs after a restart (from the positions row)."""
        if entries:
            self._partials[mint] = [PartialExit(**e) for e in entries]

    def partials_as_dicts(self, mint: str) -> list[dict]:
        return [pe.model_dump() for pe in self._partials.get(mint, [])]

    @staticmethod
    def close_reason(decision: Decision) -> str:
        """Map a framework Decision to the tracker's close-reason taxonomy.

        Keeps the DB values the dashboard already knows ("take_profit",
        "stop_loss", "max_hold") and adds the new framework-only exits.
        """
        if decision.flags.get("emergency"):
            return "emergency"
        first = decision.reasons[0] if decision.reasons else ""
        prefix = first.split(":", 1)[0].strip()
        if prefix == "hard_stop_loss":
            return "stop_loss"
        if prefix == "time_stop":
            return "max_hold"
        if prefix == "trailing_stop":
            return "trailing_stop"
        if prefix.startswith("tp_ladder"):
            return "take_profit"
        return decision.action

"""PositionTracker — open positions + the exit monitor loop, with persistence.

Open positions and closed trades persist to Postgres (M6) when a repo is
supplied; without one it behaves exactly as M4/M5 (in-memory only). On startup
`load_and_reconcile` restores open positions from the DB, and in live mode
verifies each against the actual on-chain token balance before resuming — a
mismatch is flagged `needs_review` and excluded from the monitor loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from loguru import logger
from solders.pubkey import Pubkey

from zetryn_bot.execution.executor import ClosedTrade, Executor, Position
from zetryn_bot.execution.jupiter import SOL_MINT, JupiterQuote, lamports_to_sol
from zetryn_bot.execution.risk import RiskManager

log = logger.bind(component="execution.positions")


class PositionTracker:
    """Owns open/closed paper positions and the exit-monitoring loop."""

    def __init__(
        self,
        executor: Executor,
        jupiter: JupiterQuote,
        risk: RiskManager,
        *,
        poll_interval_s: float = 5.0,
        stats_interval_s: float = 60.0,
        now_fn: Callable[[], float] = time.monotonic,
        repo=None,  # PositionRepo | None — None keeps M4/M5 in-memory behaviour
        execution_mode: str = "paper",
        notifier=None,
        dead_route_after: int = 10,  # consecutive no-route sweeps before a dead-route close
        lifecycle=None,  # LifecycleEngine | None — None keeps static TP/SL/max-hold exits
        route_lifecycles=None,  # dict[route, LifecycleEngine] — M12 per-route exit profiles
        reentry_cooldown_s: float = 0.0,  # block re-buying a mint this long after ANY close
        curve=None,  # PumpCurveQuote | None — prices curve-phase tokens Jupiter can't route
        sl_ratchet: dict[float, float] | None = None,  # rung -> new stop level (rel. entry)
    ) -> None:
        from zetryn_bot.notify.telegram import NullNotifier

        self._executor = executor
        self._jup = jupiter
        self._risk = risk
        self._poll_interval_s = poll_interval_s
        self._stats_interval_s = stats_interval_s
        self._now = now_fn
        self._repo = repo
        self._execution_mode = execution_mode
        self._notifier = notifier or NullNotifier()
        self._dead_route_after = dead_route_after
        self._lifecycle = lifecycle
        self._route_lifecycles = route_lifecycles or {}
        # Re-entry cooldown (churn guard): the 10h VPS dry run had 6 mints
        # account for 32/48 trades at net -0.076 SOL — trending scanners
        # re-emit the same token every poll, the 60s dedup expires, and the
        # analyst re-approves a fresh buy minutes after a stop-loss.
        # Re-entries after take-profit were ALSO net negative in that data
        # (mogdog: 3 TP +0.061 vs 7 SL -0.075), so the cooldown is flat
        # across close reasons.
        self._reentry_cooldown_s = reentry_cooldown_s
        self._curve = curve
        # Profit-lock: after rung X the remainder's stop moves UP (stored as a
        # NEGATIVE position.stop_loss_pct — "stop above entry"). A winner can
        # then no longer round-trip into a loser.
        self._sl_ratchet = sl_ratchet or {}
        # Junk-quote guard (Jotchua incident 2026-07-13: one Jupiter quote
        # valued 580M tokens at 84.6 SOL; 30s later the same amount quoted
        # 0.0297 SOL — the phantom fill booked +282,015%). A quote implying
        # >= extreme_x on a position that was NOT extreme last sweep must be
        # CONFIRMED by the next sweep before any exit acts on it. Real
        # moonshots persist across sweeps (bulk: 18-19x across 3 quotes/60s);
        # junk evaporates.
        self._extreme_x = 20.0
        self._extreme_pending: dict[str, tuple[float, float]] = {}  # mint -> (ts, multiple)
        self._cooldowns: dict[str, float] = {}  # mint -> monotonic deadline
        self._route_fails: dict[str, int] = {}
        self._open: dict[str, Position] = {}
        self._closed: list[ClosedTrade] = []

    def open_count(self) -> int:
        return len(self._open)

    def holds(self, mint: str) -> bool:
        return mint in self._open

    def in_cooldown(self, mint: str) -> bool:
        """True while ``mint`` is inside its post-close re-entry cooldown."""
        deadline = self._cooldowns.get(mint)
        if deadline is None:
            return False
        if self._now() >= deadline:
            del self._cooldowns[mint]
            return False
        return True

    def _start_cooldown(self, mint: str, *, elapsed_s: float = 0.0) -> None:
        if self._reentry_cooldown_s <= 0:
            return
        remaining = self._reentry_cooldown_s - elapsed_s
        if remaining > 0:
            self._cooldowns[mint] = self._now() + remaining

    async def add(self, position: Position) -> None:
        """Register a freshly-opened position, stamping its open time + persisting."""
        position.opened_at = self._now()
        self._open[position.mint] = position
        if self._repo is not None:
            await self._repo.save_open(position, self._execution_mode)

    async def load_and_reconcile(self, wallet_pubkey: str | None, rpc) -> None:
        """Restore open positions from the DB. In live mode, verify each against
        the on-chain token balance; a mismatch is marked ``needs_review`` and
        excluded from the monitor loop (never auto-traded on stale data)."""
        if self._repo is None:
            return
        loaded = await self._repo.load_open(now_fn=self._now)
        reviewed = 0
        for pos in loaded:
            if self._execution_mode == "live" and rpc is not None and wallet_pubkey:
                onchain = await rpc.get_token_balance_for_mint(
                    Pubkey.from_string(wallet_pubkey), Pubkey.from_string(pos.mint)
                )
                if onchain != pos.tokens_atomic:
                    log.warning(
                        "RECONCILE MISMATCH {} — db={} on-chain={} — marking needs_review, "
                        "NOT auto-trading",
                        pos.symbol or pos.mint[:8],
                        pos.tokens_atomic,
                        onchain,
                    )
                    await self._repo.mark_needs_review(pos.mint)
                    reviewed += 1
                    continue
            self._open[pos.mint] = pos
        log.info(
            "restored {} open position(s) from DB ({} flagged needs_review)",
            len(self._open),
            reviewed,
        )
        # Restore executed TP-ladder rungs so a restart can't refire a rung
        # that already sold (each refire would halve the position again).
        if hasattr(self._repo, "load_partials_map"):
            partials_map = await self._repo.load_partials_map()
            for mint, entries in partials_map.items():
                pos = self._open.get(mint)
                engine = self._engine(pos) if pos is not None else None
                if engine is not None:
                    engine.restore_partials(mint, entries)
        # Rebuild re-entry cooldowns from recent closed trades — otherwise a
        # container restart would reset every cooldown and churn could resume.
        if self._reentry_cooldown_s > 0:
            recent = await self._repo.load_recent_close_ages(self._reentry_cooldown_s)
            for mint, elapsed_s in recent:
                self._start_cooldown(mint, elapsed_s=elapsed_s)
            if recent:
                log.info("restored {} re-entry cooldown(s) from closed trades", len(recent))

    def _engine(self, position: Position):
        """The lifecycle engine owning this position's exits (route-specific
        profile when configured, else the default engine, else None=static)."""
        return self._route_lifecycles.get(position.route) or self._lifecycle

    def _exit_reason(self, position: Position, current_sol: float) -> str | None:
        pnl_pct = (
            (current_sol - position.size_sol) / position.size_sol if position.size_sol else 0.0
        )
        if pnl_pct >= position.take_profit_pct:
            return "take_profit"
        if pnl_pct <= -position.stop_loss_pct:
            return "stop_loss"
        if (self._now() - position.opened_at) >= position.max_hold_s:
            return "max_hold"
        return None

    async def _mark(self, mint: str, pnl_pct: float) -> None:
        """Persist the mark-to-market pnl for the dashboard. Best-effort."""
        if self._repo is not None and hasattr(self._repo, "update_mark"):
            try:
                await self._repo.update_mark(mint, pnl_pct)
            except Exception:
                log.exception("mark-to-market update failed (trading unaffected)")

    async def _partial_close(self, position: Position, decision, pnl_pct: float, engine) -> None:
        """Sell a ladder-rung fraction, keep the remainder riding.

        ``decision.size`` is the SOL slice of the CURRENT basis to sell (the
        framework computes ``fraction * current_size``). The remainder keeps
        the entry-relative SL and the (already armed) trailing stop; the
        realized slice lands in ``closed_trades`` as ``partial_tp``.
        """
        from dataclasses import replace

        if not decision.size or position.size_sol <= 0:
            return
        fraction = min(1.0, float(decision.size) / position.size_sol)
        tokens_part = min(position.tokens_atomic, int(position.tokens_atomic * fraction))
        if tokens_part <= 0:
            return
        basis_part = position.size_sol * fraction
        partial_pos = replace(position, tokens_atomic=tokens_part, size_sol=basis_part)
        trade = await self._executor.sell(partial_pos, "partial_tp")
        if trade is None:
            return  # sell failed — rung stays un-hit, retried next sweep
        # Fill sanity: the executor re-quotes, and THAT quote can be the junk
        # one even when the sweep quote was sane. A fill >3x away from the
        # sweep-implied value is discarded (rung stays un-hit, retried).
        expected = basis_part * (1.0 + pnl_pct)
        if expected > 0 and not (expected / 3 <= trade.exit_sol <= expected * 3):
            log.warning(
                "discarding suspect partial fill for {} — exit {:.4f} SOL vs sweep-implied "
                "{:.4f} SOL (junk-quote guard)",
                position.symbol or position.mint[:8],
                trade.exit_sol,
                expected,
            )
            return
        position.tokens_atomic -= tokens_part
        position.size_sol = max(0.0, position.size_sol - basis_part)
        rung = engine.mark_rung(position.mint, pnl_pct, basis_part)
        # Retarget the remainder at the next ladder rung so the dashboard bar
        # rescales (a +46% position stuck against a "+30% TP" scale reads as
        # a bug) — exits themselves stay governed by the framework ladder.
        next_rung = engine.next_rung(position.mint)
        if next_rung is not None:
            position.take_profit_pct = next_rung
        # Ratchet the stop: the remainder now stops at the configured level
        # ABOVE entry (negative stop_loss_pct = raised stop) instead of
        # riding all the way back to the entry-relative SL.
        new_floor = self._sl_ratchet.get(round(rung, 6))
        if new_floor is not None and -new_floor < position.stop_loss_pct:
            position.stop_loss_pct = -new_floor
        self._closed.append(trade)
        if self._repo is not None:
            await self._repo.save_closed_trade(trade, self._execution_mode)
            await self._repo.save_open(position, self._execution_mode)
            if hasattr(self._repo, "update_partials"):
                await self._repo.update_partials(
                    position.mint, engine.partials_as_dicts(position.mint)
                )
        await self._risk.record_close(trade.pnl_sol)
        log.info(
            "PARTIAL TP {} — rung +{:.0%}: sold {:.0%} ({:+.4f} SOL), {} tokens ride on",
            position.symbol or position.mint[:8],
            rung,
            fraction,
            trade.pnl_sol,
            position.tokens_atomic,
        )
        # Plain text — TelegramNotifier sends without parse_mode, so HTML
        # tags would print literally (user report 2026-07-12).
        next_target = f"+{next_rung:.0%}" if next_rung is not None else "trailing stop"
        stop_line = (
            f"SL moved UP to {-position.stop_loss_pct:+.0%} — profit locked 🔒"
            if position.stop_loss_pct < 0
            else f"SL {-position.stop_loss_pct:+.0%}"
        )
        await self._notifier.notify(
            f"💰 PARTIAL TP {position.symbol or position.mint[:8]} — "
            f"rung +{rung:.0%}: sold {fraction:.0%} for {trade.pnl_sol:+.4f} SOL\n"
            f"Remainder rides on — next target {next_target} | {stop_line}"
        )

    async def _quote_with_status(self, mint: str, atomic: int):
        """Quote with HTTP status when the client supports it (duck-typed so
        test doubles that only implement ``quote()`` keep working; their
        failures read as transient, matching the old behaviour)."""
        fn = getattr(self._jup, "quote_or_status", None)
        if fn is not None:
            return await fn(mint, SOL_MINT, atomic)
        return await self._jup.quote(mint, SOL_MINT, atomic), None

    async def _finalize_close(self, mint: str, position: Position, trade: ClosedTrade) -> None:
        del self._open[mint]
        self._route_fails.pop(mint, None)
        self._start_cooldown(mint)
        engine = self._engine(position)
        if engine is not None:
            engine.forget(mint)
        self._closed.append(trade)
        if self._repo is not None:
            await self._repo.delete_open(mint)
            await self._repo.save_closed_trade(trade, self._execution_mode)
        await self._risk.record_close(trade.pnl_sol)
        from zetryn_bot.notify.format import format_close

        held_s = self._now() - position.opened_at
        await self._notifier.notify(format_close(position, trade, held_s))

    async def check_once(self) -> None:
        """One sweep over open positions: quote, evaluate exits, close if triggered.

        Quote failures are split by cause (learned from 5 positions stuck open
        for 11h on the first VPS deploy):

        - transient (429 / 5xx / network): never force-close — the price will
          be back; the max-hold exit fires on the first successful quote.
        - permanent (no route, HTTP 4xx): after ``dead_route_after``
          consecutive failures on a position past its max hold, close it at
          0 SOL (``dead_route``). Honest accounting: if Jupiter cannot route
          the sell, a live position could not exit either — that token is a
          total loss, not an immortal open position.
        """
        for mint, position in list(self._open.items()):
            age_expired = (self._now() - position.opened_at) >= position.max_hold_s
            q, status = await self._quote_with_status(mint, position.tokens_atomic)
            current_lamports = q.out_amount if q is not None else 0

            # Curve-phase tokens (fresh pump.fun launches) aren't routable on
            # Jupiter yet — price them from the bonding curve instead so TP/SL
            # and mark-to-market work from the first sweep.
            if current_lamports <= 0 and self._curve is not None:
                curve_out = await self._curve.sell_quote(mint, position.tokens_atomic)
                if curve_out:
                    current_lamports = curve_out

            if current_lamports <= 0:
                transient = status is None or status == 429 or status >= 500
                if transient:
                    continue  # price will come back; retry next sweep
                fails = self._route_fails.get(mint, 0) + 1
                self._route_fails[mint] = fails
                if fails == 1 or fails == self._dead_route_after:
                    log.warning(
                        "no sell route for {} (HTTP {}, {} consecutive) — {}",
                        position.symbol or mint[:8],
                        status,
                        fails,
                        "closing as dead_route" if fails >= self._dead_route_after else "watching",
                    )
                if age_expired and fails >= self._dead_route_after:
                    trade = ClosedTrade(
                        position=position,
                        exit_sol=0.0,
                        pnl_sol=-position.size_sol,
                        reason="dead_route",
                    )
                    await self._finalize_close(mint, position, trade)
                continue

            self._route_fails.pop(mint, None)
            current_sol = lamports_to_sol(current_lamports)
            pnl_pct = (
                (current_sol - position.size_sol) / position.size_sol if position.size_sol else 0.0
            )

            # Junk-quote guard: an out-of-nowhere extreme quote must repeat on
            # the NEXT sweep before it can move money or the mark.
            multiple = 1.0 + pnl_pct
            if multiple >= self._extreme_x:
                pending = self._extreme_pending.get(mint)
                now = self._now()
                if pending is None or now - pending[0] > 300 or multiple < pending[1] / 2:
                    self._extreme_pending[mint] = (now, multiple)
                    log.warning(
                        "extreme quote for {} ({:+.0%}) — holding fire until the next sweep "
                        "confirms it (junk-quote guard)",
                        position.symbol or mint[:8],
                        pnl_pct,
                    )
                    continue
                # second consecutive extreme sweep in agreement — treat as real
                self._extreme_pending.pop(mint, None)
            else:
                self._extreme_pending.pop(mint, None)

            await self._mark(mint, pnl_pct)

            # Ratcheted stop (raised above entry after a TP rung) fires before
            # the lifecycle agent — its hard SL is the entry-relative envelope
            # and knows nothing about the profit lock.
            if position.stop_loss_pct < 0 and pnl_pct <= -position.stop_loss_pct:
                log.info(
                    "ratchet stop {} — pnl {:+.1%} <= locked floor {:+.1%}",
                    position.symbol or mint[:8],
                    pnl_pct,
                    -position.stop_loss_pct,
                )
                trade = await self._executor.sell(position, "ratchet_stop")
                if trade is not None:
                    await self._finalize_close(mint, position, trade)
                continue

            engine = self._engine(position)
            if engine is None:
                reason = self._exit_reason(position, current_sol)
            else:
                decision = await engine.evaluate(
                    position, current_sol, self._now() - position.opened_at
                )
                if decision is None or decision.action == "hold":
                    continue
                # Non-final ladder rungs sell a fraction and keep riding.
                if decision.action in ("take_profit", "scale_out") and decision.size:
                    await self._partial_close(position, decision, pnl_pct, engine)
                    continue
                reason = engine.close_reason(decision)
                log.info(
                    "lifecycle exit {} — action={} reason={} | {}",
                    position.symbol or mint[:8],
                    decision.action,
                    reason,
                    "; ".join(decision.reasons[:2]),
                )
            if reason is None:
                continue
            trade = await self._executor.sell(position, reason)
            if trade is None:
                continue  # sell failed — keep the position open, retry next sweep
            if current_sol > 0 and not (current_sol / 3 <= trade.exit_sol <= current_sol * 3):
                log.warning(
                    "discarding suspect close fill for {} — exit {:.4f} SOL vs sweep-implied "
                    "{:.4f} SOL (junk-quote guard)",
                    position.symbol or mint[:8],
                    trade.exit_sol,
                    current_sol,
                )
                continue
            await self._finalize_close(mint, position, trade)

    async def monitor_loop(self) -> None:
        """Supervised task: sweep exits every ``poll_interval_s``; log stats periodically."""
        last_stats = self._now()
        while True:
            await self.check_once()
            if (self._now() - last_stats) >= self._stats_interval_s:
                s = self.stats()
                log.info(
                    "positions — open={} closed={} win_rate={:.0%} pnl={:+.4f} SOL",
                    s["open"],
                    s["closed"],
                    s["win_rate"],
                    s["total_pnl_sol"],
                )
                last_stats = self._now()
            await asyncio.sleep(self._poll_interval_s)

    def stats(self) -> dict:
        wins = [t for t in self._closed if t.pnl_sol > 0]
        return {
            "open": len(self._open),
            "closed": len(self._closed),
            "win_rate": len(wins) / len(self._closed) if self._closed else 0.0,
            "total_pnl_sol": sum(t.pnl_sol for t in self._closed),
        }

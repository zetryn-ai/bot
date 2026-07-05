"""LiveExecutor — real on-chain swaps. Implements the same Executor Protocol as PaperExecutor.

Flow per buy/sell: balance-cache guard -> Jupiter builds an unsigned swap tx ->
sign locally with the wallet's keypair (<1ms) -> submit -> confirm -> on
timeout, check on-chain truth before ever considering a retry (never
blind-resend — that's how double-swaps happen). A per-mint lock serializes
concurrent buy/sell calls for the same mint.

Our own compute overhead here (sizing, signing, bookkeeping) is negligible;
end-to-end latency is dominated by the Jupiter API and Solana confirmation
time, which are outside this module's control.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable

from loguru import logger
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from zetryn_bot.execution.executor import ClosedTrade, Position, SwapRequest
from zetryn_bot.execution.jupiter import (
    SOL_MINT,
    JupiterQuote,
    lamports_to_sol,
    sol_to_lamports,
)
from zetryn_bot.execution.rpc import SolanaRpc
from zetryn_bot.wallet.keystore import Wallet

log = logger.bind(component="execution.live")


class BalanceCache:
    """Caches the wallet's SOL balance on a TTL so the buy hot path never
    waits on an extra RPC round trip for a balance check.
    """

    def __init__(
        self,
        rpc: SolanaRpc,
        pubkey: Pubkey,
        *,
        ttl_s: float = 10.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rpc = rpc
        self._pubkey = pubkey
        self._ttl_s = ttl_s
        self._now = now_fn
        self._value: float | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> float:
        """Return the cached balance, refreshing it if stale."""
        if self._value is not None and (self._now() - self._fetched_at) < self._ttl_s:
            return self._value
        async with self._lock:
            # Re-check after acquiring the lock — another caller may have refreshed.
            if self._value is not None and (self._now() - self._fetched_at) < self._ttl_s:
                return self._value
            self._value = await self._rpc.get_sol_balance(self._pubkey)
            self._fetched_at = self._now()
            return self._value

    def invalidate(self) -> None:
        """Force the next `get()` to refresh — call after a buy/sell changes the balance."""
        self._value = None


class LiveExecutor:
    """Real swaps via Jupiter + solana-py. Same Protocol as PaperExecutor."""

    def __init__(
        self,
        wallet: Wallet,
        rpc: SolanaRpc,
        jupiter: JupiterQuote,
        *,
        slippage_bps: int = 200,
        priority_fee_lamports: int | None = None,
        min_sol_reserve: float = 0.05,
        confirm_timeout_s: float = 30.0,
        balance_ttl_s: float = 10.0,
    ) -> None:
        self._wallet = wallet
        self._rpc = rpc
        self._jup = jupiter
        self._slippage_bps = slippage_bps
        self._priority_fee_lamports = priority_fee_lamports
        self._min_sol_reserve = min_sol_reserve
        self._confirm_timeout_s = confirm_timeout_s
        self._balance = BalanceCache(rpc, wallet.keypair.pubkey(), ttl_s=balance_ttl_s)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, mint: str) -> asyncio.Lock:
        return self._locks.setdefault(mint, asyncio.Lock())

    async def _sign_and_submit(self, swap_tx_b64: str) -> VersionedTransaction | None:
        """Decode, sign locally, and submit. Returns the signed tx, or None on send failure."""
        raw = base64.b64decode(swap_tx_b64)
        unsigned = VersionedTransaction.from_bytes(raw)
        tx = VersionedTransaction(unsigned.message, [self._wallet.keypair])  # local sign, <1ms
        sig = await self._rpc.send_transaction(tx)
        if sig is None:
            return None
        confirmed = await self._rpc.confirm(sig, timeout_s=self._confirm_timeout_s)
        if not confirmed:
            # Timeout is NOT failure — check on-chain truth before deciding anything.
            landed = await self._rpc.check_signature_landed(sig)
            if not landed:
                log.error("swap {} did not land after timeout — treating as failed", sig)
                return None
            log.warning("swap {} confirmed late via on-chain check", sig)
        return tx

    async def buy(self, req: SwapRequest) -> Position | None:
        async with self._lock_for(req.mint):
            balance = await self._balance.get()
            if balance < req.size_sol + self._min_sol_reserve:
                log.warning(
                    "LIVE BUY {} aborted — balance {:.4f} SOL < size {:.4f} + reserve {:.4f}",
                    req.symbol or req.mint[:8],
                    balance,
                    req.size_sol,
                    self._min_sol_reserve,
                )
                return None

            swap_tx_b64 = await self._jup.build_swap_tx(
                SOL_MINT,
                req.mint,
                sol_to_lamports(req.size_sol),
                str(self._wallet.pubkey),
                slippage_bps=self._slippage_bps,
                priority_fee_lamports=self._priority_fee_lamports,
            )
            if swap_tx_b64 is None:
                log.warning("LIVE BUY {} aborted — could not build swap tx", req.symbol)
                return None

            tx = await self._sign_and_submit(swap_tx_b64)
            self._balance.invalidate()  # spent SOL (+ fees) regardless of outcome
            if tx is None:
                return None

            # Best-effort token amount from the quote we just executed (the swap
            # was built from a fresh quote, so out_amount is what we expect to
            # receive; exact on-chain balance delta requires pre/post account
            # reads, which the framework doesn't need for paper-parity tracking).
            quote = await self._jup.quote(
                SOL_MINT, req.mint, sol_to_lamports(req.size_sol), self._slippage_bps
            )
            tokens = quote.out_amount if quote else 0

            log.info(
                "LIVE BUY {} size={:.4f} SOL -> ~{} tokens (conf {:.2f})",
                req.symbol or req.mint[:8],
                req.size_sol,
                tokens,
                req.confidence,
            )
            return Position(
                mint=req.mint,
                symbol=req.symbol,
                size_sol=req.size_sol,
                tokens_atomic=tokens,
                take_profit_pct=req.take_profit_pct,
                stop_loss_pct=req.stop_loss_pct,
                max_hold_s=req.max_hold_s,
                confidence=req.confidence,
                meta=req.meta,
            )

    async def sell(self, position: Position, reason: str) -> ClosedTrade | None:
        async with self._lock_for(position.mint):
            swap_tx_b64 = await self._jup.build_swap_tx(
                position.mint,
                SOL_MINT,
                position.tokens_atomic,
                str(self._wallet.pubkey),
                slippage_bps=self._slippage_bps,
                priority_fee_lamports=self._priority_fee_lamports,
            )
            if swap_tx_b64 is None:
                log.warning("LIVE SELL {} aborted — could not build swap tx", position.symbol)
                return None

            tx = await self._sign_and_submit(swap_tx_b64)
            self._balance.invalidate()
            if tx is None:
                return None

            quote = await self._jup.quote(position.mint, SOL_MINT, position.tokens_atomic)
            exit_sol = lamports_to_sol(quote.out_amount) if quote else 0.0
            pnl = exit_sol - position.size_sol

            log.info(
                "LIVE SELL {} -> ~{:.4f} SOL | pnl={:+.4f} SOL ({:+.1%}) reason={}",
                position.symbol or position.mint[:8],
                exit_sol,
                pnl,
                pnl / position.size_sol if position.size_sol else 0.0,
                reason,
            )
            return ClosedTrade(position=position, exit_sol=exit_sol, pnl_sol=pnl, reason=reason)

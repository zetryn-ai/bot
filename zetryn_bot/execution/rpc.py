"""Thin Solana RPC wrapper — submit, confirm, and verify on-chain truth.

Only the operations ``LiveExecutor`` needs: send a signed transaction, poll for
confirmation, and — critically — check whether a transaction actually landed
before ever considering a retry. Never blind-resends: a timed-out confirmation
does not mean the swap failed, so the caller must check on-chain truth first.
"""

from __future__ import annotations

from loguru import logger
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

log = logger.bind(component="execution.rpc")


class SolanaRpc:
    """Wraps one ``solana-py`` AsyncClient for the calls LiveExecutor needs."""

    def __init__(self, rpc_url: str) -> None:
        self._client = AsyncClient(rpc_url, commitment=Confirmed)

    async def close(self) -> None:
        await self._client.close()

    async def send_transaction(self, tx: VersionedTransaction) -> Signature | None:
        """Submit an already-signed transaction. Returns ``None`` on failure."""
        try:
            resp = await self._client.send_raw_transaction(bytes(tx))
            return resp.value
        except Exception as exc:
            log.warning("send_raw_transaction failed: {}", exc)
            return None

    async def confirm(self, sig: Signature, *, timeout_s: float = 30.0) -> bool:
        """Poll until ``sig`` is confirmed or ``timeout_s`` elapses."""
        try:
            resp = await self._client.confirm_transaction(sig, commitment=Confirmed)
        except Exception as exc:
            log.debug("confirm_transaction error for {}: {}", sig, exc)
            return False
        statuses = resp.value
        if not statuses:
            return False
        status = statuses[0]
        return status is not None and status.err is None

    async def check_signature_landed(self, sig: Signature) -> bool:
        """Source of truth after a confirm timeout — never retry without this.

        Returns True only if the signature is on-chain with no error. This is
        the check that prevents a double-swap: a confirmation timeout does NOT
        mean the transaction failed, only that we stopped waiting.
        """
        try:
            resp = await self._client.get_signature_statuses([sig], search_transaction_history=True)
        except Exception as exc:
            log.warning("check_signature_landed error for {}: {}", sig, exc)
            return False
        statuses = resp.value
        if not statuses or statuses[0] is None:
            return False
        return statuses[0].err is None

    async def get_sol_balance(self, pubkey: Pubkey) -> float:
        """Current SOL balance, in SOL (not lamports)."""
        resp = await self._client.get_balance(pubkey, commitment=Confirmed)
        return resp.value / 1_000_000_000

    async def get_token_balance(self, token_account: Pubkey) -> int | None:
        """Current balance of an SPL token account, in atomic units. None if the account doesn't exist."""
        try:
            resp = await self._client.get_token_account_balance(token_account, commitment=Confirmed)
        except Exception:
            return None
        return int(resp.value.amount)

#!/usr/bin/env python3
"""M5 smoke test — wallet decrypt + LiveExecutor sign/guard flow, offline.

Run from the repo root::

    python scripts/m5_smoke.py

Fully offline: creates a throwaway encrypted keyfile, decrypts it, and drives
`LiveExecutor.buy()` against fully mocked RPC + Jupiter. Verifies the
decrypt -> sign -> submit -> confirm flow without ever sending a real
transaction or touching real funds.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from zetryn_bot.execution.executor import SwapRequest
from zetryn_bot.execution.jupiter import Quote
from zetryn_bot.execution.live import LiveExecutor
from zetryn_bot.wallet.keystore import Wallet, encrypt_private_key


class _MockJupiter:
    def __init__(self, out_amount: int) -> None:
        self.out_amount = out_amount

    async def build_swap_tx(self, input_mint, output_mint, amount_atomic, user_pubkey, **kw):
        payer = Pubkey.from_string(user_pubkey)
        msg = MessageV0.try_compile(payer, [], [], Hash.default())
        n = msg.header.num_required_signatures
        unsigned = VersionedTransaction.populate(msg, [Signature.default()] * n)
        return base64.b64encode(bytes(unsigned)).decode()

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps=100):
        return Quote(in_amount=amount_atomic, out_amount=self.out_amount, price_impact_pct=0.0)


class _MockRpc:
    def __init__(self, balance: float) -> None:
        self.balance = balance
        self.sent: list[VersionedTransaction] = []

    async def send_transaction(self, tx):
        self.sent.append(tx)
        return Signature.default()

    async def confirm(self, sig, *, timeout_s=30.0):
        return True

    async def check_signature_landed(self, sig):
        return True

    async def get_sol_balance(self, pubkey):
        return self.balance


async def check() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        keyfile = Path(tmp) / "wallet.enc"
        real_kp = Keypair()
        keyfile.write_bytes(encrypt_private_key(str(real_kp), "smoke-test-passphrase"))

        # 1) Decrypt round-trips to the same keypair.
        wallet = Wallet.load(str(keyfile), "smoke-test-passphrase")
        if wallet.pubkey != str(real_kp.pubkey()):
            failures.append("decrypted wallet pubkey does not match original")
        print(f"wallet loaded: {wallet.pubkey}")

        # 2) Wrong passphrase must fail cleanly.
        try:
            Wallet.load(str(keyfile), "wrong-passphrase")
            failures.append("wrong passphrase did not raise")
        except Exception:
            print("wrong passphrase correctly rejected")

        # 3) LiveExecutor buy: mocked RPC/Jupiter, no real network/funds.
        jupiter = _MockJupiter(out_amount=1_000_000)
        rpc = _MockRpc(balance=1.0)
        executor = LiveExecutor(wallet, rpc, jupiter, min_sol_reserve=0.05)

        req = SwapRequest(
            mint="MintSmoke",
            symbol="SMOKE",
            size_sol=0.1,
            take_profit_pct=0.3,
            stop_loss_pct=0.15,
            max_hold_s=1800,
            confidence=0.8,
        )
        position = await executor.buy(req)
        if position is None:
            failures.append("LiveExecutor.buy returned None")
        elif len(rpc.sent) != 1:
            failures.append(f"expected 1 tx sent, got {len(rpc.sent)}")
        else:
            print(
                f"buy signed + sent: tokens={position.tokens_atomic} sig={rpc.sent[0].signatures[0]}"
            )

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — M5 smoke test passed. No real transaction was ever sent.")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    sys.exit(main())

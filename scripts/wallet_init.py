#!/usr/bin/env python3
"""One-time interactive wallet setup — creates the encrypted keyfile.

Run this ONCE, interactively::

    python scripts/wallet_init.py

Prompts (hidden input via ``getpass`` — never echoed, never logged) for your
Solana wallet's base58 private key and a passphrase to encrypt it with. Writes
the result to ``WALLET_KEYFILE_PATH`` (default ``wallet.enc``). Neither secret
is ever written to disk unencrypted, printed, or logged — only the resulting
public key is shown.

Keep the passphrase somewhere separate from ``wallet.enc`` (e.g. a password
manager) and set it as ``WALLET_PASSPHRASE`` in ``.env`` for the runtime to
decrypt the keyfile at startup. ``wallet.enc`` is gitignored — never commit it.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from solders.keypair import Keypair

from zetryn_bot.config import Settings
from zetryn_bot.wallet.keystore import encrypt_private_key


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    settings = Settings()
    keyfile_path = settings.wallet_keyfile_path

    if Path(keyfile_path).exists():
        print(
            f"ERROR — {keyfile_path} already exists. Remove it first if you intend to replace it."
        )
        return 1

    print(f"Wallet keyfile will be written to: {keyfile_path}\n")
    private_key_b58 = getpass.getpass("Paste your base58 private key (input hidden): ").strip()
    if not private_key_b58:
        print("ERROR — no key entered.")
        return 1

    try:
        pubkey = Keypair.from_base58_string(private_key_b58).pubkey()
    except Exception as exc:
        print(f"ERROR — that doesn't look like a valid base58 private key: {exc}")
        return 1

    passphrase = getpass.getpass("Choose a passphrase to encrypt it: ")
    confirm = getpass.getpass("Confirm passphrase: ")
    if not passphrase:
        print("ERROR — passphrase cannot be empty.")
        return 1
    if passphrase != confirm:
        print("ERROR — passphrases did not match.")
        return 1

    encrypted = encrypt_private_key(private_key_b58, passphrase)
    Path(keyfile_path).write_bytes(encrypted)
    Path(keyfile_path).chmod(0o600)

    print(f"\nWallet saved -> {keyfile_path} (chmod 600)")
    print(f"Public key: {pubkey}")
    print(
        "\nSet WALLET_PASSPHRASE in your .env to the passphrase you just chose "
        "(keep it somewhere separate from the keyfile itself)."
    )
    print("Fund this address before running with EXECUTION_MODE=live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Wallet management (M5) — encrypted keypair for live execution.

The private key lives encrypted at rest (``wallet.enc``, Fernet) and is
decrypted into memory once at startup using a passphrase supplied via
``WALLET_PASSPHRASE`` (never a default, never logged). See
:mod:`zetryn_bot.wallet.keystore` and ``scripts/wallet_init.py``.
"""

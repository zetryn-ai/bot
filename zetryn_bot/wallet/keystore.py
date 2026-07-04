"""Encrypted keyfile loading — the only place the private key touches memory.

``wallet.enc`` holds a Fernet-encrypted base58 private key. It is decrypted
once at startup using ``WALLET_PASSPHRASE`` (env only — never defaulted, never
logged) into an in-memory ``solders.Keypair``. Nothing in this module ever
writes the private key to a log, exception message, or ``repr``/``str``
output — only the public key is ever surfaced.

The keyfile is created once, interactively, by ``scripts/wallet_init.py`` —
this module only reads it.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from solders.keypair import Keypair

# PBKDF2 iteration count for deriving the Fernet key from the passphrase.
# Higher = slower brute force, slower startup. 480_000 matches current
# OWASP guidance for PBKDF2-HMAC-SHA256 (2023+).
_PBKDF2_ITERATIONS = 480_000
# Fixed salt is acceptable here: the "password" (WALLET_PASSPHRASE) is a
# high-entropy secret chosen once per deployment, not a user-memorized
# password reused across sites — the threat this salt defends against.
_SALT = b"zetryn-bot-wallet-keystore-v1"


class WalletError(Exception):
    """Raised on keyfile/passphrase problems. Never includes secret material."""


def _derive_fernet_key(passphrase: str) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), _SALT, _PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(raw)


class Wallet:
    """Wraps a decrypted ``solders.Keypair``. Never exposes the private key."""

    def __init__(self, keypair: Keypair) -> None:
        self._keypair = keypair

    def __repr__(self) -> str:
        return f"Wallet(pubkey={self.pubkey})"

    __str__ = __repr__

    @property
    def pubkey(self) -> str:
        return str(self._keypair.pubkey())

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    @classmethod
    def load(cls, path: str, passphrase: str) -> Wallet:
        """Decrypt ``path`` with ``passphrase`` into a :class:`Wallet`.

        Raises :class:`WalletError` on a missing file, empty passphrase, wrong
        passphrase, or corrupt data — the message never includes key material.
        """
        if not passphrase:
            raise WalletError("WALLET_PASSPHRASE is empty")
        keyfile = Path(path)
        if not keyfile.is_file():
            raise WalletError(f"wallet keyfile not found: {path}")

        encrypted = keyfile.read_bytes()
        try:
            decrypted = Fernet(_derive_fernet_key(passphrase)).decrypt(encrypted)
        except InvalidToken as exc:
            raise WalletError("wrong passphrase or corrupt wallet keyfile") from exc

        try:
            keypair = Keypair.from_base58_string(decrypted.decode("utf-8"))
        except Exception as exc:
            raise WalletError("decrypted wallet data is not a valid keypair") from exc
        return cls(keypair)


def encrypt_private_key(private_key_b58: str, passphrase: str) -> bytes:
    """Encrypt a base58 private key for writing to a keyfile.

    Used only by ``scripts/wallet_init.py`` — the one-time interactive setup
    path. Validates the key parses before encrypting so a typo is caught
    immediately rather than producing an unusable keyfile.
    """
    Keypair.from_base58_string(private_key_b58)  # validate; raises if malformed
    return Fernet(_derive_fernet_key(passphrase)).encrypt(private_key_b58.encode("utf-8"))

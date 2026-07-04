"""Unit tests for the wallet keystore — round trip, wrong passphrase, no leaks."""

from __future__ import annotations

from pathlib import Path

import pytest
from solders.keypair import Keypair

from zetryn_bot.wallet.keystore import Wallet, WalletError, encrypt_private_key


def _write_keyfile(tmp_path: Path, private_key_b58: str, passphrase: str) -> Path:
    path = tmp_path / "wallet.enc"
    path.write_bytes(encrypt_private_key(private_key_b58, passphrase))
    return path


def test_round_trip_decrypts_to_the_same_keypair(tmp_path):
    kp = Keypair()
    b58 = str(kp)
    path = _write_keyfile(tmp_path, b58, "correct horse battery staple")

    wallet = Wallet.load(str(path), "correct horse battery staple")
    assert wallet.pubkey == str(kp.pubkey())


def test_wrong_passphrase_raises_wallet_error(tmp_path):
    path = _write_keyfile(tmp_path, str(Keypair()), "right-passphrase")
    with pytest.raises(WalletError):
        Wallet.load(str(path), "wrong-passphrase")


def test_missing_file_raises_wallet_error(tmp_path):
    with pytest.raises(WalletError):
        Wallet.load(str(tmp_path / "does-not-exist.enc"), "whatever")


def test_empty_passphrase_raises_wallet_error(tmp_path):
    path = _write_keyfile(tmp_path, str(Keypair()), "some-passphrase")
    with pytest.raises(WalletError):
        Wallet.load(str(path), "")


def test_malformed_private_key_is_rejected_at_encrypt_time():
    with pytest.raises(Exception):  # noqa: B017 - solders raises its own error type
        encrypt_private_key("not-a-valid-base58-keypair", "pass")


def test_repr_and_str_never_expose_the_private_key(tmp_path):
    kp = Keypair()
    b58 = str(kp)
    path = _write_keyfile(tmp_path, b58, "shh")
    wallet = Wallet.load(str(path), "shh")

    assert b58 not in repr(wallet)
    assert b58 not in str(wallet)
    assert wallet.pubkey in repr(wallet)

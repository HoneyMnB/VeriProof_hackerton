"""Tests for settings-backed Solana signer configuration."""
from __future__ import annotations


def test_parse_secret_key_accepts_solana_cli_array():
    from services.solana_adapter_factory import _parse_secret_key

    assert _parse_secret_key("[1, 2, 3]") == [1, 2, 3]


def test_parse_secret_key_accepts_base58_keypair():
    from solders.keypair import Keypair

    from services.solana_adapter_factory import _parse_secret_key

    keypair = Keypair()

    assert _parse_secret_key(str(keypair)) == list(bytes(keypair))


def test_parse_secret_key_rejects_invalid_base58_keypair():
    import pytest

    from services.solana_adapter_factory import _parse_secret_key

    with pytest.raises(ValueError, match="valid 64-byte Solana Base58 keypair"):
        _parse_secret_key("not-a-keypair")

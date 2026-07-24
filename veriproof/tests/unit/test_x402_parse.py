"""SPEC-004 unit tests — X402Service.parse_payment_submitted (architecture 4, 6.3).

Parses the a2a-x402 ``payment-submitted`` / settle body into SubmittedPayment.
Defensive: required fields (tx_signature, buyer_wallet) missing ->
InvalidPaymentSubmitted. Optional amount is parsed into a Decimal.
"""
from __future__ import annotations

import decimal

import pytest

_TX_SIG = "2bob9x4XTa5ZJw7m5zKqX3VvdVs9QqF7g6nU8hYdWcN4rTbPx1mKqJfHlMs2vDc7Ea"
_BUYER = "BuyerWallet1111111111111111111111111111111111"


def test_parse_payment_submitted_basic_fields():
    """tx_signature + buyer_wallet parsed into SubmittedPayment."""
    from services.x402_service import X402Service

    payload = {"tx_signature": _TX_SIG, "buyer_wallet": _BUYER}
    result = X402Service().parse_payment_submitted(payload)

    assert result.tx_signature == _TX_SIG
    assert result.buyer_wallet == _BUYER


def test_parse_payment_submitted_amount_decimal():
    """amount_usdc parsed into a Decimal (string or number)."""
    from services.x402_service import X402Service

    result = X402Service().parse_payment_submitted(
        {"tx_signature": _TX_SIG, "buyer_wallet": _BUYER, "amount_usdc": "1.50"}
    )
    assert result.amount_usdc == decimal.Decimal("1.50")


def test_parse_payment_submitted_amount_numeric():
    """amount_usdc as a JSON number is accepted and coerced to Decimal."""
    from services.x402_service import X402Service

    result = X402Service().parse_payment_submitted(
        {"tx_signature": _TX_SIG, "buyer_wallet": _BUYER, "amount_usdc": 2.5}
    )
    assert result.amount_usdc == decimal.Decimal("2.5")


def test_parse_payment_submitted_extra_fields_preserved():
    """Unknown fields (session_id, asset_id, network) survive in ``extra``."""
    from services.x402_service import X402Service

    result = X402Service().parse_payment_submitted(
        {
            "tx_signature": _TX_SIG,
            "buyer_wallet": _BUYER,
            "session_id": "sess-1",
            "asset_id": "asset-1",
        }
    )
    assert result.extra["session_id"] == "sess-1"
    assert result.extra["asset_id"] == "asset-1"


def test_parse_payment_submitted_missing_tx_signature_raises():
    """Missing tx_signature -> InvalidPaymentSubmitted."""
    from services.x402_service import InvalidPaymentSubmitted, X402Service

    with pytest.raises(InvalidPaymentSubmitted):
        X402Service().parse_payment_submitted({"buyer_wallet": _BUYER})


def test_parse_payment_submitted_missing_buyer_wallet_raises():
    """Missing buyer_wallet -> InvalidPaymentSubmitted."""
    from services.x402_service import InvalidPaymentSubmitted, X402Service

    with pytest.raises(InvalidPaymentSubmitted):
        X402Service().parse_payment_submitted({"tx_signature": _TX_SIG})


def test_parse_payment_submitted_non_dict_raises():
    """Non-object payload -> InvalidPaymentSubmitted."""
    from services.x402_service import InvalidPaymentSubmitted, X402Service

    with pytest.raises(InvalidPaymentSubmitted):
        X402Service().parse_payment_submitted("not-an-object")  # type: ignore[arg-type]


def test_parse_payment_submitted_non_numeric_amount_raises():
    """amount_usdc present but non-numeric -> InvalidPaymentSubmitted."""
    from services.x402_service import InvalidPaymentSubmitted, X402Service

    with pytest.raises(InvalidPaymentSubmitted):
        X402Service().parse_payment_submitted(
            {"tx_signature": _TX_SIG, "buyer_wallet": _BUYER, "amount_usdc": "abc"}
        )

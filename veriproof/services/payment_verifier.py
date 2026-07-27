"""결제 검증 어댑터 — 실제 Solana 검증 경계."""
from __future__ import annotations

import decimal
from typing import Protocol

from ._types import PaymentVerification
from .solana_adapter_factory import get_solana_service

class PaymentVerifier(Protocol):
    """정산 파이프라인이 요구하는 최소 결제 검증 계약."""

    def verify_usdc_payment(
        self,
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification:
        """USDC 결제 트랜잭션을 검증하고 수신자·금액·커밋먼트를 반환하는 계약."""
        ...


def get_payment_verifier() -> PaymentVerifier:
    """Return the real Solana payment verifier.

    Offline tests must inject fakes at the consuming service boundary. Runtime
    code never accepts fabricated ``mock:`` transaction identifiers.
    """
    return get_solana_service()

"""로컬 결제 목업은 명시적 테스트 거래만 승인한다."""
from __future__ import annotations

import decimal

from services.payment_verifier import LocalMockPaymentVerifier


def test_local_mock_payment_verifier_accepts_only_explicit_mock_transaction():
    verifier = LocalMockPaymentVerifier()
    accepted = verifier.verify_usdc_payment(
        "mock:local-a2a-001", "recipient", decimal.Decimal("1.25"), "mint"
    )
    rejected = verifier.verify_usdc_payment(
        "real-looking-signature", "recipient", decimal.Decimal("1.25"), "mint"
    )
    assert accepted.is_valid is True
    assert accepted.amount == decimal.Decimal("1.25")
    assert rejected.is_valid is False

"""결제 검증 어댑터 — 로컬 목업과 실제 Solana 검증의 교체 지점."""
from __future__ import annotations

import decimal
import logging
from typing import Protocol

from ._types import PaymentVerification
from .solana_service import get_solana_service

logger = logging.getLogger(__name__)


class PaymentVerifier(Protocol):
    """정산 파이프라인이 요구하는 최소 결제 검증 계약."""

    def verify_usdc_payment(
        self,
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification: ...


class LocalMockPaymentVerifier:
    """로컬 데모 전용 결제 검증기.

    ``mock:`` 접두사가 있는 테스트 거래만 승인한다. 실제 체인 거래처럼
    보이게 만들지 않으며, 배포 환경에서는 ``PAYMENT_VERIFIER=solana``로
    전환해야 한다.
    """

    def verify_usdc_payment(
        self,
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification:
        if not tx_sig.startswith("mock:"):
            logger.warning("local mock payment rejected tx_sig=%s", tx_sig)
            return PaymentVerification(
                is_valid=False,
                amount=decimal.Decimal("0"),
                sender="",
                slot=0,
                commitment=None,
            )
        logger.info("local mock payment accepted tx_sig=%s", tx_sig)
        return PaymentVerification(
            is_valid=True,
            amount=expected_amount,
            sender="local-mock",
            slot=0,
            commitment="mock",
        )


def get_payment_verifier() -> PaymentVerifier:
    """설정에 따라 로컬 목업 또는 실제 Solana 검증기를 선택한다."""
    from django.conf import settings

    backend = getattr(settings, "PAYMENT_VERIFIER", "mock").strip().lower()
    if backend == "mock":
        return LocalMockPaymentVerifier()
    if backend == "solana":
        return get_solana_service()
    raise ValueError(f"unsupported PAYMENT_VERIFIER: {backend}")

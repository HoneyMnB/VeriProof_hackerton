"""로컬 데모 전용 Solana 신호 어댑터.

실제 RPC 거래를 제출하지 않는다. 반환값의 ``mock:solana:`` 접두사는 화면과
감사 로그에서 실체인 서명과 구별되며, 운영 전환은 ``SOLANA_ADAPTER=real``로 한다.
"""
from __future__ import annotations

import decimal
import hashlib
import logging
from typing import Any

from ._types import PaymentVerification

logger = logging.getLogger(__name__)


class LocalMockSolanaService:
    """등록 앵커·인증서·로열티 흐름을 위한 명시적 로컬 시뮬레이터."""

    def anchor_hash(self, image_sha256: str, creator_pubkey: str) -> str:
        """콘텐츠 해시를 기반으로 재현 가능한 목업 앵커 식별자를 만든다."""
        if len(image_sha256) != 64 or not creator_pubkey:
            raise ValueError("content hash and creator wallet are required")
        signal = hashlib.sha256(
            f"anchor:{image_sha256}:{creator_pubkey}".encode()
        ).hexdigest()[:32]
        tx_sig = f"mock:solana:anchor:{signal}"
        logger.info("local mock Solana anchor created creator_wallet=%s", creator_pubkey)
        return tx_sig

    def issue_certificate(self, asset_id: Any, buyer_pubkey: str, memo: str) -> str:
        """라이선스 발급 후 사용할 목업 인증서 식별자를 만든다."""
        if not asset_id or not buyer_pubkey or not memo:
            raise ValueError("asset, buyer wallet, and memo are required")
        signal = hashlib.sha256(
            f"certificate:{asset_id}:{buyer_pubkey}:{memo}".encode()
        ).hexdigest()[:32]
        tx_sig = f"mock:solana:certificate:{signal}"
        logger.info("local mock Solana certificate created asset_id=%s", asset_id)
        return tx_sig

    def issue_registration_certificate(
        self, asset_id: Any, creator_pubkey: str, content_sha256: str
    ) -> str:
        """등록 완료를 나타내는 로컬 목업 인증서 신호를 만든다."""
        if not asset_id or not creator_pubkey or len(content_sha256) != 64:
            raise ValueError("asset, creator wallet, and content hash are required")
        signal = hashlib.sha256(
            f"registration-certificate:{asset_id}:{creator_pubkey}:{content_sha256}".encode()
        ).hexdigest()[:32]
        tx_sig = f"mock:solana:registration-certificate:{signal}"
        logger.info(
            "local mock registration certificate created creator_wallet=%s asset_id=%s",
            creator_pubkey,
            asset_id,
        )
        return tx_sig

    def transfer_usdc(self, to_pubkey: str, amount: decimal.Decimal) -> str:
        """로열티 분배 파이프라인용 목업 전송 신호를 만든다."""
        if not to_pubkey or not amount.is_finite() or amount <= 0:
            raise ValueError("recipient and positive amount are required")
        signal = hashlib.sha256(
            f"transfer:{to_pubkey}:{amount}".encode()
        ).hexdigest()[:32]
        tx_sig = f"mock:solana:transfer:{signal}"
        logger.info("local mock Solana transfer created recipient=%s", to_pubkey)
        return tx_sig

    @staticmethod
    def verify_usdc_payment(
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification:
        """직접 주입되는 배치 서비스 호환을 위해 mock 결제만 검증한다."""
        is_valid = tx_sig.startswith("mock:") and bool(expected_recipient) and bool(mint)
        return PaymentVerification(
            is_valid=is_valid,
            amount=expected_amount if is_valid else decimal.Decimal("0"),
            sender="local-mock" if is_valid else "",
            slot=0,
            commitment="mock" if is_valid else None,
        )

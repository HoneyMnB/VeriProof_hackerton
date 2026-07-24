"""로컬 Solana 목업 어댑터의 명시적 신호 계약을 검증한다."""
from __future__ import annotations

from decimal import Decimal

from services.mock_solana_service import LocalMockSolanaService


def test_mock_solana_signals_are_explicit_and_repeatable():
    service = LocalMockSolanaService()

    anchor = service.anchor_hash("a" * 64, "CreatorWallet")
    certificate = service.issue_certificate("asset-1", "BuyerWallet", "memo")
    transfer = service.transfer_usdc("RecipientWallet", Decimal("1.25"))

    assert anchor.startswith("mock:solana:anchor:")
    assert certificate.startswith("mock:solana:certificate:")
    assert transfer.startswith("mock:solana:transfer:")
    assert anchor == service.anchor_hash("a" * 64, "CreatorWallet")

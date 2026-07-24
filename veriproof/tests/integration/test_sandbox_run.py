"""실제 결제 증명을 요구하는 샌드박스 경계 테스트.

런타임에는 Solana·Gemini 목업을 넣지 않는다. 실제 체인 거래가 없는 요청은
정산을 시작하지 못해야 하며, 미지정 자산도 안전하게 거부해야 한다.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.django_db
def test_sandbox_rejects_missing_payment_proof(client):
    """가짜 거래 서명을 생성하지 않고 결제 증명을 요구한다."""
    from tests.factories import IpAssetFactory

    asset = IpAssetFactory()
    response = client.post(
        "/api/v1/sandbox/run",
        data={"asset_id": str(asset.id), "offer_usdc": "1.0", "usage_type": "commercial"},
        content_type="application/json",
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payment_tx_sig"


@pytest.mark.django_db
def test_sandbox_rejects_unknown_asset_before_execution(client):
    """존재하지 않는 자산에는 외부 AI·체인 호출을 하지 않는다."""
    response = client.post(
        "/api/v1/sandbox/run",
        data={
            "asset_id": str(uuid.uuid4()),
            "offer_usdc": "1.0",
            "usage_type": "commercial",
            "payment_tx_sig": "submitted-transaction",
            "buyer_wallet": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        },
        content_type="application/json",
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"

"""SPEC-004 integration — GET /api/v1/ip/{asset_id}/certificate/{cert_id}.

Minimal certificate retrieval: 200 with a payload that EXCLUDES the original
bytes, or 404 when no License matches the (asset_id, certificate_tx_sig) pair.
``cert_id`` is the on-chain certificate Memo tx signature.
"""
from __future__ import annotations

import decimal

import pytest

from tests.conftest import VALID_WALLET

_BUYER = "BuyerWallet1111111111111111111111111111111111"
_CERT_TEMPLATE = "/api/v1/ip/{asset_id}/certificate/{cert_id}"


def _settlement_service():
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from tests.fakes import (
        FakeBigQuery,
        FakeFirestore,
        FakeRoyaltyService,
        FakeSolanaService,
    )

    class _Rec:
        def record(self, type, payload, asset=None, session=None):
            from apps.common.models import AgentEvent

            AgentEvent.objects.create(
                type=type, payload=payload or {}, asset=asset, session=session
            )

    return SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=_Rec()),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=_Rec(),
        usdc_mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU",
    )


@pytest.mark.django_db
def test_certificate_returns_payload_without_original(client, monkeypatch):
    """200 with certificate payload (asset_id, cert_tx, buyer); NO original bytes."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service",
        lambda: _settlement_service(),
    )

    # Settle to issue a certificate.
    r = client.post(
        f"/api/v1/ip/{asset.id}/settle",
        data={"tx_signature": "tx_cert_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers={"X-Agent-Protocol": "x402", "Accept": "application/json"},
    )
    assert r.status_code == 200
    cert_tx = r.json()["certificate_tx"]
    assert cert_tx

    response = client.get(_CERT_TEMPLATE.format(asset_id=asset.id, cert_id=cert_tx))
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == str(asset.id)
    assert body["certificate_tx"] == cert_tx
    assert body["buyer_wallet"] == _BUYER
    # Certificate payload MUST NOT leak the original bytes / download token.
    assert "original" not in body
    assert "download_token" not in body


@pytest.mark.django_db
def test_certificate_unknown_cert_id_404(client):
    """Unknown cert_id (no matching License) -> 404."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    response = client.get(
        _CERT_TEMPLATE.format(asset_id=asset.id, cert_id="nonexistent_sig")
    )
    assert response.status_code == 404

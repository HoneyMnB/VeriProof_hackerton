"""Agent-only sponsor-paid USDC intent boundary tests."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.test import override_settings
from solders.keypair import Keypair

from apps.ip.models import IpAsset, SponsoredPaymentIntent
from tests.factories import CreatorFactory, IpAssetFactory


@pytest.mark.django_db
@override_settings(
    AGENT_SPONSORED_PAYMENT_TOKEN="agent-token",
    PAYMENT_SPONSOR_PUBKEY="Sponsor111111111111111111111111111111111",
)
def test_agent_intent_uses_configured_wallet_without_user_lookup(client, monkeypatch, settings):
    buyer_wallet = str(Keypair().pubkey())
    settings.AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY = buyer_wallet
    creator = CreatorFactory(wallet_address=str(Keypair().pubkey()))
    asset = IpAssetFactory(
        creator=creator,
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
        target_amount="0.250000",
        currency="USDC",
    )

    class FakeSponsoredService:
        def build_transaction(self, **kwargs):
            assert kwargs["buyer_wallet"] == buyer_wallet
            assert kwargs["amount_usdc"] == Decimal("0.250000")
            return SimpleNamespace(serialized_transaction="partial-transaction", sponsor=str(Keypair().pubkey()))

    monkeypatch.setattr("apps.ip.views_api.get_sponsored_payment_service", lambda: FakeSponsoredService())

    response = client.post(
        f"/api/v1/ip/{asset.id}/agent-sponsored-usdc",
        data={"buyer_wallet": str(Keypair().pubkey())},
        content_type="application/json",
        headers={"Authorization": "Bearer agent-token"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["buyer_wallet"] == buyer_wallet
    assert Decimal(body["amount_usdc"]) == Decimal("0.250000")
    intent = SponsoredPaymentIntent.objects.get(id=body["intent_id"])
    assert intent.buyer_user is None
    assert intent.buyer_wallet == buyer_wallet
    assert intent.channel == SponsoredPaymentIntent.AGENT


@pytest.mark.django_db
def test_agent_intent_rejects_missing_or_invalid_bearer_token(client):
    asset = IpAssetFactory(visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED)

    response = client.post(f"/api/v1/ip/{asset.id}/agent-sponsored-usdc")

    assert response.status_code == 401
    assert response.json()["error"] == "agent_authentication_required"

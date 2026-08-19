"""Agent-only sponsor-paid USDC intent boundary tests."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.test.client import RequestFactory
from solders.keypair import Keypair

from apps.ip.models import IpAsset, SponsoredPaymentIntent
from apps.negotiation.models import NegotiationSession
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
@override_settings(
    AGENT_SPONSORED_PAYMENT_TOKEN="agent-token",
    PAYMENT_SPONSOR_PUBKEY="Sponsor111111111111111111111111111111111",
)
def test_agent_intent_uses_accepted_usdc_negotiation_price(monkeypatch, settings):
    from apps.ip.views_api import create_agent_sponsored_usdc_payment

    buyer_wallet = str(Keypair().pubkey())
    settings.AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY = buyer_wallet
    creator = CreatorFactory(wallet_address=str(Keypair().pubkey()))
    asset = IpAssetFactory(
        creator=creator,
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
        target_amount="1.000000",
        currency="USDC",
    )
    session = NegotiationSession.objects.create(
        asset=asset,
        buyer_agent_id="veriproof_buyer_agent",
        usage_type="commercial",
        currency="USDC",
        initial_offer_usdc="0.600000",
        final_price_usdc="0.750000",
        status=NegotiationSession.ACCEPTED,
    )

    class FakeSponsoredService:
        def build_transaction(self, **kwargs):
            assert kwargs["amount_usdc"] == Decimal("0.750000")
            return SimpleNamespace(serialized_transaction="partial-transaction", sponsor=str(Keypair().pubkey()))

    monkeypatch.setattr("apps.ip.views_api.get_sponsored_payment_service", lambda: FakeSponsoredService())

    request = RequestFactory().post(
        f"/api/v1/ip/{asset.id}/agent-sponsored-usdc",
        data={"session_id": str(session.id)},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer agent-token",
    )
    response = create_agent_sponsored_usdc_payment(request, asset.id)

    assert response.status_code == 201
    body = json.loads(response.content)
    assert Decimal(body["amount_usdc"]) == Decimal("0.750000")
    intent = SponsoredPaymentIntent.objects.get(id=body["intent_id"])
    assert intent.negotiation_session_id == session.id


@pytest.mark.django_db
@override_settings(AGENT_SPONSORED_PAYMENT_TOKEN="agent-token")
def test_agent_intent_rejects_non_accepted_usdc_session(settings):
    from apps.ip.views_api import create_agent_sponsored_usdc_payment

    buyer_wallet = str(Keypair().pubkey())
    settings.AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY = buyer_wallet
    asset = IpAssetFactory(visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED, currency="USDC")
    session = NegotiationSession.objects.create(
        asset=asset,
        buyer_agent_id="buyer",
        usage_type="commercial",
        currency="USDC",
        initial_offer_usdc="0.500000",
        status=NegotiationSession.NEGOTIATING,
    )

    request = RequestFactory().post(
        f"/api/v1/ip/{asset.id}/agent-sponsored-usdc",
        data={"session_id": str(session.id)},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer agent-token",
    )
    response = create_agent_sponsored_usdc_payment(request, asset.id)

    assert response.status_code == 409
    assert json.loads(response.content)["error"] == "invalid_negotiation_session"


@pytest.mark.django_db
def test_agent_intent_rejects_missing_or_invalid_bearer_token(client):
    asset = IpAssetFactory(visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED)

    response = client.post(f"/api/v1/ip/{asset.id}/agent-sponsored-usdc")

    assert response.status_code == 401
    assert response.json()["error"] == "agent_authentication_required"

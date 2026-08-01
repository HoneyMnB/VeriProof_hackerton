import decimal

import pytest


def test_download_url_uses_the_seller_https_origin():
    from django.test import RequestFactory

    from apps.ip.views_api import _absolute_download_url

    request = RequestFactory().post(
        "/api/v1/ip/asset/agent-sol-payment/settle",
        HTTP_HOST="seller.example",
        HTTP_X_FORWARDED_PROTO="https",
    )

    assert _absolute_download_url(request, "/files/token") == (
        "https://seller.example/files/token"
    )


def test_download_url_preserves_an_absolute_storage_url():
    from django.test import RequestFactory

    from apps.ip.views_api import _absolute_download_url

    request = RequestFactory().post("/settle", HTTP_HOST="seller.example")

    assert _absolute_download_url(
        request,
        "https://storage.googleapis.com/veriproof/file",
    ) == "https://storage.googleapis.com/veriproof/file"


@pytest.mark.django_db
def test_accepted_sol_negotiation_sets_the_agent_payment_amount(client):
    from apps.ip.models import IpAsset
    from apps.negotiation.models import NegotiationSession
    from tests.factories import IpAssetFactory, NegotiationSessionFactory

    asset = IpAssetFactory(
        visibility=IpAsset.PUBLIC,
        target_price_sol=decimal.Decimal("0.500000000"),
    )
    session = NegotiationSessionFactory(
        asset=asset,
        status=NegotiationSession.ACCEPTED,
        initial_offer_usdc=None,
        final_price_usdc=None,
        initial_offer_sol=decimal.Decimal("0.300000000"),
        final_price_sol=decimal.Decimal("0.350000000"),
    )

    response = client.get(
        f"/api/v1/ip/{asset.id}/agent-sol-payment",
        {"session_id": str(session.id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "SOL"
    assert decimal.Decimal(body["amount_sol"]) == session.final_price_sol
    assert str(session.id) in body["memo"]


@pytest.mark.django_db
def test_sol_payment_terms_reject_a_session_for_another_asset(client):
    from apps.ip.models import IpAsset
    from apps.negotiation.models import NegotiationSession
    from tests.factories import IpAssetFactory, NegotiationSessionFactory

    requested_asset = IpAssetFactory(visibility=IpAsset.PUBLIC)
    other_session = NegotiationSessionFactory(
        status=NegotiationSession.ACCEPTED,
        final_price_sol=decimal.Decimal("0.350000000"),
    )

    response = client.get(
        f"/api/v1/ip/{requested_asset.id}/agent-sol-payment",
        {"session_id": str(other_session.id)},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "invalid_negotiation_session"

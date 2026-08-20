"""에이전트 A 탐색 및 카탈로그 도구 계약 테스트."""

import pytest
from asgiref.sync import async_to_sync
from starlette.testclient import TestClient


def test_agent_card_is_available_at_the_standard_well_known_path():
    from config.asgi import application

    with TestClient(application) as client:
        response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "VeriProof Seller Agent"
    assert "fulfills settled licenses" in payload["description"]
    assert any(skill["id"] == "fulfill-settled-license" for skill in payload["skills"])
    assert payload["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert payload["supportedInterfaces"][0]["url"].endswith("/a2a/")


def test_seller_is_instructed_to_confirm_license_backed_use_for_all_public_assets():
    from agent_a.agent import root_agent

    assert "every public catalog asset is available for use under its purchased" in (
        root_agent.instruction
    )
    assert "do not expose, infer, or override a stored usage classification" in (
        root_agent.instruction
    )


@pytest.mark.django_db
def test_agent_catalog_tool_returns_only_public_registered_assets():
    from agent_a.tools import search_licensable_assets
    from apps.ip.models import IpAsset
    from tests.factories import IpAssetFactory

    visible = IpAssetFactory(
        title="Blue sea photograph",
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
    )
    IpAssetFactory(
        title="Private blue sea photograph",
        visibility=IpAsset.PRIVATE,
        status=IpAsset.ANCHORED,
    )

    result = async_to_sync(search_licensable_assets)("blue sea")

    assert result["count"] == 1
    assert result["assets"][0]["asset_id"] == str(visible.id)
    assert result["assets"][0]["currency"] == "USDC"
    assert "min_price_usdc" in result["assets"][0]
    assert "original_url" not in result["assets"][0]


@pytest.mark.django_db
def test_agent_catalog_tool_filters_prices_only_in_the_requested_currency():
    from agent_a.tools import _search_licensable_assets
    from apps.ip.models import IpAsset
    from tests.factories import IpAssetFactory

    usdc_asset = IpAssetFactory(
        title="USDC sea",
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
        currency="USDC",
        min_amount="8.000000",
    )
    IpAssetFactory(
        title="SOL sea",
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
        currency="SOL",
        min_amount="0.100000000",
    )

    result = _search_licensable_assets(
        query="sea", maximum_price=10, price_currency="USDC"
    )

    assert [asset["asset_id"] for asset in result["assets"]] == [str(usdc_asset.id)]
    assert _search_licensable_assets(query="sea", maximum_price=10)["status"] == (
        "price_currency_required"
    )


def test_agent_catalog_tool_rejects_noncanonical_asset_type():
    from agent_a.tools import _search_licensable_assets

    result = _search_licensable_assets(
        query="바다",
        asset_type="이미지",
    )

    assert result["status"] == "invalid_asset_type"
    assert "image" in result["allowed_asset_types"]
    assert result["assets"] == []


@pytest.mark.django_db
def test_settled_license_fulfillment_returns_only_persisted_delivery_data(settings):
    from agent_a.tools import _get_purchase_fulfillment
    from apps.settlement.models import License
    from tests.factories import IpAssetFactory

    asset = IpAssetFactory()
    license = License.objects.create(
        asset=asset,
        buyer_wallet="buyer-wallet",
        price_usdc="1.250000",
        payment_currency="USDC",
        usage_type="commercial",
        payment_tx_sig="settled-transaction",
        download_token="persisted-download-token",
    )
    settings.A2A_PUBLIC_BASE_URL = "https://seller.test"

    result = _get_purchase_fulfillment(str(asset.id), "settled-transaction")

    assert result["status"] == "fulfilled"
    assert result["delivery"]["license_id"] == str(license.id)
    assert result["delivery"]["download_url"] == "https://seller.test/files/persisted-download-token"
    assert result["delivery"]["network_fee_usdc"] == "0"
    assert _get_purchase_fulfillment(str(asset.id), "other-transaction") == {
        "status": "not_settled"
    }

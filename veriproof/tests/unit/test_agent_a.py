"""에이전트 A 탐색 및 카탈로그 도구 계약 테스트."""

import pytest
from starlette.testclient import TestClient


def test_agent_card_is_available_at_the_standard_well_known_path():
    from config.asgi import application

    with TestClient(application) as client:
        response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "VeriProof Seller Agent"
    assert payload["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert payload["supportedInterfaces"][0]["url"].endswith("/a2a/")


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

    result = search_licensable_assets("blue sea")

    assert result["count"] == 1
    assert result["assets"][0]["asset_id"] == str(visible.id)
    assert "original_url" not in result["assets"][0]

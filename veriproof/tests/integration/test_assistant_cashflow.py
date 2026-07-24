"""창작자 비서·현금흐름 API의 실제 DB 경계 테스트."""
from __future__ import annotations

import pytest

from tests.conftest import VALID_WALLET


@pytest.mark.django_db
def test_creator_expense_updates_assistant_overview(client):
    """지출 기록은 비서 요약의 실제 지출·순액에 반영된다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    created = client.post(
        "/api/v1/assistant/expenses",
        data={"creator_wallet": VALID_WALLET, "memo": "hosting", "amount_usdc": "1.25"},
        content_type="application/json",
    )
    assert created.status_code == 201

    overview = client.get(f"/api/v1/assistant/overview?creator={VALID_WALLET}")
    assert overview.status_code == 200
    assert overview.json()["expense_usdc"] == "1.25"
    assert overview.json()["net_usdc"] == "-1.25"


def test_agent_manifest_points_to_a_real_openapi_route(client):
    """외부 에이전트 discovery manifest가 존재하는 API 문서를 가리킨다."""
    manifest = client.get("/.well-known/ai-plugin.json")
    assert manifest.status_code == 200
    document = client.get("/api/v1/openapi.json")
    assert document.status_code == 200
    assert document.json()["openapi"] == "3.0.3"

"""자연어 비서의 검증된 도구 실행과 판매 결과 계약 테스트."""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.creator_action_service import CreatorActionService
from services.creator_assistant_service import CreatorAssistantService
from services.gemini_service import CreatorActionPlan
from tests.conftest import VALID_WALLET


@pytest.mark.django_db
def test_expense_action_is_recorded_and_verified():
    from apps.ip.models import AssistantAction, AssistantMessage
    from tests.factories import CreatorFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    source = AssistantMessage.objects.create(
        creator=creator, role=AssistantMessage.USER, content="Record 3.5 USDC for storage"
    )
    execution = CreatorActionService().execute(
        creator=creator,
        source_message=source,
        action={"name": "record_expense", "arguments": {"amount_usdc": "3.5", "memo": "storage"}},
    )

    record = AssistantAction.objects.get(pk=execution.action_id)
    assert execution.status == AssistantAction.COMPLETED
    assert execution.verification_passed is True
    assert record.result_payload["amount_usdc"] == "3.5"


@pytest.mark.django_db
def test_asset_terms_action_rejects_another_creators_asset():
    from apps.ip.models import AssistantAction, AssistantMessage
    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    other_asset = IpAssetFactory(creator=CreatorFactory())
    source = AssistantMessage.objects.create(
        creator=creator, role=AssistantMessage.USER, content="Make another asset public"
    )
    execution = CreatorActionService().execute(
        creator=creator,
        source_message=source,
        action={
            "name": "update_asset_terms",
            "arguments": {
                "asset_id": str(other_asset.id),
                "min_price_usdc": "1",
                "target_price_usdc": "2",
                "visibility": "public",
            },
        },
    )

    assert execution.status == AssistantAction.REJECTED
    assert execution.result == {"code": "asset_not_found"}


@pytest.mark.django_db
def test_structured_plan_executes_only_through_verified_action_service():
    from apps.ip.models import AssistantAction, CreatorExpense
    from tests.factories import CreatorFactory

    class PlanningGemini:
        def plan_creator_action(self, context, message):
            return CreatorActionPlan(
                reply="I will record the expense and show its verification status.",
                action={
                    "name": "record_expense",
                    "arguments": {"amount_usdc": "2.25", "memo": "preview storage"},
                },
            )

    CreatorFactory(wallet_address=VALID_WALLET)
    outcome = CreatorAssistantService(gemini=PlanningGemini()).ask(
        VALID_WALLET, "Record 2.25 USDC for preview storage"
    )

    assert outcome.action is not None
    assert outcome.action["verification_passed"] is True
    assert CreatorExpense.objects.get().amount_usdc == Decimal("2.250000")
    assert AssistantAction.objects.get().status == AssistantAction.COMPLETED


@pytest.mark.django_db
def test_sales_endpoint_uses_only_verified_license_rows(client):
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    asset = IpAssetFactory(creator=creator)
    LicenseFactory(asset=asset, price_usdc=Decimal("7.500000"))
    LicenseFactory(price_usdc=Decimal("99.000000"))

    response = client.get(f"/api/v1/assistant/sales?creator={VALID_WALLET}")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {
        "sale_count": 1,
        "gross_usdc": "7.5",
        "platform_fee_bps": 0,
        "platform_fee_usdc": "0",
        "creator_proceeds_usdc": "7.5",
    }
    assert data["items"][0]["asset_id"] == str(asset.id)

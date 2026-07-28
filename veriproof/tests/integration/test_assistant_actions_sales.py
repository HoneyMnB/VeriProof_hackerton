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
    LicenseFactory(asset=asset, price_usdc=None, price_sol=Decimal("7.500000000"), payment_currency="SOL")
    LicenseFactory(asset=asset, price_usdc=Decimal("20.000000"), price_sol=None, payment_currency="USDC")
    LicenseFactory(price_usdc=None, price_sol=Decimal("99.000000000"), payment_currency="SOL")

    response = client.get(f"/api/v1/assistant/sales?creator={VALID_WALLET}")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {
        "sale_count": 1,
        "gross_sol": "7.5",
        "platform_fee_bps": 0,
        "platform_fee_sol": "0",
        "creator_proceeds_sol": "7.5",
    }
    assert data["items"][0]["asset_id"] == str(asset.id)
    assert data["items"][0]["price_sol"] == "7.500000000"


@pytest.mark.django_db
def test_sales_endpoint_filters_and_paginates_actual_license_history(client):
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    selected = IpAssetFactory(creator=creator, title="Selected work")
    other = IpAssetFactory(creator=creator, title="Other work")
    LicenseFactory(asset=selected, buyer_wallet="BuyerWalletOne", usage_type="commercial", price_usdc=None, price_sol=Decimal("3.000000000"), payment_currency="SOL")
    LicenseFactory(asset=selected, buyer_wallet="BuyerWalletTwo", usage_type="editorial", price_usdc=None, price_sol=Decimal("4.000000000"), payment_currency="SOL")
    LicenseFactory(asset=other, price_usdc=None, price_sol=Decimal("9.000000000"), payment_currency="SOL")

    response = client.get(f"/api/v1/assistant/sales?creator={VALID_WALLET}&asset={selected.id}&usage=commercial&page_size=1")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["sale_count"] == 1
    assert data["summary"]["gross_sol"] == "3"
    assert data["pagination"] == {"page": 1, "page_size": 1, "total_count": 1, "page_count": 1}
    assert data["dashboard"]["by_work_pagination"] == {
        "page": 1,
        "page_size": 10,
        "total_count": 1,
        "page_count": 1,
    }
    assert data["dashboard"]["by_work"][0]["gross_sol"] == "3"
    assert data["dashboard"]["by_work"][0]["average_sol"] == "3"
    assert data["items"][0]["buyer_wallet"] == "BuyerWalletOne"
    assert data["items"][0]["granted_at"]
    assert data["items"][0]["payment_tx_sig"]


@pytest.mark.django_db
def test_sales_endpoint_rejects_invalid_filter_dates(client):
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)

    response = client.get(f"/api/v1/assistant/sales?creator={VALID_WALLET}&start=not-a-date")

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_sales_filter"}


@pytest.mark.django_db
def test_sales_endpoint_paginates_work_aggregates_server_side(client):
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    first = IpAssetFactory(creator=creator, title="Higher gross")
    second = IpAssetFactory(creator=creator, title="Lower gross")
    LicenseFactory(asset=first, price_usdc=None, price_sol=Decimal("8.000000000"), payment_currency="SOL")
    LicenseFactory(asset=second, price_usdc=None, price_sol=Decimal("3.000000000"), payment_currency="SOL")

    response = client.get(f"/api/v1/assistant/sales?creator={VALID_WALLET}&work_page=2&work_page_size=1")

    assert response.status_code == 200
    data = response.json()
    assert data["dashboard"]["by_work_pagination"] == {
        "page": 2,
        "page_size": 1,
        "total_count": 2,
        "page_count": 2,
    }
    assert data["dashboard"]["by_work"][0]["asset_title"] == "Lower gross"

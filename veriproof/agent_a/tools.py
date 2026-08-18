"""판매자 에이전트 A가 사용하는 읽기 전용 마켓플레이스 도구."""

import decimal
import uuid
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.ip.models import IpAsset
from apps.settlement.models import License
from services.catalog_service import get_catalog_service

from .schemas import AssetType
from services.tools_logger import ToolsLogger

tool_log = ToolsLogger()


def _json_safe_asset(asset: Any) -> dict[str, Any]:
    payload = get_catalog_service().serialize(asset)
    created_at = payload.get("created_at")
    if created_at is not None:
        payload["created_at"] = created_at.isoformat()
    return payload


def _search_licensable_assets(
    query: str,
    asset_type: AssetType | str | None = None,
    maximum_price_sol: float | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """동기 Django ORM 경로에서 공개 등록 자산을 검색한다."""
    try:

        tool_log.info(f"query: {query}, asset_type: {asset_type}, maximum_price_sol: {maximum_price_sol}, limit: {limit}")
        canonical_asset_type = (
            AssetType(asset_type).value if asset_type else ""
        )
    except ValueError:
        return {
            "status": "invalid_asset_type",
            "allowed_asset_types": [item.value for item in AssetType],
            "count": 0,
            "assets": [],
        }

    bounded_limit = max(1, min(int(limit), 20))
    price_max = (
        decimal.Decimal(str(maximum_price_sol))
        if maximum_price_sol is not None
        else None
    )
    tool_log.info(f"price_max: {price_max}")
    assets = get_catalog_service().search(
        query=query.strip(),
        asset_type=canonical_asset_type,
        price_max=price_max,
    )
    results = [_json_safe_asset(asset) for asset in assets[:bounded_limit]]
    return {"count": len(results), "assets": results}


async def search_licensable_assets(
    query: str,
    asset_type: AssetType | None = None,
    maximum_price_sol: float | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """
    라이선스 구매가 가능한 공개 블록체인 등록 자산을 검색한다.
    Args:
        query: 주제, 분위기, 스타일 등의 자연어 검색어.
        asset_type: image, document, audio, video, software, product, other 중
            하나인 선택 자산 유형. 사용자 표현을 번역하지 말고 이 표준 값을 사용한다.
        maximum_price_sol: 선택 가능한 최대 최소 판매가(SOL).
        limit: 1개에서 20개 사이의 최대 결과 수.
    """
    return await sync_to_async(_search_licensable_assets, thread_sensitive=True)(
        query,
        asset_type,
        maximum_price_sol,
        limit,
    )


def _get_licensable_asset(asset_id: str) -> dict[str, Any]:
    """동기 Django ORM 경로에서 공개 라이선스 정보를 조회한다."""
    try:
        parsed_id = uuid.UUID(asset_id)
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid_asset_id"}

    asset = (
        IpAsset.objects.select_related("creator")
        .filter(
            id=parsed_id,
            visibility=IpAsset.PUBLIC,
            status__in=(IpAsset.ANCHORED, IpAsset.LISTED),
            registration_certificate_tx_sig__isnull=False,
        )
        .first()
    )
    if asset is None:
        return {"status": "not_found"}
    return {"status": "found", "asset": _json_safe_asset(asset)}


async def get_licensable_asset(asset_id: str) -> dict[str, Any]:
    """하나의 자산 ID에 대한 공개 라이선스 정보를 반환한다."""
    return await sync_to_async(_get_licensable_asset, thread_sensitive=True)(asset_id)


def _get_purchase_fulfillment(
    asset_id: str,
    transaction_signature: str,
) -> dict[str, Any]:
    """Return delivery facts only for a settled, still-downloadable license."""
    try:
        parsed_asset_id = uuid.UUID(asset_id)
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid_asset_id"}

    signature = str(transaction_signature or "").strip()
    if not signature or len(signature) > 90:
        return {"status": "invalid_transaction_signature"}

    license = (
        License.objects.select_related("asset")
        .filter(asset_id=parsed_asset_id, payment_tx_sig=signature)
        .first()
    )
    if license is None:
        return {"status": "not_settled"}
    if not license.download_token or (
        license.download_expires_at is not None
        and license.download_expires_at <= timezone.now()
    ):
        return {"status": "download_unavailable"}

    download_url = (
        f"{settings.A2A_PUBLIC_BASE_URL.rstrip('/')}/files/{license.download_token}"
    )
    return {
        "status": "fulfilled",
        "delivery": {
            "asset_id": str(license.asset_id),
            "asset_title": license.asset.title,
            "license_id": str(license.id),
            "transaction_signature": license.payment_tx_sig,
            "amount_usdc": str(license.price_usdc),
            "currency": license.payment_currency,
            "network_fee_usdc": "0",
            "fee_sponsor": "VeriProof",
            "download_url": download_url,
            "download_expires_at": (
                license.download_expires_at.isoformat()
                if license.download_expires_at
                else None
            ),
        },
    }


async def get_purchase_fulfillment(
    asset_id: str,
    transaction_signature: str,
) -> dict[str, Any]:
    """Return the actual download and receipt data for one completed purchase.

    This tool never creates a payment or a license. It can only disclose a
    persisted fulfillment record whose asset and transaction both match.
    """
    return await sync_to_async(_get_purchase_fulfillment, thread_sensitive=True)(
        asset_id,
        transaction_signature,
    )

"""판매자 에이전트 A가 사용하는 읽기 전용 마켓플레이스 도구."""

import decimal
import uuid
from typing import Any

from apps.ip.models import IpAsset
from services.catalog_service import get_catalog_service


def _json_safe_asset(asset: Any) -> dict[str, Any]:
    payload = get_catalog_service().serialize(asset)
    created_at = payload.get("created_at")
    if created_at is not None:
        payload["created_at"] = created_at.isoformat()
    return payload


def search_licensable_assets(
    query: str,
    asset_type: str = "",
    maximum_price_usdc: float | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """라이선스 구매가 가능한 공개 블록체인 등록 자산을 검색한다.

    Args:
        query: 주제, 분위기, 스타일 등의 자연어 검색어.
        asset_type: 이미지, 오디오, 비디오 등의 선택 자산 유형.
        maximum_price_usdc: 선택 가능한 최대 최소 판매가(USDC).
        limit: 1개에서 20개 사이의 최대 결과 수.
    """
    bounded_limit = max(1, min(int(limit), 20))
    price_max = (
        decimal.Decimal(str(maximum_price_usdc))
        if maximum_price_usdc is not None
        else None
    )
    assets = get_catalog_service().search(
        query=query.strip(),
        asset_type=asset_type.strip().lower(),
        price_max=price_max,
    )
    results = [_json_safe_asset(asset) for asset in assets[:bounded_limit]]
    return {"count": len(results), "assets": results}


def get_licensable_asset(asset_id: str) -> dict[str, Any]:
    """하나의 자산 ID에 대한 공개 라이선스 정보를 반환한다."""
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

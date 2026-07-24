"""Public discovery projection for external agents and the web catalog."""
from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q

logger = logging.getLogger(__name__)


class CatalogService:
    """Read-only public catalogue; never projects original-content locations."""

    SORT_MAP = {
        "recent": "-created_at",
        "price_low": "min_price_usdc",
        "price_high": "-min_price_usdc",
        "originality": "-originality_score",
    }

    def search(
        self,
        query: str = "",
        asset_type: str = "",
        sort: str = "recent",
        price_min: Any | None = None,
        price_max: Any | None = None,
    ) -> list[Any]:
        from apps.ip.models import IpAsset

        queryset = IpAsset.objects.select_related("creator").filter(
            visibility=IpAsset.PUBLIC,
            status__in=(IpAsset.ANCHORED, IpAsset.LISTED),
            registration_certificate_tx_sig__isnull=False,
        )
        if asset_type:
            queryset = queryset.filter(asset_type=asset_type)
        if query:
            # 사용자 입력과 등록 시점 AI가 붙인 메타데이터를 함께 검색해, 외부/내부
            # 에이전트가 공개 자산을 더 쉽게 발견하도록 한다.
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(ai_description__icontains=query)
                | Q(category__icontains=query)
                | Q(tags__icontains=query)
                | Q(ai_tags__icontains=query)
            )
        if price_min is not None:
            queryset = queryset.filter(min_price_usdc__gte=price_min)
        if price_max is not None:
            queryset = queryset.filter(min_price_usdc__lte=price_max)
        order_by = self.SORT_MAP.get(sort, "-created_at")
        results = list(queryset.order_by(order_by))
        logger.info(
            "public catalog search type=%s query=%r sort=%s results=%d",
            asset_type, query, sort, len(results),
        )
        return results

    @staticmethod
    def serialize(asset: Any) -> dict:
        from services.preview_service import watermark_preview_url

        return {
            "asset_id": str(asset.id),
            "title": asset.title,
            "description": asset.description or "",
            "asset_type": asset.asset_type,
            "tags": list(asset.tags or []),
            "category": asset.category,
            # 공개 카탈로그에는 앱 권한 경계를 거친 워터마크만 제공한다.
            "watermark_url": watermark_preview_url(asset.id),
            "min_price_usdc": str(asset.min_price_usdc),
            "target_price_usdc": str(asset.target_price_usdc),
            "originality_score": asset.originality_score,
            "created_at": asset.created_at,
            "x402_endpoint": f"/api/v1/ip/{asset.id}",
        }


def get_catalog_service() -> CatalogService:
    return CatalogService()

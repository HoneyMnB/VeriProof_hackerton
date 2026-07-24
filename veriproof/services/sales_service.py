"""창작자 판매 결과와 해커톤 수수료 정책을 조회하는 읽기 전용 모듈."""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate


class FeePolicyError(ValueError):
    """실제 분배 모듈이 없는 수수료 설정을 막는다."""


@dataclass(frozen=True)
class SalesSummary:
    """검증된 라이선스 판매액과 적용 가능한 수수료 결과다."""

    sale_count: int
    gross_usdc: decimal.Decimal
    platform_fee_usdc: decimal.Decimal
    creator_proceeds_usdc: decimal.Decimal
    platform_fee_bps: int


class SalesService:
    """License만 판매 근거로 사용하며, 추정 판매 데이터를 만들지 않는다."""

    def summary(self, creator: Any) -> SalesSummary:
        from apps.settlement.models import License

        fee_bps = int(settings.PLATFORM_FEE_BPS)
        if fee_bps != 0:
            raise FeePolicyError(
                "platform fee collection is not implemented; PLATFORM_FEE_BPS must be 0"
            )
        sales = License.objects.filter(asset__creator=creator)
        gross = sales.aggregate(total=Sum("price_usdc"))["total"] or decimal.Decimal("0")
        platform_fee = decimal.Decimal("0")
        return SalesSummary(
            sale_count=sales.count(),
            gross_usdc=gross,
            platform_fee_usdc=platform_fee,
            creator_proceeds_usdc=gross - platform_fee,
            platform_fee_bps=fee_bps,
        )

    def list_sales(self, creator: Any, limit: int = 100) -> list[dict[str, str]]:
        """창작자 자산의 실제 라이선스 발급 결과를 최신순으로 반환한다."""
        from apps.settlement.models import License

        return [
            {
                "license_id": str(license.id),
                "asset_id": str(license.asset_id),
                "asset_title": license.asset.title or "",
                "buyer_wallet": license.buyer_wallet,
                "price_usdc": str(license.price_usdc),
                "usage_type": license.usage_type,
                "granted_at": license.granted_at.isoformat(),
            }
            for license in License.objects.filter(asset__creator=creator)
            .select_related("asset")
            .order_by("-granted_at")[:limit]
        ]

    def dashboard(self, creator: Any, days: int = 30) -> dict[str, list[dict[str, str]]]:
        """Return auditable per-work and per-day aggregates from settled licenses."""
        from apps.settlement.models import License

        sales = License.objects.filter(asset__creator=creator)
        by_work = sales.values("asset_id", "asset__title").annotate(
            sale_count=Count("id"), gross_usdc=Sum("price_usdc")
        ).order_by("-gross_usdc", "asset__title")
        by_day = sales.annotate(day=TruncDate("granted_at")).values("day").annotate(
            sale_count=Count("id"), gross_usdc=Sum("price_usdc")
        ).order_by("-day")[:days]
        return {
            "by_work": [{"asset_id": str(row["asset_id"]), "asset_title": row["asset__title"] or "", "sale_count": row["sale_count"], "gross_usdc": str(row["gross_usdc"])} for row in by_work],
            "by_day": [{"date": row["day"].isoformat(), "sale_count": row["sale_count"], "gross_usdc": str(row["gross_usdc"])} for row in by_day],
        }


def get_sales_service() -> SalesService:
    """판매 결과 조회 유스케이스 팩토리."""
    return SalesService()

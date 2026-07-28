"""창작자 판매 결과와 해커톤 수수료 정책을 조회하는 읽기 전용 모듈."""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from django.conf import settings
from django.db.models import Avg, Count, Max, Sum
from django.utils import timezone


class FeePolicyError(ValueError):
    """실제 분배 모듈이 없는 수수료 설정을 막는다."""


@dataclass(frozen=True)
class SalesSummary:
    """검증된 라이선스 판매액과 적용 가능한 수수료 결과다."""

    sale_count: int
    gross_sol: decimal.Decimal
    platform_fee_sol: decimal.Decimal
    creator_proceeds_sol: decimal.Decimal
    platform_fee_bps: int


class SalesService:
    """License만 판매 근거로 사용하며, 추정 판매 데이터를 만들지 않는다."""

    def summary(self, creator: Any) -> SalesSummary:
        """정산 완료된 라이선스만 합산한 창작자 판매 요약을 반환한다.

        수수료 징수가 아직 구현되지 않았으므로 ``PLATFORM_FEE_BPS``가 0일 때만
        동작하고, 그렇지 않으면 ``FeePolicyError``를 올린다.
        """
        fee_bps = int(settings.PLATFORM_FEE_BPS)
        if fee_bps != 0:
            raise FeePolicyError(
                "platform fee collection is not implemented; PLATFORM_FEE_BPS must be 0"
            )
        sales = self._sales(creator)
        gross = sales.aggregate(total=Sum("price_sol"))["total"] or decimal.Decimal("0")
        platform_fee = decimal.Decimal("0")
        return SalesSummary(
            sale_count=sales.count(),
            gross_sol=gross,
            platform_fee_sol=platform_fee,
            creator_proceeds_sol=gross - platform_fee,
            platform_fee_bps=fee_bps,
        )

    def list_sales(self, creator: Any, sales: Any | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, str]]:
        """창작자 자산의 실제 라이선스 발급 결과를 최신순으로 반환한다."""
        sales = (
            self._sales(creator).select_related("asset").order_by("-granted_at")
            if sales is None
            else sales.select_related("asset")
        )
        return [
            {
                "license_id": str(license.id),
                "asset_id": str(license.asset_id),
                "asset_title": license.asset.title or "",
                "buyer_wallet": license.buyer_wallet,
                "price_sol": str(license.price_sol),
                "usage_type": license.usage_type,
                "granted_at": license.granted_at.isoformat(),
                "payment_tx_sig": license.payment_tx_sig,
                "certificate_tx_sig": license.certificate_tx_sig or "",
            }
            for license in sales[offset:offset + limit]
        ]

    def dashboard(
        self, creator: Any, sales: Any | None = None, work_page: int = 1, work_page_size: int = 10
    ) -> dict[str, Any]:
        """Return auditable per-work aggregates from settled licenses."""
        sales = sales if sales is not None else self._sales(creator)
        by_work = sales.values("asset_id", "asset__title").annotate(
            sale_count=Count("id"),
            gross_sol=Sum("price_sol"),
            average_sol=Avg("price_sol"),
            last_sold_at=Max("granted_at"),
        ).order_by("-gross_sol", "asset__title")
        work_count = by_work.count()
        work_page_count = max(1, (work_count + work_page_size - 1) // work_page_size)
        work_page = min(work_page, work_page_count)
        work_offset = (work_page - 1) * work_page_size
        return {
            "by_work": [
                {
                    "asset_id": str(row["asset_id"]),
                    "asset_title": row["asset__title"] or "",
                    "sale_count": row["sale_count"],
                    "gross_sol": str(row["gross_sol"]),
                    "average_sol": str(row["average_sol"]),
                    "last_sold_at": row["last_sold_at"].isoformat(),
                }
                for row in by_work[work_offset:work_offset + work_page_size]
            ],
            "by_work_pagination": {
                "page": work_page,
                "page_size": work_page_size,
                "total_count": work_count,
                "page_count": work_page_count,
            },
        }

    def report(
        self, creator: Any, *, search: str = "", asset_id: str = "", usage_type: str = "",
        start_date: date | None = None, end_date: date | None = None, page: int = 1, page_size: int = 20,
        work_page: int = 1, work_page_size: int = 10,
    ) -> dict[str, Any]:
        """Return a paginated, filter-aware seller report from settled licenses only."""
        sales = self._sales(creator)
        if search:
            from django.db.models import Q
            sales = sales.filter(Q(asset__title__icontains=search) | Q(buyer_wallet__icontains=search))
        if asset_id:
            sales = sales.filter(asset_id=asset_id)
        if usage_type:
            sales = sales.filter(usage_type=usage_type)
        if start_date:
            sales = sales.filter(granted_at__gte=timezone.make_aware(datetime.combine(start_date, time.min)))
        if end_date:
            sales = sales.filter(granted_at__lte=timezone.make_aware(datetime.combine(end_date, time.max)))
        total_count = sales.count()
        page_count = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, page_count)
        offset = (page - 1) * page_size
        filtered_summary = self._summary_for(sales)
        assets = self._sales(creator).values("asset_id", "asset__title").distinct().order_by("asset__title")
        usages = self._sales(creator).values_list("usage_type", flat=True).distinct().order_by("usage_type")
        return {
            "summary": self._summary_dict(filtered_summary),
            "items": self.list_sales(creator, sales=sales, limit=page_size, offset=offset),
            "dashboard": self.dashboard(
                creator, sales=sales, work_page=work_page, work_page_size=work_page_size
            ),
            "pagination": {"page": page, "page_size": page_size, "total_count": total_count, "page_count": page_count},
            "filters": {
                "assets": [{"asset_id": str(row["asset_id"]), "asset_title": row["asset__title"] or ""} for row in assets],
                "usage_types": list(usages),
            },
        }

    def _sales(self, creator: Any) -> Any:
        """창작자 자산에 발급된 라이선스 쿼리셋을 반환한다."""
        from apps.settlement.models import License
        return License.objects.filter(
            asset__creator=creator,
            payment_currency="SOL",
            price_sol__isnull=False,
        )

    def _summary_for(self, sales: Any) -> SalesSummary:
        """필터링된 라이선스 쿼리셋에서 판매 요약을 계산한다.

        ``summary``와 동일한 수수료 정책 제약(``PLATFORM_FEE_BPS == 0``)을 적용한다.
        """
        fee_bps = int(settings.PLATFORM_FEE_BPS)
        if fee_bps != 0:
            raise FeePolicyError("platform fee collection is not implemented; PLATFORM_FEE_BPS must be 0")
        gross = sales.aggregate(total=Sum("price_sol"))["total"] or decimal.Decimal("0")
        return SalesSummary(sales.count(), gross, decimal.Decimal("0"), gross, fee_bps)

    @staticmethod
    def _summary_dict(summary: SalesSummary) -> dict[str, Any]:
        """SalesSummary를 문자열 직렬화한 응답용 dict로 변환한다."""
        return {
            "sale_count": summary.sale_count,
            "gross_sol": str(summary.gross_sol),
            "platform_fee_bps": summary.platform_fee_bps,
            "platform_fee_sol": str(summary.platform_fee_sol),
            "creator_proceeds_sol": str(summary.creator_proceeds_sol),
        }


def get_sales_service() -> SalesService:
    """판매 결과 조회 유스케이스 팩토리."""
    return SalesService()

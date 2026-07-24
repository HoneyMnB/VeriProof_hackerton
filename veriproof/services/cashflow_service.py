"""창작자 현금흐름 조회·지출 기록 유스케이스.

수입은 체인 결제 검증 후 발급된 License만 집계하고, 지출은 창작자가 직접
명시적으로 입력한 CreatorExpense만 기록한다.
"""
from __future__ import annotations

import datetime
import decimal
import logging
from dataclasses import dataclass

from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


class CashflowValidationError(ValueError):
    """현금흐름 입력이 유효하지 않을 때 발생한다."""


@dataclass(frozen=True)
class CashflowSummary:
    """검증된 수입과 사용자가 기록한 지출의 합계다."""

    income_usdc: decimal.Decimal
    expense_usdc: decimal.Decimal


class CashflowService:
    """지출 쓰기와 현금흐름 읽기를 한 모듈로 관리한다."""

    def summary(self, creator) -> CashflowSummary:
        """라이선스 수입과 기록 지출을 실제 DB에서 집계한다."""
        from apps.settlement.models import License

        income = License.objects.filter(asset__creator=creator).aggregate(total=Sum("price_usdc"))["total"]
        expense = creator.expenses.aggregate(total=Sum("amount_usdc"))["total"]
        return CashflowSummary(income or decimal.Decimal("0"), expense or decimal.Decimal("0"))

    def record_expense(
        self, *, wallet: str, amount_usdc: decimal.Decimal, memo: str, occurred_at: datetime.datetime | None = None
    ):
        """창작자의 실제 지출을 기록한다. 음수·빈 메모는 허용하지 않는다."""
        from apps.ip.models import Creator, CreatorExpense

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        if not amount_usdc.is_finite() or amount_usdc <= 0:
            raise CashflowValidationError("amount_usdc must be a positive finite number")
        if not memo.strip():
            raise CashflowValidationError("memo is required")
        expense = CreatorExpense.objects.create(
            creator=creator,
            amount_usdc=amount_usdc,
            memo=memo.strip(),
            occurred_at=occurred_at or timezone.now(),
        )
        logger.info("creator expense recorded creator_wallet=%s expense_id=%s", wallet, expense.id)
        return expense


def get_cashflow_service() -> CashflowService:
    """현금흐름 유스케이스를 생성한다."""
    return CashflowService()

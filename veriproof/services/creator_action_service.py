"""창작자 비서가 호출할 수 있는 검증 가능한 변경 도구 모듈."""
from __future__ import annotations

import decimal
import logging
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionExecution:
    """도구 실행과 DB 재검증 결과를 API/UI에 전달한다."""

    action_id: int
    action_name: str
    status: str
    verification_passed: bool
    result: dict[str, Any]


class CreatorActionService:
    """허용된 자산·지출 변경만 실행하고 완료 후 DB를 다시 읽어 검증한다."""

    def execute(
        self, *, creator: Any, source_message: Any, action: dict[str, Any]
    ) -> ActionExecution:
        from apps.ip.models import AssistantAction

        action_name = str(action.get("name") or "").strip()
        payload = action.get("arguments")
        if not isinstance(payload, dict):
            payload = {}
        record = AssistantAction.objects.create(
            creator=creator,
            source_message=source_message,
            action_name=action_name or "invalid",
            status=AssistantAction.REJECTED,
            request_payload=payload,
        )
        handler = {
            "record_expense": self._record_expense,
            "update_asset_terms": self._update_asset_terms,
            "prepare_registration": self._prepare_registration,
        }.get(action_name)
        if handler is None:
            return self._finish(
                record,
                status=AssistantAction.REJECTED,
                verified=False,
                result={"code": "unsupported_action"},
            )
        try:
            status, verified, result = handler(creator, payload)
        except Exception as exc:  # noqa: BLE001 - action boundary preserves an audit record
            logger.error(
                "assistant action failed creator_wallet=%s action=%s error=%s",
                creator.wallet_address,
                action_name,
                exc,
            )
            return self._finish(
                record,
                status=AssistantAction.FAILED,
                verified=False,
                result={"code": "execution_failed"},
            )
        return self._finish(record, status=status, verified=verified, result=result)

    def _record_expense(self, creator: Any, payload: dict[str, Any]):
        """지출을 기록한 뒤 생성된 행을 직접 조회해 금액과 메모를 검증한다."""
        from apps.ip.models import AssistantAction
        from services.cashflow_service import get_cashflow_service

        try:
            amount = decimal.Decimal(str(payload.get("amount_usdc")))
        except decimal.InvalidOperation:
            return AssistantAction.REJECTED, False, {"code": "invalid_expense_amount"}
        memo = str(payload.get("memo") or "")
        expense = get_cashflow_service().record_expense(
            wallet=creator.wallet_address, amount_usdc=amount, memo=memo
        )
        verified = (
            expense.creator_id == creator.id
            and expense.amount_usdc == amount
            and expense.memo == memo.strip()
        )
        return (
            AssistantAction.COMPLETED if verified else AssistantAction.FAILED,
            verified,
            {"expense_id": expense.id, "amount_usdc": str(expense.amount_usdc), "memo": expense.memo},
        )

    def _update_asset_terms(self, creator: Any, payload: dict[str, Any]):
        """소유 자산의 가격·공개 여부만 갱신하고 저장값을 재조회해 검증한다."""
        from apps.ip.models import AssistantAction, IpAsset

        asset_id = str(payload.get("asset_id") or "")
        asset = IpAsset.objects.filter(id=asset_id, creator=creator).first()
        if asset is None:
            return AssistantAction.REJECTED, False, {"code": "asset_not_found"}
        try:
            min_price = decimal.Decimal(str(payload.get("min_price_usdc")))
            target_price = decimal.Decimal(str(payload.get("target_price_usdc")))
        except decimal.InvalidOperation:
            return AssistantAction.REJECTED, False, {"code": "invalid_asset_terms"}
        visibility = str(payload.get("visibility") or "").strip().lower()
        if min_price < 0 or target_price < min_price or visibility not in {
            IpAsset.PRIVATE,
            IpAsset.PUBLIC,
        }:
            return AssistantAction.REJECTED, False, {"code": "invalid_asset_terms"}
        asset.min_price_usdc = min_price
        asset.target_price_usdc = target_price
        asset.visibility = visibility
        asset.save(update_fields=["min_price_usdc", "target_price_usdc", "visibility"])
        verified_asset = IpAsset.objects.filter(id=asset.id, creator=creator).values(
            "min_price_usdc", "target_price_usdc", "visibility"
        ).first()
        verified = bool(
            verified_asset
            and verified_asset["min_price_usdc"] == min_price
            and verified_asset["target_price_usdc"] == target_price
            and verified_asset["visibility"] == visibility
        )
        return (
            AssistantAction.COMPLETED if verified else AssistantAction.FAILED,
            verified,
            {
                "asset_id": str(asset.id),
                "min_price_usdc": str(min_price),
                "target_price_usdc": str(target_price),
                "visibility": visibility,
            },
        )

    @staticmethod
    def _prepare_registration(creator: Any, payload: dict[str, Any]):
        """파일 없이 등록을 가장하지 않고 업로드가 필요함을 명시적으로 기록한다."""
        from apps.ip.models import AssistantAction

        return (
            AssistantAction.AWAITING_INPUT,
            False,
            {"required_input": "file_upload", "requested_title": str(payload.get("title") or "")},
        )

    @staticmethod
    def _finish(record: Any, *, status: str, verified: bool, result: dict[str, Any]):
        record.status = status
        record.result_payload = result
        record.verification_passed = verified
        record.verified_at = timezone.now() if verified else None
        record.save(
            update_fields=["status", "result_payload", "verification_passed", "verified_at"]
        )
        return ActionExecution(
            action_id=record.id,
            action_name=record.action_name,
            status=status,
            verification_passed=verified,
            result=result,
        )


def get_creator_action_service() -> CreatorActionService:
    """창작자 비서 도구 실행기 팩토리."""
    return CreatorActionService()

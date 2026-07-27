"""구독 권한과 등록·인증서 비용 사용 이력을 관리하는 유스케이스."""
from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


class SubscriptionRequiredError(ValueError):
    """유효한 구독 또는 남은 등록 권한이 없을 때 발생한다."""


class SubscriptionService:
    """창작자 구독 활성화·권한 검증·사용 차감을 담당하는 유스케이스."""

    def authorize_registration(self, creator_wallet: str) -> None:
        """외부 작업 전 활성 구독과 잔여 등록 권한을 확인한다."""
        from apps.ip.models import Creator, CreatorSubscription

        creator = Creator.objects.filter(wallet_address=creator_wallet).first()
        now = timezone.now()
        subscription = CreatorSubscription.objects.filter(
            creator=creator, status=CreatorSubscription.ACTIVE,
            period_start__lte=now, period_end__gt=now, plan__is_active=True,
        ).select_related("plan").first()
        if subscription is None or subscription.registrations_used >= subscription.plan.included_registrations:
            raise SubscriptionRequiredError("an active subscription with registration capacity is required")

    def consume_registration(self, creator: Any, asset: Any) -> None:
        """자산 저장 트랜잭션 안에서 권한을 한 번만 차감하고 감사 기록을 남긴다."""
        from apps.ip.models import CreatorSubscription, RegistrationCharge

        now = timezone.now()
        subscription = CreatorSubscription.objects.select_for_update().filter(
            creator=creator, status=CreatorSubscription.ACTIVE,
            period_start__lte=now, period_end__gt=now, plan__is_active=True,
        ).select_related("plan").first()
        if subscription is None or subscription.registrations_used >= subscription.plan.included_registrations:
            raise SubscriptionRequiredError("subscription capacity is no longer available")
        subscription.registrations_used += 1
        subscription.save(update_fields=["registrations_used"])
        RegistrationCharge.objects.create(subscription=subscription, asset=asset)
        logger.info("subscription registration consumed creator_wallet=%s asset_id=%s", creator.wallet_address, asset.id)


def get_subscription_service() -> SubscriptionService:
    """구독 유스케이스 인스턴스를 생성한다."""
    return SubscriptionService()

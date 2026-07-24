"""대화형 등록 초안의 검증·확정 경계."""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class DraftValidationError(ValueError):
    """초안 상태 또는 필수 등록 값이 유효하지 않을 때 발생한다."""


@dataclass(frozen=True)
class ConfirmedDraft:
    """실제 등록 유스케이스에 전달할 확정 초안이다."""

    draft_id: str
    fields: dict[str, Any]


class RegistrationDraftService:
    """초안 수정과 확정을 담당하며 체인·저장소 호출은 하지 않는다."""

    _ALLOWED_FIELDS = frozenset({"asset_type", "title", "description", "tags", "min_price", "target_price", "visibility"})

    def save(self, wallet: str, payload: dict[str, Any]) -> dict[str, Any]:
        """사용자·에이전트가 수집한 값만 병합하고 확정 상태는 무효화한다."""
        from apps.ip.models import Creator, RegistrationDraft

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        draft_id = str(payload.get("draft_id") or "").strip()
        draft = RegistrationDraft.objects.filter(id=draft_id, creator=creator).first() if draft_id else None
        if draft is None:
            draft = RegistrationDraft(creator=creator)
        if draft.status == RegistrationDraft.EXECUTED:
            raise DraftValidationError("An executed draft cannot be changed.")
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        invalid = set(fields) - self._ALLOWED_FIELDS
        if invalid:
            raise DraftValidationError("Unsupported draft fields.")
        cleaned = {key: str(value).strip() for key, value in fields.items() if value is not None}
        draft.fields = {**draft.fields, **cleaned}
        draft.file_name = str(payload.get("file_name") or draft.file_name).strip()[:255]
        supplied_hash = str(payload.get("file_sha256") or draft.file_sha256).strip().lower()
        if supplied_hash and (len(supplied_hash) != 64 or any(char not in "0123456789abcdef" for char in supplied_hash)):
            raise DraftValidationError("The attachment hash is invalid.")
        draft.file_sha256 = supplied_hash
        draft.status = RegistrationDraft.COLLECTING
        draft.confirmation_token = None
        draft.confirmed_at = None
        draft.save()
        logger.info("registration draft saved creator_wallet=%s draft_id=%s", wallet, draft.id)
        return self.serialize(draft)

    def confirm(self, wallet: str, draft_id: str) -> dict[str, Any]:
        """필수값을 재검사한 뒤 1회용 확정 토큰을 발급한다."""
        from apps.ip.models import Creator, RegistrationDraft

        creator = Creator.objects.filter(wallet_address=wallet).first()
        draft = RegistrationDraft.objects.filter(id=draft_id, creator=creator).first() if creator else None
        if draft is None:
            raise LookupError("draft_not_found")
        self._validate_ready(draft)
        draft.status = RegistrationDraft.CONFIRMED
        draft.confirmation_token = uuid.uuid4()
        draft.confirmed_at = timezone.now()
        draft.save(update_fields=["status", "confirmation_token", "confirmed_at", "updated_at"])
        logger.info("registration draft confirmed creator_wallet=%s draft_id=%s", wallet, draft.id)
        return self.serialize(draft)

    def consume(self, wallet: str, draft_id: str, token: str, upload: Any) -> ConfirmedDraft:
        """업로드 바이트 해시와 확정 토큰을 함께 검증해 실제 등록을 허용한다."""
        from apps.ip.models import Creator, RegistrationDraft

        with transaction.atomic():
            creator = Creator.objects.select_for_update().filter(wallet_address=wallet).first()
            draft = RegistrationDraft.objects.select_for_update().filter(id=draft_id, creator=creator).first() if creator else None
            if draft is None or draft.status != RegistrationDraft.CONFIRMED:
                raise DraftValidationError("The registration draft is not confirmed.")
            if str(draft.confirmation_token) != str(token):
                raise DraftValidationError("The registration confirmation is invalid.")
            digest = self._hash_upload(upload)
            if digest != draft.file_sha256:
                raise DraftValidationError("The selected file differs from the confirmed attachment.")
            return ConfirmedDraft(draft_id=str(draft.id), fields=dict(draft.fields))

    def mark_executed(self, draft_id: str, asset: Any) -> None:
        """등록 성공 뒤에만 초안을 소비 완료로 전환한다."""
        from apps.ip.models import RegistrationDraft

        RegistrationDraft.objects.filter(id=draft_id, status=RegistrationDraft.CONFIRMED).update(
            status=RegistrationDraft.EXECUTED, executed_asset=asset
        )

    @staticmethod
    def _hash_upload(upload: Any) -> str:
        hasher = hashlib.sha256()
        for chunk in upload.chunks():
            hasher.update(chunk)
        upload.seek(0)
        return hasher.hexdigest()

    @staticmethod
    def _validate_ready(draft: Any) -> None:
        required = ("asset_type", "title", "min_price", "target_price", "visibility")
        if not draft.file_name or not draft.file_sha256 or any(not str(draft.fields.get(key) or "").strip() for key in required):
            raise DraftValidationError("Complete the attachment, type, title, pricing, and visibility before confirming.")

    @staticmethod
    def serialize(draft: Any) -> dict[str, Any]:
        return {"draft_id": str(draft.id), "status": draft.status, "file_name": draft.file_name, "fields": draft.fields, "confirmation_token": str(draft.confirmation_token) if draft.confirmation_token else None}


def get_registration_draft_service() -> RegistrationDraftService:
    """초안 유스케이스 팩토리."""
    return RegistrationDraftService()

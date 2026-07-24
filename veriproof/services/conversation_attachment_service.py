"""대화 첨부 저장과 이미지 분석 유스케이스."""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.ip.models import ConversationAttachment, Creator

logger = logging.getLogger(__name__)

# 드롭 시점 형식 검증에 쓰는 업로드 허용 MIME 집합(등록 업로드 accept 목록과 정렬).
ALLOWED_ATTACHMENT_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "text/plain",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "video/mp4",
        "video/webm",
        "application/zip",
        "application/x-tar",
    }
)


class ConversationAttachmentError(ValueError):
    """첨부 파일 입력 또는 외부 분석 실패를 안전한 HTTP 오류로 전달한다."""

    def __init__(self, code: str, detail: str, status: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


class ConversationAttachmentService:
    """첨부를 임시 보관하고 이미지인 경우 실제 Gemini 분석을 기록한다."""

    def __init__(self, *, image_processor: Any, gemini: Any, storage: Any) -> None:
        self.image_processor = image_processor
        self.gemini = gemini
        self.storage = storage

    def attach(self, wallet: str, upload: Any) -> ConversationAttachment:
        """드롭 시점에는 업로드 가능한 형식인지만 확인하고 저장한다. AI 분석은 하지
        않는다 — 파일은 사용자가 어시스턴트에게 분석·의견을 요청할 때만 멀티모달
        LLM으로 전송된다(CreatorAssistantService.ask 의 analyze_attachment 분기)."""
        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise ConversationAttachmentError("creator_not_found", "creator wallet was not found", 404)
        if not getattr(upload, "name", ""):
            raise ConversationAttachmentError("missing_file", "a file is required")
        content = upload.read()
        if not content:
            raise ConversationAttachmentError("empty_file", "the attachment is empty")
        if len(content) > settings.MAX_UPLOAD_BYTES:
            raise ConversationAttachmentError("file_too_large", "the attachment exceeds the upload limit")
        mime = str(getattr(upload, "content_type", "") or "application/octet-stream")
        if mime not in ALLOWED_ATTACHMENT_MIMES:
            raise ConversationAttachmentError("unsupported_type", "this file type cannot be attached", 415)
        attachment_id = uuid.uuid4()
        ttl = datetime.timedelta(days=int(settings.ORIGINAL_RETENTION_DAYS))
        try:
            temporary_url = self.storage.save_temporary(attachment_id, content, ttl)
        except Exception as exc:  # noqa: BLE001 - storage is an external adapter
            logger.error("conversation attachment failed creator_wallet=%s error=%s", wallet, exc)
            self.storage.purge_original(attachment_id)
            raise ConversationAttachmentError("attachment_unavailable", "the attachment could not be stored", 503) from exc
        return ConversationAttachment.objects.create(
            id=attachment_id,
            creator=creator,
            file_name=str(upload.name)[:255],
            content_mime_type=mime[:100],
            content_sha256=self.image_processor.sha256(content),
            perceptual_hash=None,
            temporary_url=temporary_url,
            expires_at=timezone.now() + ttl,
            analysis={},
        )


def get_conversation_attachment_service() -> ConversationAttachmentService:
    """설정 기반 실제 분석·저장 어댑터를 조립한다."""
    from services.gemini_service import get_gemini_service
    from services.image_processor import get_image_processor
    from services.storage_service import get_storage_service

    return ConversationAttachmentService(
        image_processor=get_image_processor(), gemini=get_gemini_service(), storage=get_storage_service()
    )

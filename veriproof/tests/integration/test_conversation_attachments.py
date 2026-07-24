"""대화 첨부의 실제 저장·분석·메시지 연결 계약을 검증한다."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from services.conversation_attachment_service import (
    ConversationAttachmentError,
    ConversationAttachmentService,
)
from services.creator_assistant_service import CreatorAssistantService
from services.gemini_service import CreatorActionPlan
from services.image_processor import get_image_processor
from tests.conftest import VALID_WALLET
from tests.fakes import FakeGeminiService, FakeStorageService


def _attachment_service():
    return ConversationAttachmentService(
        image_processor=get_image_processor(),
        gemini=FakeGeminiService(),
        storage=FakeStorageService(),
    )


@pytest.mark.django_db
def test_send_stores_attachment_without_llm_analysis(png_bytes):
    """드롭·전송은 저장/연결만 한다. 일반 메시지로는 파일이 LLM에 전송되지 않고
    분석도 되지 않는다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    gemini = FakeGeminiService()
    attachment_service = ConversationAttachmentService(
        image_processor=get_image_processor(), gemini=gemini, storage=FakeStorageService()
    )
    attachment = attachment_service.attach(
        VALID_WALLET, SimpleUploadedFile("work.png", png_bytes, content_type="image/png")
    )
    assert attachment.analysis == {}
    assert attachment.perceptual_hash is None

    class PlainGemini:
        def __init__(self):
            self.attachment_calls = 0

        def plan_creator_action(self, context, message):
            return CreatorActionPlan(reply="Saved. Ask me to analyze it anytime.", action=None)

        def assist_with_attachments(self, context, message, files):
            self.attachment_calls += 1
            return "should not be called"

    double = PlainGemini()
    CreatorAssistantService(gemini=double, attachment_service=attachment_service).ask(
        VALID_WALLET, "Here is my file.", [str(attachment.id)]
    )
    assert double.attachment_calls == 0
    assert not any(call[0] in ("analyze_image", "analyze_asset") for call in gemini.calls)
    attachment.refresh_from_db()
    assert attachment.analysis == {}


@pytest.mark.django_db
def test_attachment_sent_to_llm_only_when_analysis_requested(png_bytes):
    """사용자가 분석·의견을 요청하면(analyze_attachment) 그 때 파일 바이트가
    멀티모달 LLM으로 전달된다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    attachment_service = _attachment_service()
    attachment = attachment_service.attach(
        VALID_WALLET, SimpleUploadedFile("work.png", png_bytes, content_type="image/png")
    )

    class AnalyzingGemini:
        def __init__(self):
            self.files = None

        def plan_creator_action(self, context, message):
            return CreatorActionPlan(
                reply="", action={"name": "analyze_attachment", "arguments": {}}
            )

        def assist_with_attachments(self, context, message, files):
            self.files = files
            return "Here is my analysis of the attached image."

    double = AnalyzingGemini()
    outcome = CreatorAssistantService(
        gemini=double, attachment_service=attachment_service
    ).ask(VALID_WALLET, "Please analyze this image.", [str(attachment.id)])
    assert "analysis" in outcome.answer.lower()
    assert double.files is not None
    assert double.files[0][0] == png_bytes
    assert double.files[0][1] == "image/png"


@pytest.mark.django_db
def test_unsupported_format_analysis_is_declined(png_bytes):
    """분석 불가 형식(zip)은 분석 요청이 와도 LLM에 보내지 않고 안내만 한다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    attachment_service = _attachment_service()
    attachment = attachment_service.attach(
        VALID_WALLET,
        SimpleUploadedFile("bundle.zip", b"PK\x03\x04zip", content_type="application/zip"),
    )

    class AnalyzingGemini:
        def __init__(self):
            self.called = False

        def plan_creator_action(self, context, message):
            return CreatorActionPlan(
                reply="", action={"name": "analyze_attachment", "arguments": {}}
            )

        def assist_with_attachments(self, context, message, files):
            self.called = True
            return "should not run"

    double = AnalyzingGemini()
    outcome = CreatorAssistantService(
        gemini=double, attachment_service=attachment_service
    ).ask(VALID_WALLET, "Analyze this archive.", [str(attachment.id)])
    assert double.called is False
    assert "cannot be analyzed" in outcome.answer.lower()


@pytest.mark.django_db
def test_attach_rejects_unsupported_file_type(png_bytes):
    """드롭 시점 형식 검증: 허용되지 않은 MIME은 저장 전에 거부한다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    service = ConversationAttachmentService(
        image_processor=get_image_processor(), gemini=FakeGeminiService(), storage=FakeStorageService()
    )
    with pytest.raises(ConversationAttachmentError) as exc_info:
        service.attach(
            VALID_WALLET,
            SimpleUploadedFile("payload.exe", b"MZ...", content_type="application/x-msdownload"),
        )
    assert exc_info.value.code == "unsupported_type"
    assert exc_info.value.status == 415

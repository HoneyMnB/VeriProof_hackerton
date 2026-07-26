"""대화형 등록 초안의 확정 게이트를 검증한다."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.ip.models import Creator, RegistrationDraft
from services.registration_draft_service import (
    DraftValidationError,
    RegistrationDraftService,
)


@pytest.mark.django_db
def test_confirmed_draft_requires_matching_upload_hash():
    """확정한 첨부와 다른 바이트는 실제 등록 전에 거부한다."""
    wallet = "DraftWallet111111111111111111111111111111111"
    Creator.objects.create(wallet_address=wallet)
    content = b"draft-content"
    service = RegistrationDraftService()
    initial_upload = SimpleUploadedFile("work.png", content, content_type="image/png")
    draft = service.save(
        wallet,
        {
            "file_name": "work.png",
            "fields": {"asset_type": "image", "title": "Draft", "tags": "coast, sunrise", "min_price": "1", "target_price": "2", "visibility": "private"},
        },
        uploads=initial_upload,
    )
    confirmed = service.confirm(wallet, draft["draft_id"])
    matching = SimpleUploadedFile("work.png", content, content_type="image/png")
    consumed = service.consume(wallet, draft["draft_id"], confirmed["confirmation_token"], matching)
    assert consumed.fields["title"] == "Draft"
    assert consumed.fields["tags"] == "coast, sunrise"
    mismatched = SimpleUploadedFile("work.png", b"different", content_type="image/png")
    with pytest.raises(DraftValidationError, match="differs"):
        service.consume(wallet, draft["draft_id"], confirmed["confirmation_token"], mismatched)
    assert RegistrationDraft.objects.get(id=draft["draft_id"]).status == RegistrationDraft.CONFIRMED

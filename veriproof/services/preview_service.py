"""브라우저 미리보기 URL과 보호된 바이트 제공 경계."""
from __future__ import annotations

from typing import Any


def watermark_preview_url(asset_id: Any) -> str:
    """사람용 공개 화면에는 워터마크 미리보기 URL만 만든다."""
    return f"/previews/{asset_id}/watermark"


def thumbnail_preview_url(asset_id: Any) -> str:
    """창작자 전용 라이브러리에서만 사용할 비공개 썸네일 URL이다."""
    return f"/previews/{asset_id}/thumbnail"

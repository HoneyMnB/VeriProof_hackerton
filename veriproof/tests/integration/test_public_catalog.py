"""Marketplace discovery exposes only creator-approved, anchored works."""
from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_catalog_excludes_private_and_unanchored_assets(client):
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory()
    public = IpAssetFactory(
        creator=creator,
        title="Public work",
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
    )
    IpAssetFactory(creator=creator, visibility=IpAsset.PRIVATE, status=IpAsset.ANCHORED)
    IpAssetFactory(creator=creator, visibility=IpAsset.PUBLIC, status=IpAsset.DRAFT)

    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["asset_id"] for item in items] == [str(public.id)]
    assert "original_url" not in items[0]
    assert "thumbnail_url" not in items[0]
    assert "creator_wallet" not in items[0]
    assert "anchor_tx_sig" not in items[0]


@pytest.mark.django_db
def test_catalog_excludes_public_asset_without_registration_certificate(client):
    """공유 선택만으로는 공개 카탈로그에 게시될 수 없다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(),
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
        registration_certificate_tx_sig=None,
    )

    response = client.get("/api/v1/catalog")

    assert response.status_code == 200
    assert str(asset.id) not in [item["asset_id"] for item in response.json()["items"]]


@pytest.mark.django_db
def test_discovery_home_renders_public_work(client):
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(),
        title="Discoverable work",
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
    )
    response = client.get("/discover")
    assert response.status_code == 200
    assert asset.title in response.content.decode()


@pytest.mark.django_db
def test_discover_language_selection_is_kept_on_asset_detail(client):
    """언어 스위처 쿠키는 로그인 사용자의 상세 페이지 SSR에도 적용된다."""
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    user = User.objects.create_user("buyer@example.com", "buyer@example.com", "safe-password-123")
    UserPreference.objects.filter(user=user).update(language="ko")
    asset = IpAssetFactory(
        creator=CreatorFactory(), visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED
    )
    client.force_login(user)
    client.cookies["veriproof_lang"] = "en"

    discover = client.get("/discover")
    detail = client.get(f"/discover/{asset.id}")

    assert 'window.__VP_LANG__ = "en"' in discover.content.decode()
    assert 'window.__VP_LANG__ = "en"' in detail.content.decode()


@pytest.mark.django_db
def test_discovery_does_not_expose_machine_discovery_controls(client):
    response = client.get("/discover")
    content = response.content.decode()

    assert response.status_code == 200
    assert response["Link"] == '</.well-known/ai-plugin.json>; rel="service-desc"; type="application/json"'
    assert '<link rel="service-desc" type="application/json" href="/.well-known/ai-plugin.json">' in content
    assert '<a href="/.well-known/ai-plugin.json"' not in content
    assert 'href="/api/v1/openapi.json"' not in content
    assert "Agent manifest" not in content
    assert "For agents" not in content


def test_machine_manifest_contains_the_agent_workflow_without_a_human_docs_page(client):
    manifest = client.get("/.well-known/ai-plugin.json")

    assert manifest.status_code == 200
    payload = manifest.json()
    assert "X-Agent-Protocol: x402" in payload["description_for_model"]
    assert "POST /api/v1/ip/{asset_id}/settle" in payload["description_for_model"]
    assert payload["api"]["url"].endswith("/api/v1/openapi.json")
    assert client.get("/developers").status_code == 404


@pytest.mark.django_db
def test_discovery_searches_creator_tags_and_preserves_work_type(client):
    """공개 탐색은 분석·창작자가 저장한 태그를 실제 검색에 사용한다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(),
        asset_type=IpAsset.PRODUCT,
        title="Handmade object",
        tags=["brass", "desk"],
        visibility=IpAsset.PUBLIC,
        status=IpAsset.ANCHORED,
    )

    response = client.get("/discover", {"q": "brass", "asset_type": IpAsset.PRODUCT})
    content = response.content.decode()

    assert response.status_code == 200
    assert asset.title in content
    assert '<option value="product" selected' in content


@pytest.mark.django_db
def test_public_detail_only_renders_watermarked_preview(client):
    """사람용 상세 화면은 원본·비워터마크 썸네일 대신 결제 경로만 보여준다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(), visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED
    )
    response = client.get(f"/discover/{asset.id}")
    content = response.content.decode()
    assert response.status_code == 200
    assert f"/previews/{asset.id}/watermark" in content
    assert "thumbnail" not in content
    assert f"/api/v1/ip/{asset.id}/settle" in content


@pytest.mark.django_db
def test_public_detail_renders_gallery_previews_for_one_work(client, monkeypatch):
    """다중 이미지도 동일 작품 상세에서 워터마크 미리보기만 전환할 수 있다."""
    from apps.ip.models import AssetImage, IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeStorageService

    asset = IpAssetFactory(
        creator=CreatorFactory(), visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED
    )
    image = AssetImage.objects.create(
        asset=asset,
        position=1,
        file_name="detail.png",
        content_mime_type="image/png",
        content_sha256="a" * 64,
        watermark_url="memory://watermark/detail",
        original_url="memory://original/detail",
    )
    storage = FakeStorageService()
    storage.permanent[("watermark", image.id)] = b"gallery-watermark"
    monkeypatch.setattr("apps.ip.views_web.get_storage_service", lambda: storage)

    response = client.get(f"/discover/{asset.id}")
    gallery_preview = client.get(f"/previews/{asset.id}/gallery/{image.id}")

    assert response.status_code == 200
    assert f"/previews/{asset.id}/gallery/{image.id}" in response.content.decode()
    assert "data-gallery-thumbnail" in response.content.decode()
    assert gallery_preview.status_code == 200
    assert gallery_preview.content == b"gallery-watermark"


@pytest.mark.django_db
def test_public_watermark_preview_never_serves_thumbnail_to_anonymous(client, monkeypatch):
    """공개 워터마크는 반환하지만 비워터마크 썸네일은 익명 요청에 숨긴다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeStorageService

    asset = IpAssetFactory(
        creator=CreatorFactory(), visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED
    )
    storage = FakeStorageService()
    storage.permanent[("watermark", asset.id)] = b"watermarked-preview"
    storage.permanent[("thumbnail", asset.id)] = b"unwatermarked-thumbnail"
    monkeypatch.setattr("apps.ip.views_web.get_storage_service", lambda: storage)

    watermark = client.get(f"/previews/{asset.id}/watermark")
    thumbnail = client.get(f"/previews/{asset.id}/thumbnail")
    assert watermark.status_code == 200
    assert watermark.content == b"watermarked-preview"
    assert thumbnail.status_code == 404

"""SPEC-001 integration tests — POST /api/v1/ip/register.

Drives the full view through Django's test client with the four external
services swapped to fakes via the ``get_*()`` factory seam in
``apps.ip.views_api`` (monkeypatched per-test). This keeps every test offline.

Covers the SPEC §5 integration list (AC-1..AC-10 + R7):
- happy path 201, persist fields, exclude original, MIME/size/wallet rejects,
  duplicate 409, Gemini degrade, anchor failure 202, default target price,
  ANCHORED event recorded.
"""
from __future__ import annotations

import datetime
import hashlib
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from freezegun import freeze_time

from tests.conftest import VALID_WALLET
from tests.fakes import FakeGeminiService, FakeSolanaService, FakeStorageService

REGISTER_URL = "/api/v1/ip/register"


# --- Helpers ----------------------------------------------------------------


def _upload(png_bytes: bytes, filename: str = "test.png", mime: str = "image/png"):
    """Build a SimpleUploadedFile for the multipart ``image`` field."""
    return SimpleUploadedFile(filename, png_bytes, content_type=mime)


def _patch_services(
    monkeypatch,
    *,
    gemini: FakeGeminiService | None = None,
    solana: FakeSolanaService | None = None,
    storage: FakeStorageService | None = None,
):
    """테스트 전용 어댑터를 등록 유스케이스 경계에 주입한다."""
    from services.event_recorder import get_event_recorder
    from services.image_processor import get_image_processor
    from services.registration_service import RegistrationService

    service = RegistrationService(
        image_processor=get_image_processor(),
        gemini=gemini or FakeGeminiService(),
        solana=solana or FakeSolanaService(),
        storage=storage or FakeStorageService(),
        event_recorder=get_event_recorder(),
    )
    monkeypatch.setattr("apps.ip.views_api.get_registration_service", lambda: service)
    monkeypatch.setattr(
        "apps.ip.views_api.active_wallet_signer",
        lambda user: (VALID_WALLET, [7] * 64),
    )


def _post(client, image, **extra):
    """POST to /register with the given image upload + form fields."""
    _login_registrant(client)
    data = {"image": image, "creator_wallet": VALID_WALLET, "min_price": "1.5", "target_price": "2.25"}
    data.update(extra)
    return client.post(REGISTER_URL, data, format="multipart")


def _login_registrant(client) -> None:
    from django.contrib.auth.models import User

    user, _ = User.objects.get_or_create(username="registrant@test.com")
    client.force_login(user)


# --- Happy path --------------------------------------------------------------


@pytest.mark.django_db
def test_register_happy_path_returns_201_with_asset(client, png_bytes, monkeypatch):
    """AC-1: valid PNG + wallet + min_price -> 201, asset_id, anchor_tx, tags."""
    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    response = _post(client, _upload(png_bytes))
    assert response.status_code == 201
    body = response.json()
    assert "asset_id" in body
    assert body["anchor_tx"]  # non-empty
    assert body["analysis"]["tags"]  # non-empty (fake returns ["test"])
    assert body["x402_endpoint"].endswith(body["asset_id"])


@pytest.mark.django_db
def test_register_uses_the_authenticated_wallet_for_public_key_and_signing(client, png_bytes, monkeypatch, settings):
    """The request wallet is ignored; both anchor identity and signer come from the account."""
    from cryptography.fernet import Fernet
    from django.contrib.auth.models import User
    from solders.keypair import Keypair

    from apps.accounts.models import WalletConfiguration
    from apps.accounts.services import encrypt_wallet_private_address

    keypair = Keypair()
    settings.WALLET_PRIVATE_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode()
    user = User.objects.create_user(username="wallet-signer@test.com", password="test-password-123")
    WalletConfiguration.objects.create(
        user=user,
        label="Signing wallet",
        address=str(keypair.pubkey()),
        private_address=encrypt_wallet_private_address(str(keypair)),
        is_active=True,
    )
    client.force_login(user)
    solana = FakeSolanaService()
    from apps.ip.views_api import active_wallet_signer as real_active_wallet_signer

    _patch_services(monkeypatch, gemini=FakeGeminiService(), solana=solana, storage=FakeStorageService())
    monkeypatch.setattr("apps.ip.views_api.active_wallet_signer", real_active_wallet_signer)

    response = client.post(
        REGISTER_URL,
        {"image": _upload(png_bytes), "creator_wallet": VALID_WALLET, "min_price": "1.5", "target_price": "2.25"},
        format="multipart",
    )

    assert response.status_code == 201, response.content
    anchor_call = next(call for call in solana.calls if call[0] == "anchor_hash")
    assert anchor_call[1][1] == str(keypair.pubkey())
    assert anchor_call[1][2] == list(bytes(keypair))


@pytest.mark.django_db
def test_register_rejects_an_account_without_an_active_wallet(client, png_bytes, monkeypatch):
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="no-wallet@test.com", password="test-password-123")
    client.force_login(user)
    monkeypatch.setattr(
        "apps.ip.views_api.get_registration_service",
        lambda: pytest.fail("registration dependencies must not run without a wallet"),
    )

    response = client.post(
        REGISTER_URL,
        {"image": _upload(png_bytes), "creator_wallet": VALID_WALLET, "min_price": "1.5", "target_price": "2.25"},
        format="multipart",
    )

    assert response.status_code == 422
    assert response.json()["error"] == "wallet_signing_unavailable"


@pytest.mark.django_db
def test_register_normalizes_public_visibility_from_multipart(client, png_bytes, monkeypatch):
    """대소문자가 다른 폼 값도 사용자가 요청한 공개 상태로 보존한다."""
    from apps.ip.models import IpAsset

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    response = _post(client, _upload(png_bytes), visibility="PUBLIC")

    assert response.status_code == 201
    assert response.json()["visibility"] == IpAsset.PUBLIC
    assert IpAsset.objects.get(id=response.json()["asset_id"]).visibility == IpAsset.PUBLIC


@pytest.mark.django_db
def test_register_persists_asset_fields(client, png_bytes, monkeypatch):
    """AC-2: DB row has 64-char sha256 + thumbnail/watermark URLs + expiry."""
    from django.contrib.auth.models import User

    from apps.ip.models import IpAsset

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    with freeze_time("2026-03-01T00:00:00Z"):
        body = _post(client, _upload(png_bytes)).json()

    asset = IpAsset.objects.get(id=body["asset_id"])
    assert asset.account_owner == User.objects.get(username="registrant@test.com")
    assert len(asset.image_sha256) == 64
    assert asset.thumbnail_url
    assert asset.watermark_url
    assert asset.original_expires_at is not None
    # Retention is ORIGINAL_RETENTION_DAYS from now (frozen).
    assert asset.original_expires_at == datetime.datetime(
        2026, 3, 8, 0, 0, 0, tzinfo=datetime.UTC
    )


@pytest.mark.django_db
def test_registers_multiple_images_as_one_certified_work(client, png_bytes, rgba_png_bytes, monkeypatch):
    """추가 이미지는 별도 작품이 아니라 하나의 작품 매니페스트·증명에 포함된다."""
    from apps.ip.models import AssetImage, IpAsset
    from tests.fakes import FakeSolanaService

    solana = FakeSolanaService()
    _patch_services(monkeypatch, gemini=FakeGeminiService(), solana=solana, storage=FakeStorageService())
    response = _post(
        client,
        _upload(png_bytes, "cover.png"),
        gallery_images=[_upload(rgba_png_bytes, "detail.png")],
    )

    assert response.status_code == 201, response.content
    asset = IpAsset.objects.get(id=response.json()["asset_id"])
    gallery_image = AssetImage.objects.get(asset=asset)
    primary_hash = hashlib.sha256(png_bytes).hexdigest()
    detail_hash = hashlib.sha256(rgba_png_bytes).hexdigest()
    expected_manifest_hash = hashlib.sha256(f"{primary_hash}\n{detail_hash}".encode("ascii")).hexdigest()
    assert asset.image_sha256 == expected_manifest_hash
    assert gallery_image.position == 1
    assert gallery_image.content_sha256 == detail_hash
    assert len([call for call in solana.calls if call[0] == "anchor_hash"]) == 1
    assert len([call for call in solana.calls if call[0] == "issue_registration_certificate"]) == 1


@pytest.mark.django_db
def test_register_response_excludes_original(client, png_bytes, monkeypatch):
    """AC-3 / R15: response body has NO original bytes or original_url."""
    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    body = _post(client, _upload(png_bytes)).json()

    assert "original_url" not in body
    assert "original_bytes" not in body
    # Only the watermark is exposed as preview.
    assert "watermark_url" in body
    # The original PNG bytes must not appear anywhere in the serialized body.
    assert png_bytes.decode("latin1", errors="ignore") not in str(body)


@pytest.mark.django_db
def test_register_document_gets_ai_tags_and_description(client, monkeypatch):
    """비이미지(PDF) 자산도 등록 시 멀티모달 분석으로 ai_tags/ai_description을 얻고,
    사용자 태그/설명과 별개로 저장된다(에이전트 검색용)."""
    from apps.ip.models import IpAsset

    storage = FakeStorageService()
    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=storage,
    )
    upload = _upload(b"%PDF-1.4 minimal", filename="brief.pdf", mime="application/pdf")
    response = _post(client, upload, asset_type="document", tags="user-a, user-b", description="my brief")
    assert response.status_code == 201, response.content
    asset = IpAsset.objects.get(id=response.json()["asset_id"])
    assert asset.asset_type == "document"
    # 사용자 입력과 AI 산출이 각각 보존된다.
    assert asset.tags == ["user-a", "user-b"]
    assert asset.description == "my brief"
    assert asset.ai_tags == ["test"]
    assert asset.ai_description == "a test asset"
    temporary_calls = [call for call in storage.calls if call[0] == "save_temporary"]
    assert temporary_calls[0][1][3] == "application/pdf"


def test_confirmed_draft_metadata_preserves_creator_tags():
    """초안 확정 경로도 사용자가 입력한 태그를 등록 메타데이터로 전달한다."""
    from apps.ip.views_api import _metadata_from_draft

    upload = _upload(b"%PDF-1.4 minimal", filename="brief.pdf", mime="application/pdf")
    metadata = _metadata_from_draft(
        VALID_WALLET,
        {
            "asset_type": "document",
            "title": "Brief",
            "description": "A detailed licensing brief.",
            "tags": "brief, editorial",
            "min_price": "1.00",
            "target_price": "3.00",
            "visibility": "private",
        },
        upload,
    )
    assert metadata.tags == ("brief", "editorial")


@pytest.mark.django_db
def test_register_software_zip_skips_ai_analysis(client, monkeypatch):
    """LLM 분석 불가 형식(zip)은 AI 분석을 건너뛰고 ai 필드를 비운다."""
    from apps.ip.models import IpAsset

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )
    upload = _upload(b"PK\x03\x04zipdata", filename="app.zip", mime="application/zip")
    response = _post(client, upload, asset_type="software", tags="tool")
    assert response.status_code == 201, response.content
    asset = IpAsset.objects.get(id=response.json()["asset_id"])
    assert asset.ai_tags == []
    assert asset.ai_description is None


# --- Validation rejections ---------------------------------------------------


@pytest.mark.django_db
def test_register_rejects_non_image_mime(client, monkeypatch):
    """AC-4: GIF upload -> 415 (R8)."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    # Build a tiny GIF-styled payload; only the content_type matters here.
    response = _post(client, _upload(b"GIF89a...", filename="t.gif", mime="image/gif"))
    assert response.status_code == 415


@pytest.mark.django_db
def test_register_rejects_oversize(client, png_bytes, monkeypatch, settings):
    """AC-5: file larger than MAX_UPLOAD_BYTES -> 413 (R9)."""
    settings.MAX_UPLOAD_BYTES = 8  # png_bytes is much larger than 8 bytes
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    response = _post(client, _upload(png_bytes))
    assert response.status_code == 413


@pytest.mark.django_db
def test_register_rejects_invalid_wallet(client, png_bytes, monkeypatch):
    """AC-6: invalid wallet string -> 400 (R10)."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    _login_registrant(client)
    bad = SimpleUploadedFile("t.png", png_bytes, content_type="image/png")
    response = client.post(
        REGISTER_URL,
        {"image": bad, "creator_wallet": "not-a-valid-wallet!!", "min_price": "1.5"},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_register_rejects_negative_min_price(client, png_bytes, monkeypatch):
    """R11: min_price < 0 -> 400."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    _login_registrant(client)
    bad = SimpleUploadedFile("t.png", png_bytes, content_type="image/png")
    response = client.post(
        REGISTER_URL,
        {"image": bad, "creator_wallet": VALID_WALLET, "min_price": "-1"},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_register_rejects_non_finite_min_price(client, png_bytes, monkeypatch, amount):
    """Non-finite Decimal inputs are not valid monetary values."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    response = _post(client, _upload(png_bytes), min_price=amount)
    assert response.status_code == 400


@pytest.mark.django_db
def test_register_rejects_missing_image(client, monkeypatch):
    """No image file at all -> 400."""
    _login_registrant(client)
    response = client.post(
        REGISTER_URL,
        {"creator_wallet": VALID_WALLET, "min_price": "1.5"},
        format="multipart",
    )
    assert response.status_code == 400


# --- Duplicate / lineage -----------------------------------------------------


@pytest.mark.django_db
def test_register_duplicate_hash_returns_409(client, png_bytes, monkeypatch):
    """AC-7: re-registering the same image bytes -> 409 + existing asset_id."""
    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    first = _post(client, _upload(png_bytes))
    assert first.status_code == 201

    second = _post(client, _upload(png_bytes))
    assert second.status_code == 409
    assert second.json()["error"] == "duplicate"


@pytest.mark.django_db
def test_register_rejects_unknown_parent(client, png_bytes, monkeypatch):
    """parent_asset_id that does not exist -> 404 (SPEC-008 preview)."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    bogus_id = str(uuid.uuid4())
    response = _post(
        client, _upload(png_bytes), parent_asset_id=bogus_id, royalty_share_bps="3000"
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_register_rejects_invalid_royalty_share(client, png_bytes, monkeypatch):
    """parent_asset_id valid but royalty_share_bps out of range -> 400."""
    from tests.factories import CreatorFactory, IpAssetFactory

    parent = IpAssetFactory(creator=CreatorFactory())
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached

    response = _post(
        client,
        _upload(png_bytes),
        parent_asset_id=str(parent.id),
        royalty_share_bps="50000",  # > 10000
    )
    assert response.status_code == 400


# --- Degradation paths -------------------------------------------------------


@pytest.mark.django_db
def test_register_gemini_failure_is_reported_without_invented_analysis(client, png_bytes, monkeypatch):
    """Gemini 실패 시 임의 분석값이나 등록 성공을 반환하지 않는다."""
    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(fail_analyze=True),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    response = _post(client, _upload(png_bytes))
    assert response.status_code == 503
    assert response.json()["error"] == "analysis_unavailable"


@pytest.mark.django_db
def test_register_anchor_failure_is_reported_without_draft_success(client, png_bytes, monkeypatch):
    """앵커 실패 시 가짜 draft 성공 자산을 만들지 않는다."""
    from apps.ip.models import IpAsset

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(fail_anchor=True),
        storage=FakeStorageService(),
    )

    response = _post(client, _upload(png_bytes))
    assert response.status_code == 503
    assert response.json()["error"] == "anchor_unavailable"
    assert IpAsset.objects.count() == 0


# --- Defaults ----------------------------------------------------------------


@pytest.mark.django_db
def test_register_requires_explicit_target_price(client, png_bytes, monkeypatch):
    """가격 조건은 룰 기반 기본값 대신 창작자가 명시한다."""

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )
    _login_registrant(client)

    response = client.post(
        REGISTER_URL,
        {"image": _upload(png_bytes), "creator_wallet": VALID_WALLET, "min_price": "2.00"},
        format="multipart",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target_price"


@pytest.mark.django_db
def test_register_persists_creator_selected_target_price(client, png_bytes, monkeypatch):
    """SPEC-005 R3: the workspace target-price slider reaches the persisted asset."""
    import decimal as _decimal

    from apps.ip.models import IpAsset

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    body = _post(
        client, _upload(png_bytes), min_price="2.00", target_price="4.25"
    ).json()
    asset = IpAsset.objects.get(id=body["asset_id"])
    assert asset.target_price_usdc is None
    assert asset.target_price_sol == _decimal.Decimal("4.250000000")


# --- Event recording ---------------------------------------------------------


@pytest.mark.django_db
def test_register_records_anchored_event(client, png_bytes, monkeypatch):
    """R7: an ANCHORED AgentEvent is recorded for the new asset."""
    from apps.common.models import AgentEvent

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )

    body = _post(client, _upload(png_bytes)).json()

    events = AgentEvent.objects.filter(asset_id=body["asset_id"], type="ANCHORED")
    assert events.count() == 1


# --- Corrupt image -----------------------------------------------------------


@pytest.mark.django_db
def test_register_rejects_undecodable_image(client, corrupt_bytes, monkeypatch):
    """SPEC-001 §6: corrupt image -> 400."""
    _patch_services(monkeypatch, gemini=FakeGeminiService())  # not reached
    response = _post(client, _upload(corrupt_bytes))
    assert response.status_code == 400

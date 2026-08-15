"""SPEC-005 integration tests — library dashboard web + data APIs.

Covers the SPEC §5 backend integration list through Django's test client with
real SQLite DB + factories:

- ``/library`` web view renders ONLY the owner's assets (R5, AC-4).
- ``GET /api/v1/assets?creator=`` filters by creator wallet (R11).
- ``GET /api/v1/ip/{asset_id}/transactions`` merges Licenses + AgentEvents,
  time-ascending, excludes original bytes/urls (R9, AC-8).
- ``GET /api/v1/events?asset_id=&since=`` incremental polling fallback (R10,
  AC-9; shared with SPEC-006).
- ``test_dragdrop_register_response_renders_card`` — frontend contract: the
  register response shape feeds ``analysis_card_fields`` (R1/R2, AC-1/AC-2).

Every payload the frontend consumes MUST exclude original bytes/urls.
"""
from __future__ import annotations

import base64
import decimal
import uuid
from types import SimpleNamespace

import pytest
from freezegun import freeze_time

_OWNER_WALLET = "OwnerWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_OTHER_WALLET = "OtherWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_BUYER_WALLET = "BuyerWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _login_creator(client, wallet: str):
    """자산 API는 세션 계정과 저장된 창작자 지갑이 일치해야 한다."""
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference

    user = User.objects.create_user(
        username=f"{wallet[:12]}@test.com", password="test-password-123"
    )
    preference, _ = UserPreference.objects.get_or_create(user=user)
    preference.creator_wallet = wallet
    preference.save(update_fields=["creator_wallet", "updated_at"])
    client.force_login(user)
    return user


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


# --- R5 / AC-4: /library renders only owner assets --------------------------


@pytest.mark.django_db
def test_library_view_renders_only_authenticated_account_assets(client):
    """The library uses the signed-in account, never a URL-selected wallet."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    other = CreatorFactory(wallet_address=_OTHER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    IpAssetFactory(creator=owner, account_owner=user, title="Owner Asset Alpha")
    IpAssetFactory(creator=owner, account_owner=user, title="Owner Asset Beta")
    IpAssetFactory(creator=other, title="Other Creator Asset")  # must NOT appear

    response = client.get("/library", {"creator": _OTHER_WALLET})
    assert response.status_code == 200
    content = response.content.decode()
    assert "Owner Asset Alpha" in content
    assert "Owner Asset Beta" in content
    assert "Other Creator Asset" not in content


@pytest.mark.django_db
def test_library_view_empty_state_when_no_assets(client):
    """Edge (6): a creator with zero assets sees an empty-state message."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=_OWNER_WALLET)
    _login_creator(client, _OWNER_WALLET)
    response = client.get("/library")
    assert response.status_code == 200
    # Empty-state hint rendered (template includes the marker).
    assert "no-assets" in response.content.decode()


@pytest.mark.django_db
def test_library_view_ignores_wallet_alias_param(client):
    """The legacy wallet parameter cannot select another account's assets."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    other = CreatorFactory(wallet_address=_OTHER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    IpAssetFactory(creator=owner, account_owner=user, title="Account Asset")
    IpAssetFactory(creator=other, title="URL-selected asset")
    response = client.get("/library", {"wallet": _OTHER_WALLET})
    assert response.status_code == 200
    content = response.content.decode()
    assert "Account Asset" in content
    assert "URL-selected asset" not in content


@pytest.mark.django_db
def test_library_view_requires_login(client):
    """A private account library cannot be opened anonymously."""
    response = client.get("/library")
    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_owner_can_download_registration_certificate_pdf(client):
    """PDF는 소유자의 실제 등록 지문과 온체인 증명에서만 생성된다."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    asset = IpAssetFactory(
        creator=owner,
        account_owner=user,
        anchor_tx_sig="anchor_transaction_123",
        registration_certificate_tx_sig="registration_transaction_123",
    )

    protected = client.get(f"/library/{asset.id}/certificate.pdf")
    assert protected.status_code == 403

    verified = client.post(
        f"/library/{asset.id}/certificate/auth/password",
        data={"password": "test-password-123"},
        content_type="application/json",
    )
    assert verified.status_code == 200
    assert verified.json()["certificate"]["asset_id"] == str(asset.id)

    response = client.get(f"/library/{asset.id}/certificate.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].endswith(f"{asset.id}.pdf\"")
    assert response.content.startswith(b"%PDF")
    assert b"original_url" not in response.content


@pytest.mark.django_db
def test_certificate_password_fallback_rejects_invalid_password(client):
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    asset = IpAssetFactory(creator=owner, account_owner=user)

    response = client.post(
        f"/library/{asset.id}/certificate/auth/password",
        data={"password": "wrong-password"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert client.get(f"/library/{asset.id}/certificate.pdf").status_code == 403


@pytest.mark.django_db
def test_certificate_access_grant_is_asset_scoped_and_expires(client):
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    first = IpAssetFactory(
        creator=owner,
        account_owner=user,
        anchor_tx_sig="anchor_first",
        registration_certificate_tx_sig="certificate_first",
    )
    second = IpAssetFactory(
        creator=owner,
        account_owner=user,
        anchor_tx_sig="anchor_second",
        registration_certificate_tx_sig="certificate_second",
    )

    with freeze_time("2026-08-15 10:00:00"):
        verified = client.post(
            f"/library/{first.id}/certificate/auth/password",
            data={"password": "test-password-123"},
            content_type="application/json",
        )
        assert verified.status_code == 200
        assert client.get(f"/library/{first.id}/certificate.pdf").status_code == 200
        assert client.get(f"/library/{second.id}/certificate.pdf").status_code == 403

    with freeze_time("2026-08-15 10:06:00"):
        assert client.get(f"/library/{first.id}/certificate.pdf").status_code == 403


@pytest.mark.django_db
def test_certificate_passkey_step_up_preserves_current_session(client, monkeypatch):
    from apps.accounts.models import PasskeyCredential
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    credential = PasskeyCredential.objects.create(
        user=user,
        user_handle=b"u" * 64,
        credential_id=b"certificate-credential",
        public_key=b"public-key",
        sign_count=2,
    )
    asset = IpAssetFactory(
        creator=owner,
        account_owner=user,
        anchor_tx_sig="anchor_transaction_456",
        registration_certificate_tx_sig="registration_transaction_456",
    )

    password = client.post(
        f"/library/{asset.id}/certificate/auth/password",
        data={"password": "test-password-123"},
        content_type="application/json",
    )
    assert password.status_code == 409
    assert password.json()["error"] == "passkey_required"

    options = client.post(f"/library/{asset.id}/certificate/auth/options")
    assert options.status_code == 200
    assert options.json()["userVerification"] == "required"
    assert options.json()["allowCredentials"][0]["id"] == _encoded(b"certificate-credential")

    monkeypatch.setattr(
        "apps.ip.views_web.verify_authentication",
        lambda *args, **kwargs: SimpleNamespace(new_sign_count=3),
    )
    verified = client.post(
        f"/library/{asset.id}/certificate/auth/passkey",
        data={
            "credential": {
                "id": _encoded(b"certificate-credential"),
                "response": {"userHandle": _encoded(b"u" * 64)},
            }
        },
        content_type="application/json",
    )
    assert verified.status_code == 200
    assert verified.json()["certificate"]["image_sha256"] == asset.image_sha256
    assert client.session["_auth_user_id"] == str(user.pk)
    credential.refresh_from_db()
    assert credential.sign_count == 3
    assert client.get(f"/library/{asset.id}/certificate.pdf").status_code == 200


@pytest.mark.django_db
def test_certificate_passkey_rejects_another_accounts_credential(client, monkeypatch):
    from django.contrib.auth.models import User
    from apps.accounts.models import PasskeyCredential
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    PasskeyCredential.objects.create(
        user=user, user_handle=b"u" * 64, credential_id=b"own-credential", public_key=b"own-key",
    )
    other = User.objects.create_user("other-passkey@test.com", password="other-password")
    PasskeyCredential.objects.create(
        user=other, user_handle=b"o" * 64, credential_id=b"other-credential", public_key=b"other-key",
    )
    asset = IpAssetFactory(creator=owner, account_owner=user)
    assert client.post(f"/library/{asset.id}/certificate/auth/options").status_code == 200

    monkeypatch.setattr(
        "apps.ip.views_web.verify_authentication",
        lambda *args, **kwargs: SimpleNamespace(new_sign_count=1),
    )
    response = client.post(
        f"/library/{asset.id}/certificate/auth/passkey",
        data={
            "credential": {
                "id": _encoded(b"other-credential"),
                "response": {"userHandle": _encoded(b"o" * 64)},
            }
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    assert client.session["_auth_user_id"] == str(user.pk)


@pytest.mark.django_db
def test_library_does_not_embed_protected_certificate_payload(client):
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    IpAssetFactory(
        creator=owner,
        account_owner=user,
        image_sha256="protected-fingerprint-value",
        anchor_tx_sig="protected-anchor-value",
        registration_certificate_tx_sig="protected-certificate-value",
    )

    content = client.get("/library").content.decode()
    assert 'data-image-sha256=' not in content
    assert 'data-anchor-tx-sig=' not in content
    assert 'data-certificate-tx-sig=' not in content


@pytest.mark.django_db
def test_registration_certificate_pdf_is_not_available_to_other_accounts(client):
    """다른 계정은 인증서 PDF를 내려받을 수 없다."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    asset = IpAssetFactory(creator=owner, anchor_tx_sig="anchor_transaction_123")
    _login_creator(client, _OTHER_WALLET)
    response = client.get(f"/library/{asset.id}/certificate.pdf")
    assert response.status_code == 404


@pytest.mark.django_db
def test_library_management_payload_contains_registered_metadata_and_real_sales(client):
    """관리 모달은 등록 시 메타데이터와 실제 License 판매만 받는다."""
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    asset = IpAssetFactory(
        creator=owner,
        account_owner=user,
        title="Registered work",
        description="Registration description",
        tags=["portrait", "oil"],
        min_price_sol=decimal.Decimal("1.250000000"),
        target_price_sol=decimal.Decimal("2.500000000"),
    )
    LicenseFactory(asset=asset, price_usdc=None, price_sol=decimal.Decimal("3.250000000"), payment_currency="SOL")

    response = client.get("/library")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Registration description" in content
    assert "data-manage=" in content
    assert 'data-min-price-sol="1.250000000"' in content
    assert 'data-target-price-sol="2.500000000"' in content
    assert "1.250000000 SOL" in content
    assert "sale_count&quot;: 1" in content
    assert "gross_sol&quot;: &quot;3.25" in content


@pytest.mark.django_db
def test_owner_can_update_work_metadata_and_listing_terms(client):
    """관리 저장은 소유 작품의 제목·설명·태그와 판매 조건을 원자적으로 갱신한다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    asset = IpAssetFactory(creator=owner, account_owner=user, visibility=IpAsset.PRIVATE)
    response = client.post(
        f"/api/v1/ip/{asset.id}/terms",
        data={
            "title": "Edited work",
            "description": "Updated creator description",
            "tags": ["editorial", "portrait"],
            "min_price_sol": "4.50",
            "target_price_sol": "8.00",
            "visibility": "private",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    asset.refresh_from_db()
    assert asset.title == "Edited work"
    assert asset.description == "Updated creator description"
    assert asset.tags == ["editorial", "portrait"]
    assert str(asset.min_price_sol) == "4.500000000"


@pytest.mark.django_db
def test_owner_metadata_update_keeps_existing_tags_when_terms_client_omits_them(client):
    """가격만 저장해도 기존 태그를 지우지 않는다."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    asset = IpAssetFactory(creator=owner, account_owner=user, tags=["keep-me"])
    response = client.post(
        f"/api/v1/ip/{asset.id}/terms",
        data={"min_price_sol": "2", "target_price_sol": "3", "visibility": "private"},
        content_type="application/json",
    )
    assert response.status_code == 200
    asset.refresh_from_db()
    assert asset.tags == ["keep-me"]


@pytest.mark.django_db
def test_workspace_view_renders(client):
    """``/`` 등록 캔버스는 가격 기본값과 허용 단위를 함께 렌더링한다."""
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    # The drop zone + register endpoint hook exist for JS wiring.
    assert "dropzone" in content
    assert "/api/v1/ip/register" in content
    assert "data-i18n carries" not in content
    assert 'id="composer-add-menu" class="vp-composer-menu" hidden' in content
    assert 'id="min-price" type="number" min="0" step="0.001" value="0.001"' in content
    assert 'id="target-price" type="number" min="0" step="0.001" value="0.001"' in content


def test_start_registration_opens_canvas_before_wallet_connection():
    """등록 시작은 지갑 연결 전에도 캔버스를 열고, 확정 때만 지갑을 요구한다."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "workspace.js"
    ).read_text()
    open_canvas = source.split("function openCanvas()", 1)[1].split(
        "function closeCanvas()", 1
    )[0]
    assert "if (!wallet())" not in open_canvas
    assert "canvas.hidden = false" in open_canvas
    assert "!menu.contains(event.target)" in source
    assert 'var DEFAULT_REGISTRATION_PRICE = "0.001";' in source
    assert source.count("DEFAULT_REGISTRATION_PRICE") == 3
    send_conversation = source.split("function sendConversation(text)", 1)[1].split(
        "function loadHistory()", 1
    )[0]
    assert "attachment_ids: sentAttachments.map" in send_conversation
    bind_chat = source.split("function bindChat()", 1)[1].split(
        "function bindAttachmentMenu()", 1
    )[0]
    assert "compositionstart" in bind_chat
    assert "compositionend" in bind_chat
    assert "event.isComposing" in bind_chat
    assert "window.setTimeout(function () { form.requestSubmit(); }, 0);" in bind_chat


def test_certificate_close_handles_clicks_on_its_svg_icon():
    """닫기 버튼의 SVG 경로를 눌러도 상위 data 속성을 찾아 모달을 닫는다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "static" / "js" / "library.js").read_text()
    certificate_wiring = source.split("function wireCertificateModal()", 1)[1].split("function fieldRow", 1)[0]
    assert 'closest("[data-modal-close]")' in certificate_wiring
    assert "hideModal();" in certificate_wiring


# --- R11: GET /api/v1/assets -------------------------------------------------


@pytest.mark.django_db
def test_assets_api_lists_only_authenticated_account_assets(client):
    """The API lists all and only assets owned by the signed-in account."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    other = CreatorFactory(wallet_address=_OTHER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    IpAssetFactory(creator=owner, account_owner=user, title="A1")
    IpAssetFactory(creator=owner, account_owner=user, title="A2")
    IpAssetFactory(creator=other, title="A3")

    response = client.get("/api/v1/assets", {"creator": _OTHER_WALLET})
    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    assert len(items) == 2
    titles = {it["title"] for it in items}
    assert titles == {"A1", "A2"}
    # R8 edge: no original bytes/url leaks into the listing payload.
    for item in items:
        assert "original_url" not in item
        assert "original_bytes" not in item
    # Proof / listing fields present.
    assert {"asset_id", "title", "status", "creator_wallet"}.issubset(items[0].keys())


@pytest.mark.django_db
def test_assets_api_empty_for_account_without_assets(client):
    """An account without assets receives an empty account-scoped list."""
    _login_creator(client, _OWNER_WALLET)
    response = client.get("/api/v1/assets")
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.django_db
def test_assets_api_rejects_anonymous_but_ignores_wallet_query(client):
    """개인 라이브러리 API는 로그인 계정만 사용한다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=_OWNER_WALLET)
    assert client.get("/api/v1/assets", {"creator": _OWNER_WALLET}).status_code == 401
    _login_creator(client, _OTHER_WALLET)
    assert client.get("/api/v1/assets", {"creator": _OWNER_WALLET}).status_code == 200


# --- R9 / AC-8: GET /api/v1/ip/{asset_id}/transactions ----------------------


@pytest.mark.django_db
def test_transactions_api_returns_licenses_and_events(client):
    """Transactions timeline merges Licenses + AgentEvents, time-ascending."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
        LicenseFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    # Interleave events and a license at distinct timestamps.
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402", payload={"buyer_hint": "b1"})
    with freeze_time("2026-01-01T10:00:05Z"):
        LicenseFactory(
            asset=asset,
            buyer_wallet=_BUYER_WALLET,
            price_usdc=decimal.Decimal("1.50"),
            usage_type="commercial",
        )
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="CERT_ISSUED", payload={"ok": True})

    response = client.get(f"/api/v1/ip/{asset.id}/transactions")
    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    assert len(items) == 3
    # Time-ascending ordering (R9).
    timestamps = [it["timestamp"] for it in items]
    assert timestamps == sorted(timestamps)
    # Both kinds present.
    kinds = {it["kind"] for it in items}
    assert kinds == {"license", "event"}
    # License entry carries the deal fields.
    lic = next(it for it in items if it["kind"] == "license")
    assert lic["buyer_wallet"] == _BUYER_WALLET
    assert lic["price_usdc"] == "1.500000"
    assert lic["usage_type"] == "commercial"
    # Event entry carries type + payload.
    ev = next(it for it in items if it["kind"] == "event" and it["type"] == "HTTP_402")
    assert ev["payload"] == {"buyer_hint": "b1"}
    # R8 edge: no original bytes/url leaks into the timeline payload.
    import json

    blob = json.dumps(body)
    assert "original_url" not in blob
    assert "original_bytes" not in blob


@pytest.mark.django_db
def test_transactions_api_empty_when_only_asset_exists(client):
    """An asset with no License/AgentEvent yields an empty timeline (200)."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    response = client.get(f"/api/v1/ip/{asset.id}/transactions")
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.django_db
def test_transactions_api_unknown_asset_404(client):
    """Unknown asset_id -> 404 (architecture 6.1: 200 | 404)."""
    unknown = uuid.uuid4()
    response = client.get(f"/api/v1/ip/{unknown}/transactions")
    assert response.status_code == 404


# --- R10 / AC-9: GET /api/v1/events?asset_id=&since= ------------------------


@pytest.mark.django_db
def test_events_polling_endpoint_returns_since(client):
    """``since`` boundary: only AgentEvents created strictly after ``since``."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402")
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="ACCEPT")

    # Boundary: since=10:00:05 -> only the 10:00:10 event qualifies (strict >).
    response = client.get(
        "/api/v1/events",
        {"asset_id": str(asset.id), "since": "2026-01-01T10:00:05+00:00"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "ACCEPT"


@pytest.mark.django_db
def test_events_polling_without_since_returns_all_for_asset(client):
    """Omitting ``since`` returns every AgentEvent for the asset."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402")
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="ACCEPT")

    response = client.get("/api/v1/events", {"asset_id": str(asset.id)})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    # Time-ascending (oldest first) for incremental rendering.
    assert items[0]["type"] == "HTTP_402"
    assert items[1]["type"] == "ACCEPT"


# --- R1 / R2 / AC-1 / AC-2: register response feeds analysis card -----------

# This is the offline contract test for the SPEC §5 frontend test
# ``test_dragdrop_triggers_register_fetch`` / ``test_render_analysis_card_from_response``:
# it exercises the real register endpoint with fakes and feeds the response into
# the Python mirror of the JS card renderer, proving the drag->register->render
# contract end-to-end without a browser.


@pytest.mark.django_db
def test_dragdrop_register_response_renders_card(client, png_bytes, monkeypatch):
    """A register response's JSON shape renders a full analysis card (offline)."""
    from apps.ip.dashboard import analysis_card_fields
    from tests.fakes import FakeGeminiService, FakeSolanaService, FakeStorageService
    from tests.integration.test_register import _patch_services, _upload, _post

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )
    response = _post(client, _upload(png_bytes))
    assert response.status_code == 201
    body = response.json()

    # The frontend pure mirror consumes the response contract directly.
    card = analysis_card_fields(body)
    assert card["asset_id"] == body["asset_id"]
    assert card["anchor_tx"] == body["anchor_tx"]
    assert card["x402_endpoint"] == f"/api/v1/ip/{body['asset_id']}"
    assert card["originality_score"] == 80  # FakeGeminiService default
    assert card["degraded"] is False
    assert isinstance(card["tags"], list)


# --- Certificate payload served via assets context (R8 / AC-7) --------------


@pytest.mark.django_db
def test_library_view_includes_certificate_payload_for_anchored_asset(client):
    """The library view injects per-asset certificate/Explorer data into context."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    user = _login_creator(client, _OWNER_WALLET)
    IpAssetFactory(
        creator=owner,
        account_owner=user,
        title="Anchored Asset",
        status=IpAsset.ANCHORED,
        anchor_tx_sig="anchor_sig_lib_001",
    )
    response = client.get("/library")
    assert response.status_code == 200
    content = response.content.decode()
    # Explorer URL for the anchored asset is rendered (R7 / AC-6 wired in page).
    assert "explorer.solana.com/tx/anchor_sig_lib_001" in content
